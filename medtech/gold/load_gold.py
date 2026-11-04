"""
load_gold.py -- Silver -> Gold warehouse (SQLite).

v12 changes (IDs refer to docs/ISSUE_LOG.md):
  N3/MFR-03  the 'Unknown' placeholder manufacturer never receives a country (it carried 'Italy')
  N5/DEV-05  an event with no usable brand AND no usable generic name creates the device '(not reported)', not 'UNKNOWN'
  DEV-04     standardize_device_descriptions(): a description is replaced by the FDA device name when it (a) is the same words in
             any order, (b) is the first word(s) of the brand name, (c) matches config/device_description_map.csv, besides the v11 rules
             (cut copy of the FDA name, product-code list, copy of the brand); a missing description becomes the FDA name; what
             remains is Title-Cased and given ONE spelling per description
  N6         brand / model keys join a number to its unit ('4 DEG' = '4DEG') -- see text_normalize.normalize_device_key()
v11: GUDID model-level fields only on catalog matches + gudid_match_level, short names for recall-only devices, no catalog number
     from a recall, recall_initiated_date_key, calendar ends at the last month with data, ISO calendar columns, whole-word recall matching.
"""
import re
import sqlite3
import calendar
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from rapidfuzz import process, fuzz

from medtech.common import ROOT_DIR, SILVER_DIR, GOLD_DIR, get_logger
from medtech.bronze.ingest_maude import PRODUCT_CODE_CATEGORY, START_DATE, END_DATE
from medtech.reference_data import (derive_parent_company, display_name, brand_display, problem_code_group, reason_group, short_recall_name,
                            token_key, smart_case, apply_description_rules)
from medtech.text_normalize import normalize_device_key, safe_str

log = get_logger("load_gold")
DB_PATH = GOLD_DIR / "medtech_warehouse.db"
SCHEMA_PATH = ROOT_DIR / "sql" / "schema.sql"

NOT_REPORTED = "(not reported)"
_mfr_cache, _device_cache = {}, {}
RECALL_DEVICE_MATCH_CUTOFF = 90
MIN_RECALL_NAME_LEN = 5
MAX_CATALOG_LEN = 60
RECALL_DATE_FIELDS_PRIORITY = ["event_date_posted", "event_date_created", "event_date_initiated", "event_date_terminated"]
BRAND_LEVEL_FIELDS = ("product_type", "brand_family")
MODEL_LEVEL_FIELDS = ("sku", "primary_di", "issuing_agency", "commercial_distribution_status", "is_kit", "is_single_use",
                      "is_sterile", "mri_safety")
METHOD_RANK = {"not_matched": 0, "brand_name_fuzzy": 1, "brand_name_exact": 2, "catalog_number": 3}
_stats = Counter()


def _category_for(product_code):
    return PRODUCT_CODE_CATEGORY.get(product_code, "Uncategorized")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA synchronous = OFF;")      # Gold is rebuildable from Silver / Bronze
    conn.execute("PRAGMA journal_mode = MEMORY;")
    return conn


def apply_schema(conn):
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    log.info("Schema applied to %s", DB_PATH)


# ------------------------------------------------------------------ calendar
def _parse_any_date(raw):
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    try:
        return datetime.strptime(str(raw)[:10].replace("-", ""), "%Y%m%d").date()
    except ValueError:
        return None


def _compute_date_bounds():
    """First data date .. LAST DAY OF THE LAST MONTH that holds data (not the run date)."""
    dates = []
    p = SILVER_DIR / "maude_silver.parquet"
    if p.exists():
        dates.extend(pd.read_parquet(p, columns=["date_received"])["date_received"].apply(_parse_any_date).dropna().tolist())
    p = SILVER_DIR / "recalls_silver.parquet"
    if p.exists():
        df = pd.read_parquet(p)
        for f in RECALL_DATE_FIELDS_PRIORITY:
            if f in df.columns:
                dates.extend(df[f].apply(_parse_any_date).dropna().tolist())
    if not dates:
        log.warning("No Silver data yet -- using the MAUDE window %s..%s for dim_date.", START_DATE, END_DATE)
        return START_DATE, END_DATE
    lo, last = min(dates), max(dates)
    hi = last.replace(day=calendar.monthrange(last.year, last.month)[1])
    log.info("dim_date bounds from Silver data: %s to %s (%s dates scanned; ends at the end of the last month with data)", lo, hi, len(dates))
    return lo, hi


