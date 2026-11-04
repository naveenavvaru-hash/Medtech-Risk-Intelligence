"""
quality_checks.py -- Bronze -> Silver cleaning and manufacturer entity resolution.

v12 changes (IDs refer to docs/ISSUE_LOG.md):
  EVT-05/N4  duplicate flags use the FULL text, not the first 200 characters: >= 95% similar = 'Same narrative' (probable),
             70-95% = 'Similar narrative' (possible), a text reference = probable, same lot only = possible. Below 70% is not
             flagged on the narrative. Nothing is ever removed.
  N2         the MAUDE country CODE standard is detected from the data (FIPS vs ISO) and every code is decoded to a country name
             here in Silver; a code that cannot be decided is left blank (the registry lookup fills it) - never a raw 'CH'.
  N5/DEV-05  the generic-name fallback no longer re-introduces a placeholder: a report with no usable brand AND no usable
             generic name gets device_name = None (load_gold labels it '(not reported)').
  EVT-01     the log lists the most frequent lost-character contexts that remain undecided.
v11: complete-month ingestion, quantity parser, punctuation-insensitive manufacturer matching, literature_basis, lot_kind ...
"""
import json
import re
from collections import Counter

import pandas as pd
from rapidfuzz import process, fuzz

from medtech.silver import narrative_clean
from medtech.common import BRONZE_DIR, SILVER_DIR, get_logger
from medtech.country_normalize import normalize_country, detect_code_standard, PLACEHOLDERS as COUNTRY_PLACEHOLDERS
from medtech.reference_data import derive_parent_company, lot_kind
from medtech.text_normalize import (normalize_key, normalize_device_key, is_placeholder, fix_mojibake, safe_str, null_if_placeholder)

log = get_logger("quality_checks")

_US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA",
    "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC", "PR", "VI", "GU", "AS", "MP",
}
_US_STATE_NAMES = [
    "ALABAMA", "ALASKA", "ARIZONA", "ARKANSAS", "CALIFORNIA", "COLORADO", "CONNECTICUT", "DELAWARE", "FLORIDA",
    "GEORGIA", "HAWAII", "IDAHO", "ILLINOIS", "INDIANA", "IOWA", "KANSAS", "KENTUCKY", "LOUISIANA", "MAINE",
    "MARYLAND", "MASSACHUSETTS", "MICHIGAN", "MINNESOTA", "MISSISSIPPI", "MISSOURI", "MONTANA", "NEBRASKA", "NEVADA",
    "NEW HAMPSHIRE", "NEW JERSEY", "NEW MEXICO", "NEW YORK", "NORTH CAROLINA", "NORTH DAKOTA", "OHIO", "OKLAHOMA",
    "OREGON", "PENNSYLVANIA", "RHODE ISLAND", "SOUTH CAROLINA", "SOUTH DAKOTA", "TENNESSEE", "TEXAS", "UTAH", "VERMONT",
    "VIRGINIA", "WASHINGTON", "WEST VIRGINIA", "WISCONSIN", "WYOMING", "PUERTO RICO",
]
_FOREIGN_WORDS = [
    "INTERNATIONAL", "FOREIGN", "OVERSEAS", "OUTSIDE THE US", "OUTSIDE THE U.S", "EUROPE", "ASIA", "AFRICA", "LATIN AMERICA",
    "SOUTH AMERICA", "MIDDLE EAST", "CANADA", "MEXICO", "GERMANY", "FRANCE", "ITALY", "SPAIN", "UNITED KINGDOM", "ENGLAND",
    "IRELAND", "NETHERLANDS", "BELGIUM", "SWITZERLAND", "AUSTRIA", "SWEDEN", "NORWAY", "DENMARK", "FINLAND", "POLAND",
    "PORTUGAL", "GREECE", "TURKEY", "ISRAEL", "JAPAN", "CHINA", "KOREA", "TAIWAN", "INDIA", "SINGAPORE", "MALAYSIA",
    "THAILAND", "AUSTRALIA", "NEW ZEALAND", "BRAZIL", "ARGENTINA", "CHILE", "COLOMBIA", "PERU", "SAUDI", "EMIRATES",
    "SOUTH AFRICA", "EGYPT", "RUSSIA", "UKRAINE", "CZECH", "HUNGARY",
]
_WORLDWIDE_RX = re.compile(r"\b(WORLDWIDE|WORLD WIDE|GLOBAL|ALL COUNTRIES)\b", re.I)
_FOREIGN_RX = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in _FOREIGN_WORDS) + r")\b", re.I)
_US_RX = re.compile(r"\b(?:US|U\.S\.A?\.?|USA|UNITED STATES|NATIONWIDE|DOMESTIC|ALL STATES|" +
                    "|".join(re.escape(w) for w in _US_STATE_NAMES) + r")\b", re.I)
