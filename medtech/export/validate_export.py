#!/usr/bin/env python3
"""
validate_export.py -- quality gate for PowerBI_Export.xlsx (last pipeline stage; also standalone).

    python validate_export.py [path/to/PowerBI_Export.xlsx] [--log path/to/pipeline.log] [--json out.json]

Exit codes: 0 clean | 1 HIGH finding | 2 CRITICAL finding
v12 adds: raw 2-letter country codes, a country on the 'Unknown' placeholder, the four duplicate bases, credentials (api_key) in the log.
"""
import argparse
import calendar
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_EXPORT = ROOT_DIR / "data" / "gold" / "PowerBI_Export.xlsx"
DEFAULT_LOG = ROOT_DIR / "data" / "_logs" / "pipeline.log"
EXCEL_CELL_LIMIT = 32767
MIN_HISTORY_MONTHS = 6
SLACK = 3

EXPECTED_SHEETS = ["fact_events", "fact_event_outcomes", "fact_event_problem_codes", "fact_recalls", "recall_compliance_ref",
                   "dim_device", "dim_manufacturer", "dim_date", "dim_classification", "classification_compliance_ref",
                   "root_cause_flags", "root_cause_flags_parent", "data_coverage", "field_guide"]
FLAG_COLS = ["risk_velocity_ratio", "velocity_direction", "is_velocity_spike", "is_velocity_drop", "is_statistical_watch", "category_z_score",
             "is_low_history", "flag_label", "literature_event_count", "probable_duplicate_count", "possible_duplicate_count"]
NEW_COLUMNS = {
    "fact_events": ["lot_kind", "is_literature_report", "literature_basis", "is_probable_duplicate", "is_possible_duplicate", "duplicate_basis"],
    "fact_event_problem_codes": ["problem_code_group"],
    "fact_recalls": ["recall_date_source", "recall_initiated_date_key", "reason_group"],
    "recall_compliance_ref": ["code_info_is_truncated"],
    "dim_device": ["brand_name_display", "device_type_fda", "gudid_match_level"],
    "dim_manufacturer": ["display_name", "is_placeholder_entity"],
    "dim_classification": ["device_class_label"],
    "dim_date": ["iso_year", "year_month_label", "year_month_sort"],
    "root_cause_flags": FLAG_COLS,
    "root_cause_flags_parent": ["parent_company"] + FLAG_COLS,
}
REMOVED_COLUMNS = {
    "dim_device": ["device_count", "is_combination_product", "regulatory_segment"], "dim_manufacturer": ["size_tier"],
    "dim_classification": ["life_sustain_support_flag", "gmp_exempt_flag", "third_party_flag"],
    "fact_recalls": ["code_info", "k_numbers", "pma_numbers", "res_event_number", "firm_fei_number", "address"],
}
TEXT_COLUMNS = {
    "fact_events": ["report_number", "lot_number"], "fact_recalls": ["recall_id"],
    "recall_compliance_ref": ["recall_id", "firm_fei_number", "k_numbers", "pma_numbers", "res_event_number"],
    "dim_device": ["sku", "primary_di"], "dim_manufacturer": ["duns_number"], "dim_classification": ["device_class", "regulation_number"],
}
MODEL_LEVEL_FIELDS = ["sku", "primary_di", "issuing_agency", "commercial_distribution_status", "is_kit", "is_single_use", "is_sterile", "mri_safety"]
_BYTE_REPR_RE = re.compile(r"^b'.*'$|^b\"\s*.*\"$")
_LOG_KEY_RE = re.compile(r"(?i)api[_-]?key=(?!\*\*\*)[^&\s'\")\]]+")
BOILERPLATE_RX = r"SUBMITS? THIS REPORT TO COMPLY WITH FDA|DOES NOT CONSTITUTE AN ADMISSION|ANY REQUIRED FIELDS THAT ARE UNPOPULATED|REASONABLE EFFORTS TO PROVIDE AS MUCH"
PLACEHOLDER_BRANDS = {"UNKNOWN", "N/A", "NA", "NONE", "NULL", "-", "."}


def pct(n, d):
    return 0.0 if not d else round(100.0 * n / d, 1)


class Report:
    def __init__(self):
        self.findings = defaultdict(list)

    def add(self, sev, check, msg):
        self.findings[sev].append((check, msg))

    def ok(self, cond, check, fail_sev, ok_msg, fail_msg):
        self.add("PASS" if cond else fail_sev, check, ok_msg if cond else fail_msg)

    def render(self, emit=print):
        order = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "PASS"]
        for sev in order:
            items = self.findings.get(sev, [])
            if items:
                emit("=" * 74)
                emit(f"  {sev}  ({len(items)})")
                emit("=" * 74)
                for check, msg in items:
                    emit(f"  [{check}] {msg}")
        counts = {s: len(self.findings.get(s, [])) for s in order}
        emit("")
        emit("=" * 74)
        emit(f"  RESULT: {counts['CRITICAL']} critical | {counts['HIGH']} high | {counts['MEDIUM']} medium | {counts['LOW']} low | {counts['PASS']} passed")
        emit("=" * 74)
        return counts