def _date_key(v):
    if not v or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return int(datetime.strptime(str(v)[:10].replace("-", ""), "%Y%m%d").strftime("%Y%m%d"))
    except ValueError:
        return None


def _date_key_with_source(row, fields):
    for f in fields:
        dk = _date_key(row.get(f))
        if dk is not None:
            return dk, f
    return None, None


def _calendar_row(d):
    iso = d.isocalendar()
    return (int(d.strftime("%Y%m%d")), d.strftime("%Y-%m-%d"), d.year, (d.month - 1) // 3 + 1, d.month, d.strftime("%B"),
            int(iso[1]), int(iso[0]), d.strftime("%b %Y"), d.year * 100 + d.month)


_CAL_SQL = "INSERT OR IGNORE INTO dim_date VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"


def populate_full_calendar(conn, start=None, end=None):
    if start is None or end is None:
        s, e = _compute_date_bounds()
        start, end = start or s, end or e
    rows, d = [], start
    while d <= end:
        rows.append(_calendar_row(d))
        d += timedelta(days=1)
    conn.executemany(_CAL_SQL, rows)
    conn.commit()
    log.info("dim_date populated: %s consecutive days (%s to %s), zero gaps.", len(rows), start, end)


def _ensure_date_keys(conn, all_dates):
    rows = []
    for dk in {d for d in all_dates if d and not pd.isna(d)}:
        try:
            dt = datetime.strptime(str(int(dk)), "%Y%m%d").date()
        except (ValueError, TypeError):
            continue
        if not conn.execute("SELECT 1 FROM dim_date WHERE date_key = ?", (int(dk),)).fetchone():
            rows.append(_calendar_row(dt))
    if rows:
        log.error("GAP-RISK: %s date_key(s) fell outside the calendar and were added individually: %s", len(rows), sorted(r[0] for r in rows))
        conn.executemany(_CAL_SQL, rows)
        conn.commit()


def _clean(v):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else v


# ------------------------------------------------------------------ dimensions
def _get_or_create_mfr(conn, name, duns=None, country=None, country_source=None):
    placeholder = 0
    if not name or (isinstance(name, float) and pd.isna(name)):
        name, placeholder = "Unknown", 1
    if placeholder:
        country, country_source = None, None          # N3: the placeholder entity never gets a country
    if name in _mfr_cache:
        key = _mfr_cache[name]
        if country:
            _maybe_set_country(conn, key, country, country_source)
        return key
    row = conn.execute("SELECT mfr_key, duns_number, country FROM dim_manufacturer WHERE canonical_name = ?", (name,)).fetchone()
    if row:
        key, existing_duns, existing_country = row
        if not existing_duns and duns:
            conn.execute("UPDATE dim_manufacturer SET duns_number = ? WHERE mfr_key = ?", (duns, key))
        if not existing_country and country:
            _maybe_set_country(conn, key, country, country_source)
        conn.commit()
        _mfr_cache[name] = key
        return key
    cur = conn.execute(
        "INSERT INTO dim_manufacturer (canonical_name, display_name, parent_company, duns_number, country, country_source, "
        "is_placeholder_entity) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, display_name(name), derive_parent_company(name), _clean(duns), _clean(country),
         _clean(country_source) if country else None, placeholder))
    conn.commit()
    _mfr_cache[name] = cur.lastrowid
    return cur.lastrowid


def _maybe_set_country(conn, mfr_key, country, source):
    conn.execute("UPDATE dim_manufacturer SET country = ?, country_source = ? WHERE mfr_key = ? AND country IS NULL "
                 "AND COALESCE(is_placeholder_entity, 0) = 0", (country, source, mfr_key))
    conn.commit()