_STATE_TOKEN_RX = re.compile(r"\b[A-Z]{2}\b")


def derive_distribution_scope(raw):
    """Worldwide / International / Domestic / Unknown (text with no recognisable place is 'Unknown', not 'Domestic')."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)) or not str(raw).strip():
        return None
    s = str(raw)
    if _WORLDWIDE_RX.search(s):
        return "Worldwide"
    if _FOREIGN_RX.search(s):
        return "International"
    if _US_RX.search(s):
        return "Domestic"
    tokens = set(_STATE_TOKEN_RX.findall(s))
    if len(tokens) >= 2 and tokens <= _US_STATE_CODES:
        return "Domestic"
    return "Unknown"


# ------------------------------------------------------------------ recalled units
_NUM = r"(\d[\d,]*)"
_TOTAL_BEFORE_RX = re.compile(_NUM + r"\s*(?:[A-Za-z]+\s+){0,2}total\b", re.I)          # '1,511 total implants'
_TOTAL_AFTER_RX = re.compile(r"\btotal(?:\s+of)?\s*[:=]?\s*" + _NUM, re.I)              # 'total: 600'
_LEADING_PAREN_RX = re.compile(r"^\s*" + _NUM + r"\s*\(")                               # '315 (172 in USA)'
_UNIT_QTY_RX = re.compile(_NUM + r"\s*(?:units?|ea\b|each|pcs?\b|pieces?|devices?|implants?|kits?|systems?|catheters?)", re.I)
_ANY_NUM_RX = re.compile(_NUM)


def parse_product_quantity_units(raw):
    """Number of units recalled: an explicit 'total' figure -> a leading number followed by a bracketed sub-count -> numbers next to a
    unit word (summed) -> a single bare number. Several unrelated numbers with no unit word stay blank (ambiguous)."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw)

    def to_int(x):
        try:
            return int(x.replace(",", ""))
        except ValueError:
            return None

    for rx in (_TOTAL_BEFORE_RX, _TOTAL_AFTER_RX, _LEADING_PAREN_RX):
        m = rx.search(s)
        if m and to_int(m.group(1)) is not None:
            return to_int(m.group(1))
    with_units = [v for v in (to_int(x) for x in _UNIT_QTY_RX.findall(s)) if v is not None]
    if with_units:
        return sum(with_units)
    nums = [v for v in (to_int(x) for x in _ANY_NUM_RX.findall(s)) if v is not None]
    return nums[0] if len(nums) == 1 else None


# ------------------------------------------------------------------ small helpers
def _clean_text_field(s):
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = re.sub(r"\s+", " ", fix_mojibake(str(s))).strip()
    return s or None


def _clean_manufacturer_name_raw(name):
    cleaned = _clean_text_field(name)
    if cleaned is None:
        return None
    return None if is_placeholder(normalize_key(cleaned)) else cleaned


MAUDE_PATIENT_OUTCOME_CODES = {"D": "Death", "L": "Life Threatening", "H": "Hospitalization", "S": "Disability",
                               "C": "Congenital Anomaly", "R": "Required Intervention", "O": "Other"}


def _format_duns(value):
    v = safe_str(value)
    if v is None:
        return None
    if v.endswith(".0"):
        v = v[:-2]
    digits = re.sub(r"\D", "", v)
    return digits.zfill(9) if digits else None


def parse_patient_age_years(raw):
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(YR|MO|WK|DA)?$", str(raw).strip().upper())
    if not m:
        return None
    value, unit = float(m.group(1)), (m.group(2) or "YR")
    return {"YR": round(value, 1), "MO": round(value / 12.0, 1), "WK": round(value / 52.0, 2), "DA": round(value / 365.0, 2)}[unit]


def split_dedup_sorted(raw, sep=";"):
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return []
    return sorted({p.strip() for p in str(raw).split(sep) if p.strip()})


def decode_patient_outcomes(raw):
    return sorted({MAUDE_PATIENT_OUTCOME_CODES.get(t.strip().upper(), t) for t in split_dedup_sorted(raw)})


def _normalize_sex(value):
    """'Prefer Not To Disclose' and placeholders are 'Unknown'."""
    s = safe_str(value)
    if s is None or s.strip().upper() in {"UNKNOWN", "UNK", "N/A", "NA", "PREFER NOT TO DISCLOSE", "NOT DISCLOSED", ""}:
        return "Unknown"
    return s.strip()