# ------------------------------------------------------------------ structure and integrity
def check_structure(d, rep):
    missing = [s for s in EXPECTED_SHEETS if s not in d]
    if missing:
        rep.add("CRITICAL", "structure", f"Missing sheets: {', '.join(missing)}")
        return False
    rep.add("PASS", "structure", f"All {len(EXPECTED_SHEETS)} expected sheets present")
    return True


def check_columns(d, rep):
    missing = [f"{s}.{c}" for s, cols in NEW_COLUMNS.items() for c in cols if c not in d.get(s, pd.DataFrame()).columns]
    removed = [f"{s}.{c}" for s, cols in REMOVED_COLUMNS.items() for c in cols if c in d.get(s, pd.DataFrame()).columns]
    rep.ok(not missing, "new-columns", "HIGH", "All expected v11 / v12 columns are present", f"Expected columns are missing: {missing}")
    rep.ok(not removed, "removed-columns", "HIGH", "No previously removed dead / blob columns reappeared", f"Removed columns reappeared: {removed}")
    return not missing


def check_date_table(d, rep):
    dd = d["dim_date"]
    rep.ok(pd.api.types.is_datetime64_any_dtype(dd["full_date"]), "date-type", "HIGH", "dim_date.full_date is a real date column", "dim_date.full_date is not a real date")
    dates = pd.to_datetime(dd["full_date"], errors="coerce").dropna().sort_values()
    if dates.empty:
        rep.add("CRITICAL", "date-contiguity", "dim_date has no parseable dates")
        return
    missing = pd.date_range(dates.min(), dates.max(), freq="D").difference(dates)
    if len(missing):
        rep.add("CRITICAL", "date-contiguity", f"dim_date is missing {len(missing)} day(s); Mark as Date Table will fail. First: {[x.strftime('%Y-%m-%d') for x in missing[:5]]}")
    elif dates.duplicated().any():
        rep.add("HIGH", "date-contiguity", "dim_date has duplicate dates")
    else:
        rep.add("PASS", "date-contiguity", f"dim_date contiguous: {len(dates)} days, {dates.min().date()} to {dates.max().date()}")
    iso = dates.dt.isocalendar()
    sub = dd.assign(_d=pd.to_datetime(dd["full_date"])).sort_values("_d")
    ok = bool((iso["week"].astype(int).values == sub["week"].astype(int).values).all() and (iso["year"].astype(int).values == sub["iso_year"].astype(int).values).all())
    rep.ok(ok, "iso-week", "HIGH", "dim_date.week / iso_year match the ISO calendar", "dim_date.week / iso_year do not match the ISO calendar")
    last = [pd.to_datetime(d["fact_events"].merge(dd[["date_key", "full_date"]], on="date_key")["full_date"]).max()]
    rec = d["fact_recalls"].merge(dd[["date_key", "full_date"]], on="date_key")["full_date"]
    if len(rec):
        last.append(pd.to_datetime(rec).max())
    last = max(x for x in last if pd.notna(x))
    month_end = last.replace(day=calendar.monthrange(last.year, last.month)[1])
    rep.ok(dates.max() <= month_end, "calendar-end", "LOW", f"dim_date ends {dates.max().date()}, inside the last month with data ({last.date()})",
           f"dim_date runs to {dates.max().date()} but the last data is {last.date()} (empty days at the end of the calendar)")


def check_referential_integrity(d, rep):
    pairs = [("fact_events", "device_key", "dim_device", "device_key"), ("fact_events", "mfr_key", "dim_manufacturer", "mfr_key"),
             ("fact_events", "date_key", "dim_date", "date_key"), ("fact_recalls", "device_key", "dim_device", "device_key"),
             ("fact_recalls", "mfr_key", "dim_manufacturer", "mfr_key"), ("fact_recalls", "date_key", "dim_date", "date_key"),
             ("fact_event_outcomes", "event_key", "fact_events", "event_key"), ("fact_event_problem_codes", "event_key", "fact_events", "event_key")]
    bad = 0
    for ft, fk, dt, pk in pairs:
        n = int((~d[ft][fk].isin(d[dt][pk])).sum())
        bad += n
        if n:
            rep.add("CRITICAL", "referential-integrity", f"{ft}.{fk}: {n} orphaned keys")
    init = d["fact_recalls"]["recall_initiated_date_key"].dropna()
    n = int((~init.isin(d["dim_date"]["date_key"])).sum())
    if n:
        bad += n
        rep.add("CRITICAL", "referential-integrity", f"fact_recalls.recall_initiated_date_key: {n} orphaned keys")
    if not bad:
        rep.add("PASS", "referential-integrity", "All fact -> dimension, bridge and initiated-date relationships resolve (0 orphans)")


def check_duplicates(d, rep):
    bad = []
    for sheet, col in (("fact_events", "report_number"), ("fact_recalls", "recall_id"), ("dim_manufacturer", "canonical_name")):
        n = int(d[sheet][col].duplicated().sum())
        if n:
            bad.append(f"{sheet}.{col}: {n}")
    dd = d["dim_device"]
    n = int((dd.groupby(["product_code", "brand_name_norm", "model_norm"], dropna=False)["device_key"].nunique() > 1).sum())
    if n:
        bad.append(f"dim_device key combinations: {n}")
    norm = d["dim_manufacturer"]["canonical_name"].dropna().astype(str).str.replace(r"\s+", " ", regex=True).str.strip().str.upper()
    n = int((norm.value_counts() > 1).sum())
    if n:
        bad.append(f"manufacturer case-duplicate groups: {n}")
    rep.ok(not bad, "duplicates", "HIGH", "No duplicate business keys, device keys or case-duplicate manufacturers", "; ".join(bad))