def _get_or_create_device(conn, product_code, brand_name, description, model=None, catalog_number=None):
    brand_norm, model_norm = normalize_device_key(brand_name), normalize_device_key(model)
    ck = (product_code, brand_norm, model_norm)
    if ck in _device_cache:
        return _device_cache[ck]
    row = conn.execute("SELECT device_key FROM dim_device WHERE product_code = ? AND brand_name_norm = ? AND model_norm = ?",
                       (product_code, brand_norm, model_norm)).fetchone()
    if row:
        _device_cache[ck] = row[0]
        return row[0]
    if catalog_number is not None and len(str(catalog_number)) > MAX_CATALOG_LEN:     # that is a list, not a catalog number
        _stats["catalog_dropped_too_long"] += 1
        catalog_number = None
    cur = conn.execute(
        "INSERT INTO dim_device (product_code, device_description, category, brand_name, brand_name_display, model, brand_name_norm, "
        "model_norm, catalog_number_observed, gudid_enriched, match_method, gudid_match_level) VALUES (?,?,?,?,?,?,?,?,?,0,'not_matched','None')",
        (product_code, _clean(description), _category_for(product_code), _clean(brand_name), brand_display(brand_name),
         _clean(model), brand_norm, model_norm, _clean(catalog_number)))
    conn.commit()
    _device_cache[ck] = cur.lastrowid
    return cur.lastrowid


_event_device_cache = None


def _load_event_devices(conn, product_code):
    global _event_device_cache
    if _event_device_cache is None:
        _event_device_cache = {}
    if product_code not in _event_device_cache:
        _event_device_cache[product_code] = conn.execute(
            "SELECT DISTINCT dd.device_key, dd.brand_name_norm, dd.brand_name FROM dim_device dd "
            "WHERE dd.product_code = ? AND EXISTS (SELECT 1 FROM fact_events fe WHERE fe.device_key = dd.device_key)",
            (product_code,)).fetchall()
    return _event_device_cache[product_code]


def _contains_whole(haystack, needle):
    return re.search(r"(?<![A-Z0-9])" + re.escape(needle) + r"(?![A-Z0-9])", haystack) is not None


def _resolve_recall_device(conn, product_code, recall_text, catalog_number=None):
    """Link a recall to a device that already has adverse events (whole-word match of a name >= 5 characters). Otherwise create a device
    named with the SHORT form of the recall text, with no catalog number; devices created here are not candidates for later recalls."""
    candidates = _load_event_devices(conn, product_code)
    recall_norm = normalize_device_key(recall_text)
    best, best_len = None, 0
    for device_key, brand_norm, _ in candidates:
        if brand_norm and len(brand_norm) >= MIN_RECALL_NAME_LEN and len(brand_norm) > best_len and _contains_whole(recall_norm, brand_norm):
            best, best_len = device_key, len(brand_norm)
    if best is not None:
        return best
    norms = [c[1] for c in candidates if c[1] and len(c[1]) >= MIN_RECALL_NAME_LEN]
    if norms:
        m = process.extractOne(recall_norm, norms, scorer=fuzz.token_set_ratio, score_cutoff=RECALL_DEVICE_MATCH_CUTOFF)
        if m:
            for device_key, brand_norm, _ in candidates:
                if brand_norm == m[0]:
                    return device_key
    short = short_recall_name(recall_text) or recall_text
    return _get_or_create_device(conn, product_code, short, short, catalog_number=None)