def _load_bronze(prefix):
    records = []
    for f in sorted(BRONZE_DIR.glob(f"{prefix}*.json")):
        with open(f, "r", encoding="utf-8") as fh:
            records.extend(json.load(fh))
    log.info("Loaded %s raw records for prefix '%s'", len(records), prefix)
    return records


# ------------------------------------------------------------------ manufacturer entity resolution
KNOWN_MANUFACTURER_ALIASES = {
    "st jude medical": "Abbott Medical", "st. jude medical": "Abbott Medical",
    "guidant corp-cpi": "Boston Scientific Corporation", "guidant cpi": "Boston Scientific Corporation",
    "cardiac pacemakers inc": "Boston Scientific Corporation",
    "guidant corporation acs": "Abbott Vascular", "guidant corp acs": "Abbott Vascular",
    "advanced cardiovascular systems": "Abbott Vascular",
}


def _apply_known_aliases(name):
    lower = name.lower()
    for legacy, current in KNOWN_MANUFACTURER_ALIASES.items():
        if legacy in lower:
            site = re.search(r"\(([^)]+)\)\s*$", name)
            return f"{current}{f' ({site.group(1)})' if site else ''}"
    return name


_CORPORATE_SUFFIXES = ["incorporated", "inc", "corporation", "corp", "company", "co", "llc", "llp", "lp", "pllc", "ltd",
                       "limited", "gmbh", "srl", "s.r.l", "sarl", "s.a", "sa", "bv", "b.v", "kg", "plc", "ag", "laboratories",
                       "labs", "holdings", "holding", "worldwide", "partners"]
_CORP_SUFFIX_RE = re.compile(r"\b(" + "|".join(re.escape(s) for s in _CORPORATE_SUFFIXES) + r")\.?\s*$")
_TRAILING_COUNTRY_CODE_RE = re.compile(r"\b[a-z]{2}\s*$")
_DBA_RE = re.compile(r"\(\s*D/?B/?A\b[^)]*\)|\bD/?B/?A\b.*$", re.I)
_DIVISION_RE = re.compile(r",?\s+A DIVISION OF\b.*$", re.I)


def _strip_corporate_suffix(s):
    prev = None
    while prev != s:
        prev = s
        s = _CORP_SUFFIX_RE.sub("", s).strip()
        s = _TRAILING_COUNTRY_CODE_RE.sub("", s).strip()
    return s


def _normalize_for_mfr_matching(name):
    """Matching form of a manufacturer name. Punctuation becomes a space BEFORE suffix stripping; 'dba ...' and ', a Division of ...' tails go."""
    s = normalize_key(_apply_known_aliases(name))
    s = _DIVISION_RE.sub(" ", _DBA_RE.sub(" ", s))
    s = re.sub(r"[^A-Za-z0-9]+", " ", s).lower().strip()
    return _strip_corporate_suffix(s)


def _digit_signature(s):
    return tuple(re.findall(r"\d+", s))


def _pick_representative(cluster):
    aliased = [_apply_known_aliases(n) for n in cluster]
    flags = [a != n for a, n in zip(aliased, cluster)]
    candidates = [a for a, f in zip(aliased, flags) if f] if any(flags) else cluster
    return max(candidates, key=lambda c: (len(c), c))


def _fuzzy_cluster(all_names, score_cutoff=88):
    uniques = sorted({str(n).strip() for n in all_names if n and str(n).strip()})
    clusters, norms, sigs, compact_idx = [], [], [], {}
    for name in uniques:
        norm = _normalize_for_mfr_matching(name)
        compact = re.sub(r"[^a-z0-9]", "", norm)
        if compact and compact in compact_idx:               # identical once punctuation / spacing is removed
            clusters[compact_idx[compact]].append(name)
            continue
        sig = _digit_signature(norm)
        match = None
        eligible = [i for i, s in enumerate(sigs) if s == sig]
        if eligible:
            elig_norms = [norms[i] for i in eligible]
            cand = process.extractOne(norm, elig_norms, scorer=fuzz.token_sort_ratio, score_cutoff=score_cutoff)
            if cand:
                match = eligible[elig_norms.index(cand[0])]
        if match is not None:
            clusters[match].append(name)
            compact_idx.setdefault(compact, match)
        else:
            clusters.append([name]); norms.append(norm); sigs.append(sig)
            compact_idx[compact] = len(clusters) - 1
    return clusters