def check_compliance(d, rep):
    hits = []
    for sheet in ("recall_compliance_ref", "classification_compliance_ref"):
        for col in d[sheet].columns:
            s = d[sheet][col]
            if s.dtype == object:
                n = int(s.dropna().astype(str).apply(lambda v: bool(_BYTE_REPR_RE.match(v))).sum())
                if n:
                    hits.append(f"{sheet}.{col}: {n}")
    rep.ok(not hits, "compliance-corruption", "CRITICAL", "No raw-bytes corruption in compliance tables", f"Raw-bytes text found: {hits}")
    over, at_limit = [], []
    for n, df in d.items():
        if n in ("field_guide", "data_coverage"):
            continue
        for c in df.columns:
            if df[c].dtype != object:
                continue
            lens = df[c].dropna().astype(str).str.len()
            if len(lens) and int(lens.max()) > EXCEL_CELL_LIMIT:
                over.append(f"{n}.{c}")
            cut_silently = (lens >= EXCEL_CELL_LIMIT) & ~df[c].dropna().astype(str).str.contains("[truncated", regex=False)
            if int(cut_silently.sum()):
                at_limit.append(f"{n}.{c} ({int(cut_silently.sum())})")
    rep.ok(not over, "excel-cell-limit", "CRITICAL", f"No cell exceeds Excel's {EXCEL_CELL_LIMIT}-character limit", f"Cells over the limit: {over}")
    rep.ok(not at_limit, "excel-silent-cut", "HIGH", "No cell sits exactly at the limit without a truncation marker",
           f"Cells at exactly {EXCEL_CELL_LIMIT} characters (silently cut by Excel, text is incomplete): {at_limit}")
    rc = d["recall_compliance_ref"]
    has_marker = rc["code_info"].fillna("").str.contains("[truncated", regex=False)
    rep.ok(bool((has_marker == (rc["code_info_is_truncated"] == 1)).all()), "truncation-flag", "HIGH",
           f"code_info_is_truncated matches the truncation marker ({int(has_marker.sum())} cut value(s); full text in recall_code_info_full.csv)",
           "code_info_is_truncated does not match the truncation marker in code_info")


def check_text_identifier_storage(path, rep):
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True)
    except Exception as exc:      # noqa: BLE001
        rep.add("MEDIUM", "text-cells", f"Could not open the workbook to inspect cell types: {exc}")
        return
    problems = []
    for sheet, cols in TEXT_COLUMNS.items():
        if sheet not in wb.sheetnames:
            continue
        rows = wb[sheet].iter_rows(min_row=1, max_row=1500)
        header = [c.value for c in next(rows)]
        bad = {c: 0 for c in cols if c in header}
        for row in rows:
            for c in bad:
                cell = row[header.index(c)]
                if cell.value is not None and cell.data_type != "s":
                    bad[c] += 1
        problems += [f"{sheet}.{c} ({n} non-text cells)" for c, n in bad.items() if n]
    wb.close()
    rep.ok(not problems, "text-cells", "CRITICAL", "Identifier columns (lot, DUNS, primary_di, regulation_number, FEI / K / PMA ...) are TEXT cells on disk",
           f"Identifier columns stored as numbers: {problems}")


def check_log_secrets(log_path, rep):
    """v12 N1: the log must never contain the openFDA API key."""
    p = Path(log_path)
    if not p.exists():
        rep.add("LOW", "log-secrets", f"Log file {p} not found - credential check skipped")
        return
    text = p.read_text(encoding="utf-8", errors="replace")
    hits = _LOG_KEY_RE.findall(text)
    rep.ok(not hits, "log-secrets", "HIGH", "No API key in the log (all credentials are masked)",
           f"{len(hits)} line fragment(s) in {p.name} contain an unmasked API key. Create a NEW openFDA key and do not share this log.")


# ------------------------------------------------------------------ risk flags
def _event_month_bounds(d):
    m = d["fact_events"].merge(d["dim_date"][["date_key", "full_date"]], on="date_key")
    lo, hi = pd.to_datetime(m["full_date"]).min(), pd.to_datetime(m["full_date"]).max()
    first = lo.year * 12 + lo.month - 1 + (1 if lo.day > 1 + SLACK else 0)
    last = hi.year * 12 + hi.month - 1 - (1 if hi.day < calendar.monthrange(hi.year, hi.month)[1] - SLACK else 0)
    return lo, hi, first, last