# ------------------------------------------------------------------ facts
def load_events(conn):
    path = SILVER_DIR / "maude_silver.parquet"
    if not path.exists():
        log.warning("No maude_silver.parquet found - skipping fact_events load")
        return
    df = pd.read_parquet(path)
    df["date_key"] = df["date_received"].apply(_date_key)
    df = df.sort_values(["date_key", "report_number"], kind="mergesort").reset_index(drop=True)
    _ensure_date_keys(conn, df["date_key"])
    inserted, skipped = 0, 0
    for r in df.to_dict("records"):
        if r["date_key"] is None or pd.isna(r["date_key"]):
            skipped += 1
            continue
        mfr_key = _get_or_create_mfr(conn, r.get("manufacturer_canonical") or r.get("manufacturer_name_raw"), duns=r.get("manufacturer_duns"),
                                     country=_clean(r.get("manufacturer_country")), country_source="maude_native")
        brand = _clean(r.get("brand_name")) or _clean(r.get("device_name")) or NOT_REPORTED
        device_key = _get_or_create_device(conn, r.get("product_code"), brand, _clean(r.get("device_name")),
                                           model=_clean(r.get("model")), catalog_number=_clean(r.get("catalog_number")))
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO fact_events (report_number, device_key, mfr_key, date_key, event_type, narrative_text, lot_number, "
                "lot_kind, patient_age, patient_age_years, patient_sex, patient_outcome, product_problem_codes, has_narrative, "
                "is_literature_report, literature_basis, is_probable_duplicate, is_possible_duplicate, duplicate_basis) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r.get("report_number"), device_key, mfr_key, int(r["date_key"]), _clean(r.get("event_type")), _clean(r.get("narrative")),
                 _clean(r.get("lot_number")), _clean(r.get("lot_kind")), _clean(r.get("patient_age")), _clean(r.get("patient_age_years")),
                 _clean(r.get("patient_sex")), _clean(r.get("patient_outcome")), _clean(r.get("product_problem_codes")),
                 int(bool(r.get("has_narrative"))), int(bool(r.get("is_literature_report"))), _clean(r.get("literature_basis")),
                 int(bool(r.get("is_probable_duplicate"))), int(bool(r.get("is_possible_duplicate"))), _clean(r.get("duplicate_basis"))))
            if cur.rowcount:
                event_key = cur.lastrowid
                for outcome in [o.strip() for o in (r.get("patient_outcome") or "").split(";") if o.strip()]:
                    conn.execute("INSERT INTO fact_event_outcomes (event_key, outcome) VALUES (?, ?)", (event_key, outcome))
                for problem in [p.strip() for p in (r.get("product_problem_codes") or "").split(";") if p.strip()]:
                    conn.execute("INSERT INTO fact_event_problem_codes (event_key, problem_code, problem_code_group) VALUES (?, ?, ?)",
                                 (event_key, problem, problem_code_group(problem)))
            inserted += 1
            if inserted % 500 == 0:
                log.info("  ... fact_events %s of %s rows loaded", inserted, len(df))
        except sqlite3.IntegrityError:
            continue
    conn.commit()
    log.info("fact_events loaded: %s rows (%s skipped)", inserted, skipped)


def load_recalls(conn):
    path = SILVER_DIR / "recalls_silver.parquet"
    if not path.exists():
        log.warning("No recalls_silver.parquet found - skipping fact_recalls load")
        return
    records = pd.read_parquet(path).to_dict("records")
    fields = [f for f in RECALL_DATE_FIELDS_PRIORITY if f in records[0]] if records else []
    all_keys = []
    for r in records:
        r["_dk"], r["_src"] = _date_key_with_source(r, fields)
        r["_init"] = _date_key(r.get("event_date_initiated"))
        all_keys.extend([r["_dk"], r["_init"]])
    _ensure_date_keys(conn, all_keys)
    inserted, skipped, src_counter = 0, 0, Counter()
    for r in records:
        if r["_dk"] is None:
            skipped += 1
            continue
        mfr_key = _get_or_create_mfr(conn, r.get("manufacturer_canonical") or r.get("manufacturer_name_raw"), duns=r.get("manufacturer_duns"),
                                     country=r.get("country"), country_source="recall_derived" if r.get("country_is_derived") else "recall_native")
        device_key = _resolve_recall_device(conn, r.get("product_code"), _clean(r.get("product_description")))
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO fact_recalls (recall_id, device_key, mfr_key, date_key, recall_date_source, recall_initiated_date_key, "
                "recall_status, reason, reason_group, action, country, state, city, product_description, distribution_scope, product_quantity, "
                "product_quantity_units, country_is_derived) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r.get("recall_id"), device_key, mfr_key, int(r["_dk"]), r["_src"], r["_init"], _clean(r.get("recall_status")), _clean(r.get("reason")),
                 reason_group(r.get("reason")), _clean(r.get("action")), _clean(r.get("country")), _clean(r.get("state")), _clean(r.get("city")),
                 _clean(r.get("product_description")), _clean(r.get("distribution_scope")), _clean(r.get("product_quantity")),
                 _clean(r.get("product_quantity_units")), int(bool(r.get("country_is_derived")))))
            if cur.rowcount:
                src_counter[r["_src"]] += 1
                conn.execute("INSERT OR IGNORE INTO recall_compliance_ref (recall_id, address, code_info, firm_fei_number, k_numbers, pma_numbers, "
                             "res_event_number) VALUES (?,?,?,?,?,?,?)",
                             (r.get("recall_id"), safe_str(r.get("address")), safe_str(r.get("code_info")), safe_str(r.get("firm_fei_number")),
                              safe_str(r.get("k_numbers")), safe_str(r.get("pma_numbers")), safe_str(r.get("res_event_number"))))
            inserted += 1
        except sqlite3.IntegrityError:
            continue
    conn.commit()
    log.info("fact_recalls loaded: %s rows (%s skipped). Recall date source: %s. Initiated date known for %s.", inserted, skipped,
             dict(src_counter), conn.execute("SELECT COUNT(*) FROM fact_recalls WHERE recall_initiated_date_key IS NOT NULL").fetchone()[0])
    n_rec = conn.execute("SELECT COUNT(*) FROM fact_recalls").fetchone()[0]
    n_link = conn.execute("SELECT COUNT(*) FROM fact_recalls fr WHERE EXISTS (SELECT 1 FROM fact_events fe WHERE fe.device_key = fr.device_key)").fetchone()[0]
    log.info("Recall->device linking (counted from the database): %s of %s recalls (%.0f%%) point to a device that also has adverse events; "
             "%s recall-only devices were created with short names; %s catalog values over %s characters were dropped.",
             n_link, n_rec, (100.0 * n_link / n_rec) if n_rec else 0, conn.execute(
                 "SELECT COUNT(*) FROM dim_device dd WHERE NOT EXISTS (SELECT 1 FROM fact_events fe WHERE fe.device_key = dd.device_key)").fetchone()[0],
             _stats["catalog_dropped_too_long"], MAX_CATALOG_LEN)