DUNS_MERGE_MIN_SIMILARITY = 45


def _apply_duns_backstop_merge(clusters, name_to_duns):
    def norm_key(n):
        return re.sub(r"[.,]", "", n.lower().strip())

    cluster_duns = [{name_to_duns[norm_key(v)] for v in c if name_to_duns.get(norm_key(v))} for c in clusters]
    refused, merged = set(), True
    while merged:
        merged = False
        for i in range(len(clusters)):
            if not cluster_duns[i]:
                continue
            for j in range(i + 1, len(clusters)):
                if cluster_duns[i] & cluster_duns[j]:
                    sim = fuzz.token_sort_ratio(_normalize_for_mfr_matching(clusters[i][0]), _normalize_for_mfr_matching(clusters[j][0]))
                    if sim < DUNS_MERGE_MIN_SIMILARITY:
                        pair = tuple(sorted((clusters[i][0], clusters[j][0])))
                        if pair not in refused:
                            refused.add(pair)
                            log.warning("Refusing DUNS-based merge of '%s' and '%s' (shared DUNS %s, similarity %.0f < %s).",
                                        pair[0], pair[1], sorted(cluster_duns[i] & cluster_duns[j])[0], sim, DUNS_MERGE_MIN_SIMILARITY)
                        continue
                    clusters[i] = clusters[i] + clusters[j]
                    cluster_duns[i] = cluster_duns[i] | cluster_duns[j]
                    del clusters[j]; del cluster_duns[j]
                    merged = True
                    break
            if merged:
                break
    if refused:
        log.warning("DUNS backstop refused %s merge(s) in total (similarity below %s).", len(refused), DUNS_MERGE_MIN_SIMILARITY)
    final_map, duns_map = {}, {}
    for cluster, duns_set in zip(clusters, cluster_duns):
        rep = _pick_representative(cluster)
        for variant in cluster:
            final_map[variant] = rep
        if duns_set:
            duns_map[rep] = sorted(duns_set)[0]
    return final_map, duns_map


def canonicalize_manufacturers_global(all_names, name_to_duns=None, score_cutoff=88):
    return _apply_duns_backstop_merge(_fuzzy_cluster(all_names, score_cutoff=score_cutoff), name_to_duns or {})


def _derive_parent_company(canonical_name):      # kept for backwards compatibility
    return derive_parent_company(canonical_name)


def _first_nonblank(*values):
    for v in values:
        if v not in (None, "", "N/A", "NA"):
            return v
    return None


# ------------------------------------------------------------------ MAUDE (events)
MAUDE_COUNTRY_FIELDS = ("device.manufacturer_d_country", "manufacturer_g1_country", "manufacturer_country")


def _maude_country(r, device):
    """First non-blank value among the candidate MAUDE country fields (and which field supplied it)."""
    for src, val in (("device.manufacturer_d_country", device.get("manufacturer_d_country")),
                     ("manufacturer_g1_country", r.get("manufacturer_g1_country")),
                     ("manufacturer_country", r.get("manufacturer_country"))):
        v = _first_nonblank(val)
        if v:
            return v, src
    return None, None


def decode_maude_countries(rows):
    """N2: detect which code standard the MAUDE country field uses and decode every value to a country NAME (or None when undecidable).
    Adds row['manufacturer_country']; returns (standard, fips_rows, iso_rows, undecided Counter, decoded count)."""
    raw_codes = [str(r["manufacturer_country_raw"]).strip().upper() for r in rows
                 if r.get("manufacturer_country_raw") and len(str(r["manufacturer_country_raw"]).strip()) <= 2]
    standard, fips, iso = detect_code_standard(raw_codes)
    undecided, decoded = Counter(), 0
    for r in rows:
        raw = r.get("manufacturer_country_raw")
        name = normalize_country(raw, source="maude_native", standard=standard) if raw else None
        r["manufacturer_country"] = name
        if name:
            decoded += 1
        elif raw and str(raw).strip().upper() not in COUNTRY_PLACEHOLDERS:
            undecided[str(raw).strip()] += 1
    return standard, fips, iso, undecided, decoded