def check_flags(d, rep, sheet):
    rc, label = d[sheet], sheet
    if rc.empty:
        rep.add("HIGH", f"{label}-empty", f"{sheet} is empty")
        return
    name = "parent_company" if sheet.endswith("parent") else "manufacturer"
    ok = bool(((rc["risk_velocity_pct"] / 100.0).round(4) - rc["risk_velocity_ratio"]).abs().fillna(0).max() < 1e-3)
    rep.ok(ok, f"{label}-velocity-ratio", "HIGH", "risk_velocity_ratio = risk_velocity_pct / 100 on every row", "risk_velocity_ratio does not equal risk_velocity_pct / 100")
    for col in ("prev_month_count", "trailing_history_months"):
        s = rc[col].dropna()
        rep.ok(bool((s == s.round()).all()), f"{label}-int", "MEDIUM", f"{col} holds whole numbers", f"{col} holds non-integer values")
    rep.ok(not rc["flag_reason"].astype(str).str.contains(";", regex=False).any(), f"{label}-flag-format", "MEDIUM", "flag_reason is clean (no semicolons)", "flag_reason still contains semicolons")
    tags = [("is_statistical_breach", "STATISTICAL_BREACH"), ("is_statistical_watch", "STATISTICAL_WATCH"), ("is_velocity_spike", "VELOCITY_SPIKE"), ("is_velocity_drop", "VELOCITY_DROP")]
    consistent = all(((rc[c] == 1) == rc["flag_reason"].astype(str).str.contains(t)).all() for c, t in tags)
    rep.ok(consistent, f"{label}-flag-consistency", "HIGH", "The four flag columns agree with flag_reason", "A flag column disagrees with flag_reason")
    rise_ok = bool((rc.loc[rc["is_velocity_spike"] == 1, "risk_velocity_pct"] > 0).all() and (rc.loc[rc["is_velocity_drop"] == 1, "risk_velocity_pct"] < 0).all())
    dir_ok = bool(((rc["risk_velocity_pct"] > 0) == (rc["velocity_direction"] == "Rise")).all() and ((rc["risk_velocity_pct"] < 0) == (rc["velocity_direction"] == "Fall")).all())
    rep.ok(rise_ok and dir_ok, f"{label}-direction", "HIGH", "Spikes are rises, drops are falls, velocity_direction matches the sign", "A 'spike' has a negative velocity, a 'drop' a positive one, or velocity_direction is wrong")
    low = rc["is_low_history"] == 1
    bw = rc[(rc["is_statistical_breach"] == 1) | (rc["is_statistical_watch"] == 1)]
    rep.ok(not (bw["is_low_history"] == 1).any(), f"{label}-breach-history", "HIGH", "Breach / watch rows all have >= 6 prior months", "A breach or watch row has too little history")
    rep.ok(bool(rc.loc[low, "category_z_score"].isna().all()), f"{label}-z-blank", "MEDIUM", "category_z_score is blank where the baseline is short", "category_z_score is filled on low-history rows")
    br = rc[rc["is_statistical_breach"] == 1]
    wt = rc[rc["is_statistical_watch"] == 1]
    zok = bool((br["category_z_score"].dropna() > 3).all() and (wt["category_z_score"].between(2, 3)).all())
    rep.ok(zok, f"{label}-z-bands", "HIGH", f"{len(br)} breach row(s) have z > 3 and {len(wt)} watch row(s) have 2 <= z <= 3", "category_z_score is outside the band of its flag")
    rep.ok(not ((rc["prev_month_count"] == 0) & rc["risk_velocity_pct"].notna()).any(), f"{label}-velocity-calendar", "HIGH",
           "Velocity is blank whenever the previous calendar month had no events", "A velocity is reported although the previous month had 0 events")
    rep.ok(bool(((rc["literature_event_count"] <= rc["event_count"]) & (rc["probable_duplicate_count"] <= rc["event_count"]) & (rc["possible_duplicate_count"] <= rc["event_count"])).all()),
           f"{label}-counts", "MEDIUM", "Literature / duplicate counts never exceed event_count", "A literature / duplicate count exceeds event_count")
    rep.ok(not rc[name].isin(["Unknown"]).any(), f"{label}-unknown", "MEDIUM", "The 'Unknown' entity is not flagged", "The 'Unknown' entity appears in the flags")
    rep.ok(not rc.duplicated([name, "category", "yr", "mo"]).any(), f"{label}-unique", "HIGH", "One row per entity, category and month", "Duplicate entity / category / month rows")
    kw = rc["top_keywords"].fillna("").str.contains(r"\bredacted\b", regex=True)
    rep.ok(not kw.any(), f"{label}-keywords", "LOW", "top_keywords has no 'redacted' / template words", f"{int(kw.sum())} keyword list(s) contain 'redacted'")
    lo, hi, first, last = _event_month_bounds(d)
    mi = rc["yr"] * 12 + rc["mo"] - 1
    outside = int(((mi < first) | (mi > last)).sum())
    rep.ok(outside == 0, f"{label}-partial-months", "HIGH", f"No flag falls in a partial month (complete months: {first // 12}-{first % 12 + 1:02d} .. {last // 12}-{last % 12 + 1:02d})",
           f"{outside} flag row(s) fall in a partial month")
    if lo.day > 1 + SLACK:
        first_rows = rc[mi == first]
        rep.ok(first_rows["prev_month_count"].isna().all(), f"{label}-partial-previous", "HIGH", "The first complete month does not use the partial month before it as 'previous month'",
               "A flag in the first complete month still compares with the partial month before it")