def load_classification(conn):
    path = SILVER_DIR / "classification_silver.parquet"
    if not path.exists():
        log.warning("No classification_silver.parquet found - skipping dim_classification load")
        return
    inserted = 0
    for r in pd.read_parquet(path).to_dict("records"):
        try:
            conn.execute(
                "INSERT OR REPLACE INTO dim_classification (product_code, device_class, device_class_label, device_name_fda, medical_specialty, "
                "medical_specialty_description, implant_flag, review_panel, regulation_number, summary_malfunction_reporting, definition) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (r.get("product_code"), _clean(r.get("device_class")), _clean(r.get("device_class_label")), _clean(r.get("device_name_fda")),
                 _clean(r.get("medical_specialty")), _clean(r.get("medical_specialty_description")), _clean(r.get("implant_flag")),
                 _clean(r.get("review_panel")), safe_str(r.get("regulation_number")), _clean(r.get("summary_malfunction_reporting")), _clean(r.get("definition"))))
            conn.execute("INSERT OR REPLACE INTO classification_compliance_ref (product_code, k_numbers_ref, registration_numbers_ref, fei_numbers_ref) "
                         "VALUES (?,?,?,?)", (r.get("product_code"), safe_str(r.get("k_numbers_ref")), safe_str(r.get("registration_numbers_ref")),
                                              safe_str(r.get("fei_numbers_ref"))))
            inserted += 1
        except sqlite3.Error as exc:
            log.warning("Could not load classification row for %s: %s", r.get("product_code"), exc)
    conn.commit()
    log.info("dim_classification loaded: %s rows", inserted)