def extract_maude():
    raw = _load_bronze("maude_")
    narrative_clean.reset_stats()
    rows, prepared, lit_basis = [], [], []
    country_src = Counter()
    for r in raw:
        device = (r.get("device") or [{}])[0] if r.get("device") else {}
        patient = (r.get("patient") or [{}])[0] if r.get("patient") else {}
        parts = [mt.get("text", "") for mt in (r.get("mdr_text") or []) if mt.get("text")]
        narrative_raw = _clean_text_field(" ".join(parts))
        lit_basis.append(narrative_clean.literature_basis(narrative_raw))
        prepared.append(narrative_clean.prepare_narrative(narrative_raw))

        problems_raw = r.get("product_problems") or []
        if isinstance(problems_raw, str):
            problems_raw = [problems_raw]
        problem_codes = split_dedup_sorted("; ".join(str(p) for p in problems_raw))
        outcome_raw = patient.get("sequence_number_outcome")
        if isinstance(outcome_raw, list):
            outcome_raw = "; ".join(str(o) for o in outcome_raw if o)
        outcomes = decode_patient_outcomes(outcome_raw)

        country, src = _maude_country(r, device)
        country_src[src or "(none)"] += 1
        lot = null_if_placeholder(_clean_text_field(device.get("lot_number")))
        brand = null_if_placeholder(_clean_text_field(device.get("brand_name")))
        generic = null_if_placeholder(_clean_text_field(device.get("generic_name")))
        rows.append({
            "report_number": r.get("report_number"),
            "date_received": r.get("date_received"),
            "event_type": r.get("event_type"),
            "manufacturer_name_raw": _clean_manufacturer_name_raw(device.get("manufacturer_d_name") or r.get("manufacturer_name")),
            "manufacturer_country_raw": country,
            "manufacturer_state": _first_nonblank(r.get("manufacturer_state")),
            "manufacturer_city": _first_nonblank(r.get("manufacturer_city")),
            "product_code": device.get("device_report_product_code"),
            "brand_name": brand,
            "device_name": generic or brand,                    # None when BOTH are missing / placeholders (N5)
            "model": null_if_placeholder(_clean_text_field(device.get("model_number"))),
            "catalog_number": null_if_placeholder(_clean_text_field(device.get("catalog_number"))),
            "lot_number": lot,
            "lot_kind": lot_kind(lot),
            "patient_age": null_if_placeholder(_clean_text_field(patient.get("patient_age"))),
            "patient_sex": _normalize_sex(patient.get("patient_sex")),
            "patient_outcome": "; ".join(outcomes) if outcomes else None,
            "product_problem_codes": "; ".join(problem_codes) if problem_codes else None,
        })

    standard, fips, iso, undecided, decoded = decode_maude_countries(rows)
    log.info("MAUDE manufacturer-country field supplied a value for %.0f%% of records (by field: %s).",
             100.0 * (1 - country_src.get("(none)", 0) / max(len(raw), 1)), dict(country_src))
    log.info("MAUDE country codes: standard detected = %s (FIPS-only codes in %s rows, ISO-only codes in %s rows); %s records decoded to a "
             "country name; undecided codes (left blank, the registry lookup fills them): %s",
             standard, fips, iso, decoded, dict(undecided.most_common(10)) or "none")

    vocab = narrative_clean.build_vocab(prepared)
    common = narrative_clean.build_common_sentences(prepared)
    if common:
        top = sorted(common.values(), key=lambda v: -v[0])[:8]
        log.info("Corpus-frequent sentences (>= %.0f%% of narratives) are removed from narratives that keep other content. "
                 "%s such sentences; most frequent: %s", 100 * narrative_clean.COMMON_SENTENCE_SHARE, len(common),
                 " || ".join(f"{c}x {s[:90]}" for c, s in top))
    for row, prep, basis in zip(rows, prepared, lit_basis):
        narr = narrative_clean.finalize_narrative(prep, vocab, common)
        row["narrative"] = narr
        row["has_narrative"] = bool(narr)
        row["is_literature_report"] = basis is not None
        row["literature_basis"] = basis
        if basis is not None:                      # the age is a study average, not a patient's age
            row["patient_age"] = None
        row["patient_age_years"] = None if basis is not None else parse_patient_age_years(row["patient_age"])

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = _dedup_only(df, source="maude", key_cols=["report_number"])
    df = df.sort_values(["date_received", "report_number"], kind="mergesort").reset_index(drop=True)
    st = narrative_clean.STATS
    log.info("Narrative cleaning: %s lost-character fixes (%s unresolved), %s boilerplate sentences, %s corpus-frequent sentences, "
             "%s repeated sentences removed, %s split words repaired, %s redaction markers replaced.",
             st["lost_char_fixed"], st["lost_char_unresolved"], st["boilerplate_sentences_removed"], st["common_sentences_removed"],
             st["repeated_sentences_removed"], st["split_words_repaired"], st["redaction_markers"])
    if narrative_clean.UNRESOLVED:
        log.info("Lost-character contexts that could not be decided (most frequent): %s",
                 " || ".join(f"{c}x {ctx!r}" for ctx, c in narrative_clean.unresolved_samples(5)))
    log.info("Journal-literature reports flagged: %s of %s (basis: %s). patient_age blanked for these.",
             int(df["is_literature_report"].sum()), len(df), df["literature_basis"].value_counts().to_dict())
    return df