def check_events(d, rep):
    fe = d["fact_events"]
    chrono = fe.merge(d["dim_date"][["date_key", "full_date"]], on="date_key").sort_values("event_key")
    rep.ok(bool(chrono["full_date"].is_monotonic_increasing), "event-key-order", "MEDIUM", "event_key is chronological", "event_key is not in date order")
    na_age = int(fe["patient_age"].astype(str).str.upper().isin(["NA", "N/A", "UNKNOWN", "NONE"]).sum())
    bad_lot = int(fe["lot_number"].astype(str).str.upper().isin(["UNKNOWN", "NA", "N/A", "NONE"]).sum())
    rep.ok(na_age + bad_lot == 0, "placeholders", "HIGH", "No 'NA' / 'UNKNOWN' placeholder text in patient_age or lot_number", f"Placeholder text remains: patient_age {na_age}, lot_number {bad_lot}")
    lit = fe[fe["is_literature_report"] == 1]
    leaked = int(lit["patient_age_years"].notna().sum() + lit["patient_age"].notna().sum())
    rep.ok(leaked == 0, "literature-age", "HIGH", f"{len(lit)} journal-literature reports flagged ({lit['literature_basis'].value_counts().to_dict()}); none carries an age",
           f"{leaked} literature report(s) still carry an age (text or years)")
    rep.ok(bool(set(lit["literature_basis"].dropna().unique()) <= {"Template", "Citation"} and lit["literature_basis"].notna().all()), "literature-basis", "MEDIUM", "literature_basis is Template or Citation for every literature report", "literature_basis is missing or unexpected")
    prob, poss = fe["is_probable_duplicate"] == 1, fe["is_possible_duplicate"] == 1
    basis_ok = bool(fe.loc[prob, "duplicate_basis"].isin(["Text reference", "Same narrative"]).all()
                    and fe.loc[poss, "duplicate_basis"].isin(["Similar narrative", "Same lot only"]).all() and not (prob & poss).any()
                    and fe.loc[~(prob | poss), "duplicate_basis"].isna().all())
    rep.ok(basis_ok, "duplicate-basis", "HIGH",
           f"Duplicates: {int(prob.sum())} probable ({fe.loc[prob, 'duplicate_basis'].value_counts().to_dict()}), {int(poss.sum())} possible ({fe.loc[poss, 'duplicate_basis'].value_counts().to_dict()}); flagged, never removed",
           "is_probable_duplicate / is_possible_duplicate disagree with duplicate_basis")
    for col in ("patient_outcome", "product_problem_codes"):
        s = fe[col].dropna().astype(str)
        unsorted = int(s.apply(lambda v: [t.strip() for t in v.split(";")] != sorted(t.strip() for t in v.split(";"))).sum())
        dup = int(s.apply(lambda v: len({t.strip() for t in v.split(";")}) != len(v.split(";"))).sum())
        bare = int(s.apply(lambda v: any(len(t.strip()) == 1 for t in v.split(";"))).sum()) if col == "patient_outcome" else 0
        rep.ok(unsorted + dup + bare == 0, "multi-value-" + col, "HIGH", f"{col}: tokens are unique, sorted and decoded", f"{col}: {unsorted} unsorted, {dup} with repeats, {bare} undecoded")
    pcg = set(d["fact_event_problem_codes"]["problem_code_group"].dropna().unique())
    rep.ok(pcg <= {"Informative", "Uninformative"}, "problem-code-group", "HIGH", f"problem_code_group values: {sorted(pcg)}", f"Unexpected problem_code_group values: {sorted(pcg)}")
    generic = d["fact_event_problem_codes"][d["fact_event_problem_codes"]["problem_code"].str.contains(r"Insufficient Device Problem Information|No Apparent Adverse Event", case=False, regex=True)]
    rep.ok((generic["problem_code_group"] == "Uninformative").all(), "generic-problem-codes", "LOW", "Generic problem codes are grouped Uninformative", "Generic problem codes are still grouped Informative")
    sexv = set(fe["patient_sex"].dropna().unique())
    rep.ok("Prefer Not To Disclose" not in sexv, "patient-sex", "LOW", f"patient_sex values: {sorted(sexv)}", "'Prefer Not To Disclose' is still a separate patient_sex value")
    lk = fe.loc[fe["lot_number"].notna(), "lot_kind"]
    pma = fe["lot_number"].astype(str).str.fullmatch(r"P\d{8,9}")
    rep.ok(bool(lk.notna().all() and (fe.loc[pma & fe["lot_number"].notna(), "lot_kind"] == "PMA code").all()), "lot-kind", "MEDIUM", f"lot_kind filled for every lot ({lk.value_counts().to_dict()})", "lot_kind is missing or wrong for some lots")