# ------------------------------------------------------------------ GUDID enrichment
def enrich_devices_with_gudid(conn):
    """Tiered match (catalog_number > brand exact > brand fuzzy). Only a catalog_number match copies model-specific fields;
    gudid_match_level says whether the enrichment is Model- or Brand-level."""
    path = SILVER_DIR / "gudid_silver.parquet"
    if not path.exists():
        log.warning("No gudid_silver.parquet found - skipping GUDID enrichment")
        return
    gudid_df = pd.read_parquet(path)
    observed = conn.execute("SELECT device_key, product_code, brand_name_norm, catalog_number_observed FROM dim_device").fetchall()
    if gudid_df.empty or not observed:
        return
    by_catalog, by_brand = {}, defaultdict(list)
    for device_key, pc, brand_norm, catalog_obs in observed:
        if catalog_obs:
            by_catalog.setdefault((pc, normalize_device_key(catalog_obs)), device_key)
        by_brand[(pc, brand_norm)].append(device_key)
    norms_by_code = defaultdict(list)
    for (pc, bn) in by_brand:
        if bn:
            norms_by_code[pc].append(bn)
    fuzzy_cache, best, matched = {}, {}, Counter()
    for g in gudid_df.to_dict("records"):
        pc, sku_norm, brand_norm = g.get("product_code"), normalize_device_key(g.get("sku")), g.get("brand_name_norm")
        targets, method = [], "not_matched"
        if sku_norm and (pc, sku_norm) in by_catalog:
            targets, method = [by_catalog[(pc, sku_norm)]], "catalog_number"
        elif brand_norm and (pc, brand_norm) in by_brand:
            targets, method = by_brand[(pc, brand_norm)], "brand_name_exact"
        elif brand_norm and norms_by_code.get(pc):
            ck = (pc, brand_norm)
            if ck not in fuzzy_cache:
                cand = process.extractOne(brand_norm, norms_by_code[pc], scorer=fuzz.token_sort_ratio, score_cutoff=90)
                fuzzy_cache[ck] = cand[0] if cand else None
            if fuzzy_cache[ck]:
                targets, method = by_brand[(pc, fuzzy_cache[ck])], "brand_name_fuzzy"
        matched[method] += 1
        for dk in targets:
            b = best.setdefault(dk, {"rank": 0, "method": "not_matched", "brand": {}, "model": {}})
            if METHOD_RANK[method] > b["rank"]:
                b["rank"], b["method"] = METHOD_RANK[method], method
            for f in BRAND_LEVEL_FIELDS:
                if b["brand"].get(f) is None and _clean(g.get(f)) is not None:
                    b["brand"][f] = g.get(f)
            if method == "catalog_number":
                for f in MODEL_LEVEL_FIELDS:
                    if b["model"].get(f) is None and _clean(g.get(f)) is not None:
                        b["model"][f] = g.get(f)
    for dk, b in best.items():
        vals = {**b["brand"], **b["model"]}
        sets = ", ".join(f"{f} = COALESCE({f}, ?)" for f in vals)
        level = "Model" if b["method"] == "catalog_number" else "Brand"
        conn.execute("UPDATE dim_device SET " + (sets + ", " if sets else "") + "gudid_enriched = 1, match_method = ?, gudid_match_level = ? WHERE device_key = ?",
                     (*[v if isinstance(v, (int, float, str)) else str(v) for v in vals.values()], b["method"], level, dk))
    conn.commit()
    log.info("GUDID enrichment: %s GUDID records examined %s; devices enriched by best method: %s", len(gudid_df), dict(matched),
             dict(Counter(b["method"] for b in best.values())))
    for cat, n, e, m in conn.execute("SELECT category, COUNT(*), SUM(gudid_enriched), SUM(gudid_match_level = 'Model') FROM dim_device GROUP BY category"):
        log.info("  GUDID %-15s any enrichment %5s of %5s devices (%.1f%%) | model-level %5s (%.1f%%)", cat, e or 0, n,
                 100.0 * (e or 0) / n if n else 0, m or 0, 100.0 * (m or 0) / n if n else 0)


# ------------------------------------------------------------------ device labels (DEV-04)
_CODE_LIST_RX = re.compile(r"^[A-Z]{3}(?:,\s*[A-Z]{3})*$")


def _standard_description(desc, brand, name, product_code):
    """The FDA device name when `desc` is a variant of it; otherwise None. Rules: same text; cut copy; product-code list; copy of the
    brand; the SAME WORDS in any order; the first word(s) of the brand name; the hand-maintained config map."""
    up, nup = desc.upper(), name.upper()
    brand_up = (brand or "").strip().upper().rstrip(" ,;:")
    if up == nup or (len(desc) >= 20 and nup.startswith(up)) or _CODE_LIST_RX.match(desc):
        return name
    if brand_up and up == brand_up:
        return name
    if token_key(desc) == token_key(name):
        return name
    if brand_up and len(desc.split()) <= 2 and brand_up.startswith(up + " "):      # 'Optima' for 'OPTIMA COIL SYSTEM'
        return name
    return apply_description_rules(desc, product_code, name)