# ------------------------------------------------------------------ duplicate reports (EVT-05 / N4)
DUP_PROBABLE_SIMILARITY = 95.0
DUP_POSSIBLE_SIMILARITY = 80.0
_DUP_COMPARE_CHARS = 20000


def _norm_text(t):
    return re.sub(r"\s+", " ", str(t or "").upper()).strip()


def text_similarity(a, b):
    """0-100 similarity of two narratives (full text, capped at 20,000 characters)."""
    if a == b:
        return 100.0
    return float(fuzz.ratio(a[:_DUP_COMPARE_CHARS], b[:_DUP_COMPARE_CHARS]))


def flag_duplicates(df):
    """Expects df sorted by date and report number; the FIRST report of a group is never flagged. Each report is compared with the earlier
    reports of the same manufacturer, device, model and receipt date (the start of the text does not have to match):
      'Text reference'    the narrative says it duplicates an FDA report                         -> probable
      'Same narrative'    the FULL text is >= 95% similar to an earlier report                   -> probable
      'Similar narrative' the full text is 80-95% similar                                        -> possible
      'Same lot only'     same lot, but no similar narrative (can be a separate patient)         -> possible
    Below 80% similarity there is no narrative flag. Reports are flagged, never removed."""
    if df.empty:
        return df
    groups, l_seen, basis = {}, set(), []
    for mfr, brand, model, date, lot, narr in zip(df["manufacturer_canonical"], df["brand_name"], df["model"], df["date_received"],
                                                   df["lot_number"], df["narrative"]):
        base = "|".join(str(x or "") for x in (mfr, brand, model, date))
        up = _norm_text(narr)
        b = "Text reference" if "DUPLICATE OF FDA REPORT" in up else None
        if len(re.sub(r"\W+", "", up)) >= 30:
            prior = groups.setdefault(base, [])
            if b is None and prior:
                best = max(text_similarity(up, p) for p in prior[-20:])
                if best >= DUP_PROBABLE_SIMILARITY:
                    b = "Same narrative"
                elif best >= DUP_POSSIBLE_SIMILARITY:
                    b = "Similar narrative"
            prior.append(up)
        if lot is not None and not pd.isna(lot):
            lk = base + "|L:" + str(lot)
            if b is None and lk in l_seen:
                b = "Same lot only"
            l_seen.add(lk)
        basis.append(b)
    out = df.copy()
    out["duplicate_basis"] = pd.Series(basis, index=out.index, dtype="object")
    out["is_probable_duplicate"] = out["duplicate_basis"].isin(["Text reference", "Same narrative"]).astype(bool)
    out["is_possible_duplicate"] = out["duplicate_basis"].isin(["Similar narrative", "Same lot only"]).astype(bool)
    log.info("Duplicate reports flagged (never removed): %s probable, %s possible, of %s. By basis: %s",
             int(out["is_probable_duplicate"].sum()), int(out["is_possible_duplicate"].sum()), len(out),
             out["duplicate_basis"].value_counts().to_dict())
    return out


# ------------------------------------------------------------------ recalls, GUDID, classification
def extract_recalls():
    raw = _load_bronze("recalls_")
    rows = []
    for r in raw:
        address = ", ".join(p for p in [r.get("address_1"), r.get("address_2")] if p) or None
        country_raw, state = _first_nonblank(r.get("country")), _first_nonblank(r.get("state"))
        country, derived = country_raw, False
        if not country and state and state.upper() in _US_STATE_CODES:
            country, derived = "United States", True
        rows.append({
            "recall_id": r.get("cfres_id"), "product_code": r.get("product_code"),
            "manufacturer_name_raw": _clean_manufacturer_name_raw(r.get("recalling_firm")),
            "recall_status": r.get("recall_status"), "reason": r.get("root_cause_description"),
            "action": _clean_text_field(r.get("action")), "country": country, "country_is_derived": derived, "state": state,
            "city": _first_nonblank(r.get("city")), "address": safe_str(address),
            "product_description": _clean_text_field(r.get("product_description")),
            "code_info": safe_str(r.get("code_info")),
            "distribution_pattern": _clean_text_field(r.get("distribution_pattern")),
            "distribution_scope": derive_distribution_scope(r.get("distribution_pattern")),
            "product_quantity": r.get("product_quantity"),
            "product_quantity_units": parse_product_quantity_units(r.get("product_quantity")),
            "firm_fei_number": safe_str(r.get("firm_fei_number")), "k_numbers": safe_str(r.get("k_numbers")),
            "pma_numbers": safe_str(r.get("pma_numbers")), "res_event_number": safe_str(r.get("res_event_number")),
            "event_date_posted": r.get("event_date_posted"), "event_date_initiated": r.get("event_date_initiated"),
            "event_date_created": r.get("event_date_created"), "event_date_terminated": r.get("event_date_terminated"),
        })
    return _dedup_only(pd.DataFrame(rows), source="recalls", key_cols=["recall_id"])