def check_narratives(d, rep):
    nt = d["fact_events"]["narrative_text"].dropna().astype(str)
    lost = int(nt.str.contains("\u00bf", regex=False).sum())
    share = 100.0 * lost / max(len(nt), 1)
    rep.add("PASS" if lost == 0 else ("LOW" if share < 1.0 else "MEDIUM"), "narrative-lost-chars",
            "No lost-character symbols left in narratives" if lost == 0 else f"{lost} narrative(s) ({share:.1f}%) still contain the lost-character symbol (the remaining contexts cannot be decided safely; the log lists the most frequent ones)")
    boiler = int(nt.str.contains(BOILERPLATE_RX, case=False, regex=True).sum())
    rep.ok(boiler == 0, "narrative-boilerplate", "MEDIUM", "Legal boilerplate removed from narratives", f"{boiler} narrative(s) still contain legal boilerplate")
    redact = int(nt.str.contains(r"\(B\)\(\d\)", regex=True).sum())
    rep.ok(redact == 0, "narrative-redaction", "LOW", "Redaction markers replaced", f"{redact} narrative(s) still contain (B)(n) markers")
    dup_sent = 0
    for t in nt.head(2000):
        s = [re.sub(r"[^A-Z0-9]+", "", x.upper()) for x in re.split(r"(?<=[.!?])\s+", t) if len(x.strip()) >= 20]
        dup_sent += len(s) - len(set(s))
    rep.ok(dup_sent == 0, "narrative-repeats", "LOW", "No repeated sentences inside narratives (first 2,000 checked)", f"{dup_sent} repeated sentence(s) remain")


# ------------------------------------------------------------------ devices, manufacturers, recalls
def check_devices(d, rep):
    dd = d["dim_device"]
    over = int((dd["catalog_number_observed"].dropna().astype(str).str.len() > 60).sum())
    rep.ok(over == 0, "catalog-length", "HIGH", "catalog_number_observed is never a long text (max 60 characters)", f"{over} catalog_number_observed value(s) are longer than 60 characters (lot / serial lists)")
    long_b = int((dd["brand_name"].dropna().astype(str).str.len() > 120).sum())
    rep.ok(long_b == 0, "brand-length", "MEDIUM", "brand_name is never a paragraph (max 120 characters)", f"{long_b} brand_name value(s) are longer than 120 characters")
    lvl = dd["gudid_match_level"]
    consistent = bool(((lvl == "Model") == (dd["match_method"] == "catalog_number")).all() and ((lvl == "None") == (dd["match_method"] == "not_matched")).all()
                      and ((lvl == "Brand") == dd["match_method"].isin(["brand_name_exact", "brand_name_fuzzy"])).all())
    rep.ok(consistent, "gudid-level", "HIGH", "gudid_match_level agrees with match_method", "gudid_match_level disagrees with match_method")
    non_model = dd[lvl != "Model"]
    viol = int(sum(non_model[f].notna().sum() for f in MODEL_LEVEL_FIELDS))
    rep.ok(viol == 0, "gudid-model-fields", "HIGH", "Model-specific GUDID fields are filled only for catalog_number matches", f"{viol} model-specific GUDID value(s) sit on brand-only devices")
    per = dd.groupby("category").agg(n=("device_key", "size"), any_=("gudid_enriched", "sum"), model=("gudid_match_level", lambda s: int((s == "Model").sum())))
    txt = ", ".join(f"{c}: any {pct(r.any_, r.n)}% / model {pct(r.model, r.n)}%" for c, r in per.iterrows())
    overall_model = pct((lvl == "Model").sum(), len(dd))
    rep.add("MEDIUM" if overall_model < 50 else "PASS", "gudid-coverage",
            f"GUDID enrichment {pct(dd['gudid_enriched'].sum(), len(dd))}% any level, {overall_model}% model-level ({txt}); source limitation, see data_coverage sheet")
    fda_ok = dd.loc[dd["product_code"].isin(d["dim_classification"]["product_code"]), "device_type_fda"].notna().all()
    rep.ok(bool(fda_ok), "device-type-fda", "MEDIUM", "device_type_fda is filled for every device", "device_type_fda is missing for some devices")
    desc = dd["device_description"].dropna().astype(str)
    trailing = int(desc.str.contains(r"[,;:]\s*$", regex=True).sum())
    codes = int(desc.str.fullmatch(r"[A-Z]{3}(?:,\s*[A-Z]{3})*").sum())
    var = dd.assign(_k=dd["device_description"].astype(str).str.upper().str.strip()).groupby("_k")["device_description"].nunique()
    shouting = int((desc.str.isupper() & (desc.str.split().str.len() >= 2)).sum())
    rep.ok(trailing + codes + shouting == 0 and int((var > 1).sum()) == 0 and int(dd["device_description"].isna().sum()) == 0, "device-description", "MEDIUM",
           "device_description: filled, no cut text, no product-code lists, no ALL-CAPS text, one spelling per description",
           f"device_description: {int(dd['device_description'].isna().sum())} empty, {trailing} end with punctuation, {codes} are product-code lists, {shouting} are ALL CAPS, {int((var > 1).sum())} have several spellings")
    own = int((dd["device_description"] != dd["device_type_fda"]).sum())
    rep.add("PASS", "description-vs-fda", f"{len(dd) - own} of {len(dd)} device rows carry the FDA device name as description; {own} keep their own specific term "
                                          f"(group on device_type_fda for a clean label)")
    ph = int(dd["brand_name"].astype(str).str.strip().str.upper().isin(PLACEHOLDER_BRANDS).sum())
    rep.ok(ph == 0, "brand-placeholders", "LOW", "No placeholder brand names", f"{ph} brand_name value(s) are placeholders (UNKNOWN / N/A)")
    compact = dd["brand_name_norm"].fillna("").str.replace(r"[^A-Z0-9+&#]", "", regex=True)
    near = int((dd.assign(_c=compact).groupby(["product_code", "_c", "model_norm"], dropna=False)["brand_name_norm"].nunique() > 1).sum())
    rep.ok(near == 0, "device-key-punctuation", "MEDIUM", "No devices that differ only by punctuation or spacing", f"{near} device key group(s) differ only by punctuation / spacing")
    shown = int(dd["brand_name_display"].fillna("").str.contains("\u00bf|\ufffd", regex=True).sum())
    rep.ok(shown == 0, "brand-display", "LOW", "brand_name_display has no broken symbols", f"brand_name_display has {shown} broken symbols")