def standardize_device_descriptions(conn):
    """Adds dim_device.device_type_fda and cleans device_description (see _standard_description); a missing description becomes the FDA
    name; ALL-CAPS leftovers are Title-Cased; then ONE spelling per description (the most common mixed-case variant)."""
    fda = dict(conn.execute("SELECT product_code, device_name_fda FROM dim_classification WHERE device_name_fda IS NOT NULL"))
    conn.execute("UPDATE dim_device SET device_type_fda = (SELECT device_name_fda FROM dim_classification c WHERE c.product_code = dim_device.product_code)")
    rows = conn.execute("SELECT device_key, product_code, device_description, brand_name FROM dim_device").fetchall()
    new_desc, replaced, filled = {}, 0, 0
    for dk, pc, desc, brand in rows:
        name = fda.get(pc)
        if desc is None or not str(desc).strip():
            if name:
                new_desc[dk], filled = name, filled + 1
            continue
        d = re.sub(r"\s+", " ", desc).strip().rstrip(" ,;:")
        if name:
            std = _standard_description(d, brand, name, pc)
            if std:
                if d != std:
                    replaced += 1
                d = std
        new_desc[dk] = smart_case(d)
    groups = defaultdict(Counter)
    for d in new_desc.values():
        groups[d.upper()][d] += 1
    best = {}
    for key, variants in groups.items():
        mixed = [(c, v) for v, c in variants.items() if not v.isupper() and not v.islower()]
        best[key] = max(mixed)[1] if mixed else max((c, v) for v, c in variants.items())[1]
    changed = 0
    for dk, pc, desc, brand in rows:
        if dk not in new_desc:
            continue
        final = best[new_desc[dk].upper()]
        if final != desc:
            conn.execute("UPDATE dim_device SET device_description = ? WHERE device_key = ?", (final, dk))
            changed += 1
    conn.commit()
    own = conn.execute("SELECT COUNT(*) FROM dim_device WHERE device_description <> device_type_fda").fetchone()[0]
    top = conn.execute("SELECT device_description, COUNT(*) c FROM dim_device WHERE device_description <> device_type_fda GROUP BY 1 ORDER BY c DESC LIMIT 5").fetchall()
    log.info("device_description standardised: %s replaced by the FDA device name, %s missing descriptions filled, %s row(s) updated, "
             "%s spelling group(s) merged. %s device row(s) keep their own specific term (largest: %s); use device_type_fda to group them.",
             replaced, filled, changed, sum(1 for g in groups.values() if len(g) > 1), own, top)


def dedupe_duplicate_manufacturers(conn):
    """Safety net for an older database: merge manufacturer rows whose names differ only by case / spacing."""
    groups = defaultdict(list)
    for mfr_key, name in conn.execute("SELECT mfr_key, canonical_name FROM dim_manufacturer").fetchall():
        groups[re.sub(r"\s+", " ", (name or "").strip()).upper()].append((mfr_key, name))
    merged = 0
    for variants in groups.values():
        if len(variants) <= 1:
            continue
        variants.sort(key=lambda v: v[0])
        survivor = variants[0][0]
        for loser, _ in variants[1:]:
            conn.execute("UPDATE fact_events SET mfr_key = ? WHERE mfr_key = ?", (survivor, loser))
            conn.execute("UPDATE fact_recalls SET mfr_key = ? WHERE mfr_key = ?", (survivor, loser))
            row = conn.execute("SELECT country, duns_number, country_source FROM dim_manufacturer WHERE mfr_key = ?", (loser,)).fetchone()
            if row:
                conn.execute("UPDATE dim_manufacturer SET country = COALESCE(country, ?), country_source = COALESCE(country_source, ?), "
                             "duns_number = COALESCE(duns_number, ?) WHERE mfr_key = ?", (row[0], row[2], row[1], survivor))
            conn.execute("DELETE FROM dim_manufacturer WHERE mfr_key = ?", (loser,))
            merged += 1
    conn.commit()
    log.info("dedupe_duplicate_manufacturers: %s", f"merged {merged} case-duplicate row(s)." if merged else "no case-duplicate manufacturer rows found.")


def reset_caches():
    global _event_device_cache
    _mfr_cache.clear()
    _device_cache.clear()
    _event_device_cache = None
    _stats.clear()


def run():
    reset_caches()
    conn = get_connection()
    apply_schema(conn)
    populate_full_calendar(conn)
    load_events(conn)
    load_recalls(conn)
    load_classification(conn)
    enrich_devices_with_gudid(conn)
    standardize_device_descriptions(conn)
    dedupe_duplicate_manufacturers(conn)
    conn.close()
    log.info("Gold layer build complete: %s", DB_PATH)


if __name__ == "__main__":
    run()