def _derive_brand_family(brand_name):
    if not brand_name:
        return brand_name
    tokens = brand_name.strip().split()
    if len(tokens) <= 1:
        return brand_name
    last = tokens[-1]
    if bool(re.search(r"\d", last)) or (last.isupper() and len(last) <= 3):
        return " ".join(tokens[:-1]).strip() or brand_name
    return brand_name


def clean_gudid():
    raw = _load_bronze("gudid_")
    rows = []
    for r in raw:
        first_code = (r.get("product_codes") or [{}])[0]
        identifiers = r.get("identifiers", []) or []
        primary = next((i for i in identifiers if i.get("type") == "Primary"), (identifiers[0] if identifiers else {}))
        gmdn = r.get("gmdn_terms", []) or []
        brand_name = null_if_placeholder(_clean_text_field(r.get("brand_name")))
        model = r.get("version_or_model_number")
        rows.append({
            "device_key_raw": r.get("public_device_record_key"), "brand_name": brand_name,
            "manufacturer_name_raw": _clean_manufacturer_name_raw(r.get("company_name")),
            "product_code": first_code.get("code"), "device_description": _clean_text_field(r.get("device_description")),
            "sku": safe_str(r.get("catalog_number")), "model": model, "primary_di": safe_str(primary.get("id")),
            "issuing_agency": primary.get("issuing_agency"), "product_type": gmdn[0].get("name") if gmdn else None,
            "brand_family": None, "commercial_distribution_status": r.get("commercial_distribution_status"),
            "is_kit": r.get("is_kit"), "is_single_use": r.get("is_single_use"),
            "is_sterile": (r.get("sterilization", {}) or {}).get("is_sterile"), "mri_safety": r.get("mri_safety"),
            "labeler_duns_number": safe_str(r.get("labeler_duns_number")),
            "brand_name_norm": normalize_device_key(brand_name), "model_norm": normalize_device_key(model),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["brand_family"] = df["brand_name"].apply(_derive_brand_family)
    return _dedup_only(df, source="gudid", key_cols=["device_key_raw"])


_DEVICE_CLASS_LABELS = {"1": "Class I", "2": "Class II", "3": "Class III", "U": "Unclassified", "N": "Not classified"}


def clean_classification():
    raw = _load_bronze("classification_")
    rows = []
    for r in raw:
        ob = r.get("openfda", {}) or {}
        dc = safe_str(r.get("device_class"))
        rows.append({
            "product_code": r.get("product_code"), "device_class": dc,
            "device_class_label": _DEVICE_CLASS_LABELS.get((dc or "").upper()),
            "device_name_fda": r.get("device_name"), "medical_specialty": r.get("medical_specialty"),
            "medical_specialty_description": r.get("medical_specialty_description"),
            "implant_flag": r.get("implant_flag"), "review_panel": r.get("review_panel"),
            "regulation_number": safe_str(r.get("regulation_number")),
            "summary_malfunction_reporting": r.get("summary_malfunction_reporting"), "definition": r.get("definition"),
            "k_numbers_ref": safe_str(ob.get("k_number")), "registration_numbers_ref": safe_str(ob.get("registration_number")),
            "fei_numbers_ref": safe_str(ob.get("fei_number")),
        })
    return _dedup_only(pd.DataFrame(rows), source="classification", key_cols=["product_code"])


def _dedup_only(df, source, key_cols):
    total_in = len(df)
    if total_in == 0:
        log.warning("No rows to clean for source=%s", source)
        return df
    if key_cols and key_cols[0] in df.columns and df[key_cols[0]].isna().mean() > 0.5:
        log.warning("%s: more than 50%% of rows are missing key '%s' before dedup", source, key_cols[0])
    dup_mask = df.duplicated(subset=key_cols, keep="first")
    df = df[~dup_mask].copy()
    log.info("%s: %s rows after dedup (from %s raw, %s duplicates removed)", source, len(df), total_in, int(dup_mask.sum()))
    return df


def _build_name_to_duns(gudid_df):
    if gudid_df.empty or "labeler_duns_number" not in gudid_df.columns:
        return {}
    tmp = gudid_df.dropna(subset=["manufacturer_name_raw", "labeler_duns_number"]).copy()
    if tmp.empty:
        return {}
    tmp["norm_name"] = tmp["manufacturer_name_raw"].str.lower().str.strip().str.replace(r"[.,]", "", regex=True)
    tmp["labeler_duns_number"] = tmp["labeler_duns_number"].apply(_format_duns)
    tmp = tmp.dropna(subset=["labeler_duns_number"])
    if tmp.empty:
        return {}
    return tmp.groupby("norm_name")["labeler_duns_number"].agg(lambda s: Counter(s).most_common(1)[0][0]).to_dict()


def run():
    maude_df, recalls_df = extract_maude(), extract_recalls()
    gudid_df, classification_df = clean_gudid(), clean_classification()
    names = []
    for d in (maude_df, recalls_df, gudid_df):
        if not d.empty and "manufacturer_name_raw" in d.columns:
            names.extend(d["manufacturer_name_raw"].dropna().unique().tolist())
    canon_map, duns_map = canonicalize_manufacturers_global(names, _build_name_to_duns(gudid_df))
    log.info("Global manufacturer canonicalization: %s raw name variants -> %s canonical manufacturers (%s with DUNS)",
             len(names), len(set(canon_map.values())), len(duns_map))
    if not maude_df.empty:
        maude_df["manufacturer_canonical"] = maude_df["manufacturer_name_raw"].map(canon_map).fillna(maude_df["manufacturer_name_raw"])
        maude_df["manufacturer_duns"] = maude_df["manufacturer_canonical"].map(duns_map)
        maude_df = flag_duplicates(maude_df)
        maude_df.to_parquet(SILVER_DIR / "maude_silver.parquet", index=False)
    if not recalls_df.empty:
        recalls_df["manufacturer_canonical"] = recalls_df["manufacturer_name_raw"].map(canon_map).fillna(recalls_df["manufacturer_name_raw"])
        recalls_df["manufacturer_duns"] = recalls_df["manufacturer_canonical"].map(duns_map)
        recalls_df.to_parquet(SILVER_DIR / "recalls_silver.parquet", index=False)
    if not gudid_df.empty:
        gudid_df.to_parquet(SILVER_DIR / "gudid_silver.parquet", index=False)
    if not classification_df.empty:
        classification_df.to_parquet(SILVER_DIR / "classification_silver.parquet", index=False)
    _log_quality_summary(maude_df, recalls_df, gudid_df)
    return maude_df, recalls_df, gudid_df, classification_df


def _log_quality_summary(maude_df, recalls_df, gudid_df):
    log.info("=" * 70)
    log.info("BRONZE -> SILVER DATA QUALITY SUMMARY")
    log.info("=" * 70)
    if not maude_df.empty:
        log.info("MAUDE: %s rows | narrative %.0f%% | age known %.0f%% | outcome %.0f%% | lot known %.0f%% | catalog known %.0f%% | country known %.0f%%",
                 len(maude_df), 100 * maude_df["has_narrative"].mean(), 100 * maude_df["patient_age_years"].notna().mean(),
                 100 * maude_df["patient_outcome"].notna().mean(), 100 * maude_df["lot_number"].notna().mean(),
                 100 * maude_df["catalog_number"].notna().mean(), 100 * maude_df["manufacturer_country"].notna().mean())
    if not recalls_df.empty:
        log.info("Recalls: %s rows | country populated %.0f%% (%.0f%% derived from state) | scope %s | quantity parsed for %s of %s",
                 len(recalls_df), 100 * recalls_df["country"].notna().mean(), 100 * recalls_df["country_is_derived"].mean(),
                 recalls_df["distribution_scope"].value_counts(dropna=False).to_dict(),
                 int(recalls_df["product_quantity_units"].notna().sum()), int(recalls_df["product_quantity"].notna().sum()))
    if not gudid_df.empty:
        log.info("GUDID: %s rows | brand_name_norm populated %.0f%%", len(gudid_df), 100 * gudid_df["brand_name_norm"].astype(bool).mean())
    log.info("=" * 70)


if __name__ == "__main__":
    run()