def check_manufacturers(d, rep):
    dm = d["dim_manufacturer"]
    comp = dm["canonical_name"].astype(str).str.upper().str.replace(r"\(\s*D/?B/?A[^)]*\)|\bD/?B/?A\b.*$", "", regex=True).str.replace(r"[^A-Z0-9]", "", regex=True)
    near = dm[comp.duplicated(keep=False)]["canonical_name"].tolist()
    rep.ok(not near, "mfr-near-duplicates", "MEDIUM", "No manufacturer names that are identical once punctuation and 'dba' tails are removed", f"Near-duplicate manufacturer names: {near}")
    rep.ok(bool(((dm["is_placeholder_entity"] == 1) == (dm["canonical_name"] == "Unknown")).all()), "placeholder-entity", "MEDIUM", "is_placeholder_entity marks exactly the 'Unknown' manufacturer", "is_placeholder_entity does not match the 'Unknown' row")
    ph = dm[dm["is_placeholder_entity"] == 1]
    rep.ok(bool(ph["country"].isna().all()), "placeholder-country", "MEDIUM", "The 'Unknown' manufacturer has no country", f"The placeholder manufacturer carries a country: {ph['country'].dropna().tolist()}")
    raw_codes = dm[dm["country"].astype(str).str.fullmatch(r"[A-Z]{2}")][["canonical_name", "country"]].values.tolist()
    rep.ok(not raw_codes, "country-raw-codes", "HIGH", "dim_manufacturer.country holds country names only (no raw 2-letter codes)", f"Raw country codes in dim_manufacturer.country: {raw_codes}")
    titan = dm[dm["canonical_name"].str.contains("TITAN SPINE", case=False, na=False)]
    rep.ok(bool((titan["parent_company"] == "Medtronic").all()), "parent-titan", "LOW", "Titan Spine rolls up to Medtronic", "Titan Spine is not rolled up to Medtronic")
    bad = int(dm["display_name"].astype(str).str.contains(r"(?i)\b(?:SARL|GMBH|AG|BV|SA|SAS|SRL|LTD|LLC|INC\.?|KG|SPA|PLC)\s+(?:CH|DE|FR|IT|ES|NL|BE|JP|CN|US|UK)$", regex=True).sum())
    rep.ok(bad == 0, "display-name", "LOW", "display_name has no trailing country codes", f"{bad} display_name value(s) end with a country code")
    rep.ok(int(dm["parent_company"].isna().sum()) == 0, "parent-company", "LOW", "parent_company is filled for every manufacturer", "Some manufacturers have no parent_company")
    real = dm[dm["is_placeholder_entity"] != 1]
    cov = pct(real["country"].notna().sum(), len(real))
    unknown_names = real[real["country"].isna()]["canonical_name"].tolist()
    rep.add("PASS" if cov >= 98 else ("LOW" if cov >= 90 else "MEDIUM"), "country-coverage",
            f"Manufacturer country known for {cov}% of real manufacturers" + (f"; still unknown: {unknown_names[:8]} (add them to config/manufacturer_country_overrides.csv)" if unknown_names else ""))
    src = dm["country_source"].value_counts().to_dict()
    rep.add("LOW" if src.get("maude_native", 0) == 0 else "PASS", "maude-country",
            f"country_source = {src}. " + ("No manufacturer country came from MAUDE - check the 'MAUDE country codes' line in pipeline.log." if src.get("maude_native", 0) == 0 else "MAUDE supplies manufacturer country."))
    dun = pct(dm["duns_number"].notna().sum(), len(dm))
    rep.add("PASS" if dun > 0 else "MEDIUM", "duns-coverage", f"DUNS known for {dun}% of manufacturers")


def check_recalls(d, rep):
    fr = d["fact_recalls"]
    vals = set(fr["distribution_scope"].dropna().unique())
    rep.ok(vals <= {"Worldwide", "International", "Domestic", "Unknown"}, "distribution-scope", "HIGH", f"distribution_scope values: {sorted(vals)}", f"Unexpected distribution_scope values: {sorted(vals)}")
    rg = set(fr["reason_group"].dropna().unique())
    rep.ok(rg <= {"Vague", "Specific"}, "reason-group", "HIGH", f"reason_group: {fr['reason_group'].value_counts().to_dict()}", f"Unexpected reason_group values: {sorted(rg)}")
    q = fr[fr["product_quantity"].notna()]
    blank = q[q["product_quantity_units"].isna() & q["product_quantity"].astype(str).str.contains(r"\d")]
    rep.ok(len(blank) <= max(2, 0.03 * len(q)), "quantity-parsing", "MEDIUM", f"Recalled units parsed for {len(q) - int(q['product_quantity_units'].isna().sum())} of {len(q)} quantity texts ({len(blank)} ambiguous with numbers)",
           f"{len(blank)} of {len(q)} quantity texts contain numbers but were not parsed, e.g. {blank['product_quantity'].astype(str).str[:60].head(3).tolist()}")
    rep.ok(bool((fr["product_quantity_units"].dropna() >= 0).all()), "quantity-sign", "HIGH", "No negative recalled units", "Negative recalled units found")
    src = set(fr["recall_date_source"].dropna().unique())
    rep.add("PASS" if src else "MEDIUM", "recall-date-source", f"recall_date_source used: {sorted(src)}; initiated date known for {pct(fr['recall_initiated_date_key'].notna().sum(), len(fr))}% of recalls")
    dc = d["dim_classification"]
    rep.ok(bool(dc["device_class_label"].notna().all()), "device-class-label", "LOW", "device_class_label filled for every product code", "device_class_label is missing")


def check_temporal_and_misc(d, rep):
    fe = d["fact_events"].merge(d["dim_date"][["date_key", "full_date"]], on="date_key")
    months = round((fe["full_date"].max() - fe["full_date"].min()).days / 30.4)
    rep.ok(months >= 20, "lookback", "HIGH", f"Events span {months} months", f"Events only span {months} months")
    shared = set(d["fact_recalls"]["device_key"]) & set(d["fact_events"]["device_key"])
    rep.add("PASS", "cross-signal", f"{len(shared)} devices carry both adverse events and recalls")
    mo = int(d["dim_device"]["brand_name_norm"].fillna("").str.contains("\u00bf|\ufffd", regex=True).sum())
    rep.ok(mo == 0, "mojibake-key", "MEDIUM", "brand_name_norm has no broken symbols", f"brand_name_norm has {mo} broken symbols")
    dead = [f"{s}.{c}" for s, df in d.items() if s not in ("field_guide", "data_coverage") for c in df.columns if len(df) and df[c].isna().all()]
    rep.ok(not dead, "dead-columns", "LOW", "No 100%-null columns", f"100%-null columns: {dead}")
    unclassified = d["field_guide"][d["field_guide"]["recommendation"].astype(str).str.contains("not yet classified")]
    rep.ok(unclassified.empty, "field-guide", "LOW", "Every column is classified in field_guide", f"{len(unclassified)} column(s) not classified in field_guide")


def validate(export_path, json_path=None, emit=print, log_path=None):
    try:
        xl = pd.ExcelFile(export_path)
    except Exception as exc:      # noqa: BLE001
        emit(f"ERROR: could not open {export_path}: {exc}")
        return 2
    # keep_default_na=False: pandas would otherwise turn the TEXT 'NA' / 'N/A' / 'NULL' into blanks and hide placeholders.
    d = {n: xl.parse(n, dtype={c: str for c in TEXT_COLUMNS.get(n, [])}, keep_default_na=False, na_values=[""]) for n in xl.sheet_names}
    rep = Report()
    emit("=" * 74)
    emit("  MedTech Risk & Market Intelligence -- Export Validation (v12)")
    emit("=" * 74)
    emit(f"  file: {export_path}")
    for n, df in d.items():
        emit(f"    {n:<30} {len(df):>8,} rows {len(df.columns):>3} cols")
    emit("")
    if check_structure(d, rep) and check_columns(d, rep):
        check_date_table(d, rep)
        check_referential_integrity(d, rep)
        check_duplicates(d, rep)
        check_compliance(d, rep)
        check_text_identifier_storage(export_path, rep)
        check_flags(d, rep, "root_cause_flags")
        check_flags(d, rep, "root_cause_flags_parent")
        check_events(d, rep)
        check_narratives(d, rep)
        check_devices(d, rep)
        check_manufacturers(d, rep)
        check_recalls(d, rep)
        check_temporal_and_misc(d, rep)
    if log_path is not None:
        check_log_secrets(log_path, rep)
    counts = rep.render(emit=emit)
    if json_path:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({s: [{"check": c, "message": m} for c, m in items] for s, items in rep.findings.items()}, f, indent=2)
    return 2 if counts["CRITICAL"] else (1 if counts["HIGH"] else 0)


def run():
    try:
        from medtech.common import get_logger
        log = get_logger("validate_export")
        return validate(str(DEFAULT_EXPORT), emit=log.info, log_path=DEFAULT_LOG)
    except SystemExit:
        raise
    except Exception:      # noqa: BLE001
        return validate(str(DEFAULT_EXPORT), emit=print, log_path=DEFAULT_LOG)


def main():
    ap = argparse.ArgumentParser(description="Validate a PowerBI_Export.xlsx")
    ap.add_argument("export", nargs="?", default=str(DEFAULT_EXPORT))
    ap.add_argument("--log", metavar="PATH", help="also scan this pipeline.log for credentials")
    ap.add_argument("--json", metavar="PATH")
    a = ap.parse_args()
    return validate(a.export, json_path=a.json, log_path=a.log)


if __name__ == "__main__":
    sys.exit(main())
