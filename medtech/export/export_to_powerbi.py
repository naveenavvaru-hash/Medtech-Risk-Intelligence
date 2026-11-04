"""
export_to_powerbi.py -- Gold SQLite warehouse -> PowerBI_Export.xlsx

  Excel cannot hold more than 32,767 characters in a cell: longer text is cut at EXCEL_TEXT_LIMIT with a marker, the row is flagged
  (recall_compliance_ref.code_info_is_truncated) and the COMPLETE text is written to data/gold/recall_code_info_full.csv.
  Typed columns (nullable integers, real dates), identifier columns stored and verified as TEXT cells, data_coverage and field_guide sheets,
  a second flags sheet per parent company.
"""
import importlib.util
import re
import sqlite3
import zipfile

import pandas as pd

from medtech.common import GOLD_DIR, get_logger
from medtech.gold.root_cause import FLAG_TABLES, output_columns

log = get_logger("export_to_powerbi")
DB_PATH = GOLD_DIR / "medtech_warehouse.db"
CODE_INFO_FULL_CSV = GOLD_DIR / "recall_code_info_full.csv"
OUTPUT_PATH = GOLD_DIR / "PowerBI_Export.xlsx"
EXCEL_CELL_LIMIT = 32767
EXCEL_TEXT_LIMIT = 32000
TRUNCATION_MARKER = " ...[truncated - full text in recall_code_info_full.csv]"

TABLES = ["fact_events", "fact_event_outcomes", "fact_event_problem_codes", "fact_recalls", "recall_compliance_ref",
          "dim_device", "dim_manufacturer", "dim_date", "dim_classification", "classification_compliance_ref",
            "root_cause_flags", "root_cause_flags_parent"]       # the two flag tables are produced by gold/root_cause.py
_BYTE_REPR_RE = re.compile(r"^b'.*'$|^b\"\s*.*\"$")

TEXT_COLUMNS = {
    "fact_events": ["report_number", "lot_number"],
    "fact_recalls": ["recall_id"],
    "recall_compliance_ref": ["recall_id", "address", "code_info", "firm_fei_number", "k_numbers", "pma_numbers", "res_event_number"],
    "dim_device": ["sku", "catalog_number_observed", "primary_di", "model", "model_norm"],
    "dim_manufacturer": ["duns_number"],
    "dim_classification": ["device_class", "regulation_number"],
    "classification_compliance_ref": ["k_numbers_ref", "registration_numbers_ref", "fei_numbers_ref"],
}
INT_COLUMNS = {"root_cause_flags": ["prev_month_count", "trailing_history_months"],
               "root_cause_flags_parent": ["prev_month_count", "trailing_history_months"]}


def _fg(**kw):
    return kw


_FLAGS_GUIDE = dict(
    category="keep", yr="keep", mo="keep", event_count="keep - core measure", prev_month_count="keep - previous CALENDAR month (blank = month missing / partial)",
    risk_velocity_pct="keep - percentage POINTS (800 = 800%)", is_statistical_breach="keep - 0/1, rolling-baseline 3-sigma breach",
    category_event_count="keep", trailing_mean="drill-through only", trailing_stdev="drill-through only (sample stdev)",
    trailing_ucl="keep - control limit", trailing_history_months="hide - months in the baseline (max 12); use is_low_history",
    flag_reason="keep - clean text", risk_velocity_ratio="keep - fraction (8.0 = 800%); format as Percentage",
    velocity_direction="keep - Rise / Fall", is_velocity_spike="keep - 0/1 rise >= +75%", is_velocity_drop="keep - 0/1 fall <= -75%",
    is_statistical_watch="keep - 0/1 category z-score >= 2 but not a breach", category_z_score="keep - blank when the baseline has < 6 months",
    is_low_history="keep - 1 = fewer than 6 prior months", flag_label="keep - readable slicer value",
    literature_event_count="keep - events that restate journal articles", probable_duplicate_count="keep - probable duplicates",
    possible_duplicate_count="keep - possible duplicates (similar narrative or same lot only)", top_keywords="AI summary card only", narrative_count="keep",
    sample_narrative_1="AI summary card only", sample_narrative_2="AI summary card only", sample_narrative_3="AI summary card only")

FIELD_GUIDE = {
    "fact_events": _fg(
        event_key="keep - surrogate key (chronological)", report_number="keep - business key", device_key="keep - FK", mfr_key="keep - FK",
        date_key="keep - FK (FDA RECEIPT date, not event date)", event_type="keep - core slicer", narrative_text="hide from visuals - cleaned free text",
        lot_number="hide from visuals - text", lot_kind="keep - Lot / PMA code / Multiple", patient_age="hide from visuals - blank for literature reports",
        patient_age_years="keep - numeric age (blank for literature reports)", patient_sex="keep - core slicer (Unknown includes 'prefer not to disclose')",
        patient_outcome="hide from visuals - use bridge table", product_problem_codes="hide from visuals - use bridge table",
        has_narrative="keep - 0/1", is_literature_report="keep - 1 = restates a journal article", literature_basis="keep - Template / Citation",
        is_probable_duplicate="keep - 1 = text reference or >= 95% similar narrative", is_possible_duplicate="keep - 1 = 80-95% similar narrative, or same lot only (may be a separate event)",
        duplicate_basis="keep - Text reference / Same narrative / Similar narrative / Same lot only"),
    "fact_event_outcomes": _fg(event_key="keep - FK", outcome="keep - bridge table"),
    "fact_event_problem_codes": _fg(event_key="keep - FK", problem_code="keep - bridge table",
                                    problem_code_group="keep - Informative / Uninformative (filter the problem chart on this)"),
    "fact_recalls": _fg(
        recall_key="keep", recall_id="keep - business key", device_key="keep - FK", mfr_key="keep - FK",
        date_key="keep - FK (FDA posted / created date - see recall_date_source)", recall_date_source="keep - which FDA field supplied date_key",
        recall_initiated_date_key="keep - FK, date the firm started the recall (use for recall trends; inactive relationship)",
        recall_status="keep - core slicer", reason="keep - core slicer", reason_group="keep - Vague / Specific", action="hide from visuals",
        country="keep - geo slicer (Data category: Country)", state="keep - geo slicer", city="keep - geo slicer", product_description="hide from visuals",
        distribution_scope="keep - Worldwide / International / Domestic / Unknown", product_quantity="hide from visuals",
        product_quantity_units="keep - numeric units", country_is_derived="hide from visuals - lineage flag"),
    "recall_compliance_ref": _fg(recall_id="keep on compliance page only", address="keep on compliance page only",
                                 code_info="keep on compliance page only (cut at 32,000 characters - see code_info_is_truncated)",
                                 code_info_is_truncated="keep on compliance page only - 1 = cut; full text in recall_code_info_full.csv",
                                 firm_fei_number="keep on compliance page only", k_numbers="keep on compliance page only",
                                 pma_numbers="keep on compliance page only", res_event_number="keep on compliance page only"),
    "dim_device": _fg(
        device_key="keep - primary key", product_code="keep - core slicer", device_description="keep - cleaned MAUDE term (FDA name when it is a variant of it)",
        device_type_fda="keep - FDA device name for the product code (clean label, best for grouping)", category="keep - core slicer (pipeline grouping, not FDA specialty)",
        brand_name="keep - as submitted (recall-only devices: short name; '(not reported)' when the report names no brand)", brand_name_display="keep - cleaned display name",
        model="keep - display label", brand_name_norm="hide from visuals", model_norm="hide from visuals", sku="drill-through only (catalog matches only)",
        catalog_number_observed="drill-through only", primary_di="drill-through only (catalog matches only)", issuing_agency="drill-through only (catalog matches only)",
        product_type="drill-through only", brand_family="drill-through only", commercial_distribution_status="drill-through only (catalog matches only)",
        is_kit="drill-through only (catalog matches only)", is_single_use="drill-through only (catalog matches only)",
        is_sterile="drill-through only (catalog matches only)", mri_safety="drill-through only (catalog matches only)",
        gudid_enriched="hide from visuals - lineage flag (any enrichment)", match_method="hide from visuals - lineage flag",
        gudid_match_level="keep - Model / Brand / None (use this for GUDID coverage)"),
    "dim_manufacturer": _fg(
        mfr_key="keep - primary key", canonical_name="keep - core slicer", display_name="keep - name without country suffix",
        parent_company="keep - roll-up from config/parent_company_map.csv", country="keep - geo slicer (a country name; blank when unknown)", duns_number="hide from visuals",
        country_source="hide from visuals - lineage flag", is_placeholder_entity="keep - 1 = the 'Unknown' manufacturer (exclude from rankings; has no country)"),
    "dim_date": _fg(date_key="keep - primary key", full_date="keep - Mark as date table (real date)", year="keep", quarter="keep", month="keep",
                    month_name="keep - sort by month", week="keep - ISO week", iso_year="keep - pair with ISO week",
                    year_month_label="keep - e.g. Oct 2024", year_month_sort="keep - sort key for year_month_label"),
    "dim_classification": _fg(
        product_code="keep - primary key", device_class="keep - text", device_class_label="keep - Class I / II / III", device_name_fda="keep - display label",
        medical_specialty="keep - slicer", medical_specialty_description="keep - FDA specialty (can differ from dim_device.category)",
        implant_flag="keep - slicer", review_panel="keep - slicer", regulation_number="keep - TEXT (keep it Text in Power Query)",
        summary_malfunction_reporting="keep - slicer", definition="drill-through only (blank for some codes in the FDA data)"),
    "classification_compliance_ref": _fg(product_code="keep on compliance page only", k_numbers_ref="keep on compliance page only",
                                         registration_numbers_ref="keep on compliance page only", fei_numbers_ref="keep on compliance page only"),
    "data_coverage": _fg(**{c: "documentation - do not import" for c in ("sheet", "column", "rows", "non_null", "non_null_pct", "distinct_values")}),
    "root_cause_flags": _fg(manufacturer="keep", mfr_key="hide - link to dim_manufacturer only", **_FLAGS_GUIDE),
    "root_cause_flags_parent": _fg(parent_company="keep - parent-company level (no mfr_key)", **_FLAGS_GUIDE),
}


def _say(msg):
    print(msg)
    log.info(msg)


def _pick_excel_engine():
    if importlib.util.find_spec("xlsxwriter") is not None:
        return "xlsxwriter"
    log.warning("xlsxwriter is not installed - falling back to openpyxl.")
    return "openpyxl"


def _check_date_table_gaps(df):
    if df.empty:
        return
    d = pd.to_datetime(df["full_date"]).sort_values()
    missing = pd.date_range(d.min(), d.max(), freq="D").difference(d)
    if len(missing):
        log.error("DATE TABLE GAP: dim_date is missing %s day(s), e.g. %s", len(missing), [x.strftime("%Y-%m-%d") for x in missing[:10]])
    else:
        log.info("dim_date contiguity check PASSED: %s consecutive days from %s to %s.", len(d), d.min().date(), d.max().date())


def _check_compliance_ref_corruption(sheets):
    bad = False
    for name in ("recall_compliance_ref", "classification_compliance_ref"):
        for col in sheets.get(name, pd.DataFrame()).columns:
            s = sheets[name][col]
            if s.dtype == object:
                n = int(s.dropna().astype(str).apply(lambda v: bool(_BYTE_REPR_RE.match(v))).sum())
                if n:
                    bad = True
                    log.error("BYTE-CORRUPTION: %s.%s has %s raw-bytes-repr value(s).", name, col, n)
    if not bad:
        log.info("compliance-ref byte-corruption check PASSED.")


def enforce_cell_limit(name, df):
    """Cut text above EXCEL_TEXT_LIMIT, log every column that was cut. Returns (df, list of cut column names)."""
    df, cut_cols = df.copy(), []
    for col in df.columns:
        if df[col].dtype != object:
            continue
        s = df[col]
        too_long = s.map(lambda v: isinstance(v, str) and len(v) > EXCEL_TEXT_LIMIT)
        if too_long.any():
            df.loc[too_long, col] = s[too_long].map(lambda v: v[:EXCEL_TEXT_LIMIT] + TRUNCATION_MARKER)
            cut_cols.append(col)
            log.warning("EXCEL CELL LIMIT: %s.%s -- %s value(s) were longer than %s characters and were cut.", name, col, int(too_long.sum()), EXCEL_TEXT_LIMIT)
    return df, cut_cols


def _coverage_table(sheets):
    rows = []
    for name, df in sheets.items():
        if name in ("field_guide", "data_coverage"):
            continue
        n = len(df)
        for col in df.columns:
            nn = int(df[col].notna().sum())
            try:
                distinct = int(df[col].nunique(dropna=True))
            except TypeError:
                distinct = None
            rows.append({"sheet": name, "column": col, "rows": n, "non_null": nn, "non_null_pct": round(100.0 * nn / n, 1) if n else None, "distinct_values": distinct})
    return pd.DataFrame(rows)


def _field_guide_df(sheets):
    rows = []
    for name, df in sheets.items():
        guide = FIELD_GUIDE.get(name, {})
        for col in df.columns:
            rows.append({"sheet": name, "column": col, "recommendation": guide.get(col, "keep - review manually (not yet classified)")})
    return pd.DataFrame(rows)


def _prepare(name, df):
    df = df.copy()
    for col in TEXT_COLUMNS.get(name, []):
        if col in df.columns:
            df[col] = df[col].map(lambda v: None if (v is None or (isinstance(v, float) and pd.isna(v))) else str(v)).astype(object)
    for col in INT_COLUMNS.get(name, []):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    if name == "dim_date":
        df["full_date"] = pd.to_datetime(df["full_date"])
    return df


def verify_text_cells(path, text_columns, sample=300):
    """Read the saved workbook back and confirm identifier columns are stored as text cells."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True)
    problems = []
    for sheet, cols in text_columns.items():
        if sheet not in wb.sheetnames:
            continue
        rows = wb[sheet].iter_rows(min_row=1, max_row=sample + 1)
        header = [c.value for c in next(rows)]
        bad = {c: 0 for c in cols if c in header}
        for row in rows:
            for c in bad:
                cell = row[header.index(c)]
                if cell.value is not None and cell.data_type != "s":
                    bad[c] += 1
        problems += [f"{sheet}.{c}: {n} non-text cell(s)" for c, n in bad.items() if n]
    wb.close()
    return problems


def read_gold_table(conn, table):
    """One Gold table as a DataFrame. A missing risk-flag table gives an empty sheet with a warning (the validator then reports it);
    any other missing table is an error."""
    try:
        return pd.read_sql_query(f"SELECT * FROM {table}", conn)
    except (pd.errors.DatabaseError, sqlite3.Error):
        levels = {name: level for level, name in FLAG_TABLES.items()}
        if table not in levels:
            raise
        log.warning("Table %s is missing from the warehouse (run the risk-flag stage) - the sheet will be empty.", table)
        return pd.DataFrame(columns=output_columns(levels[table]))


def run():
    if not DB_PATH.exists():
        raise SystemExit(f"Gold database not found at {DB_PATH}. Run `python run_pipeline.py` first.")
    conn = sqlite3.connect(DB_PATH)
    sheets = {}
    for table in TABLES:
        sheets[table] = read_gold_table(conn, table)
        log.info("Read %s: %s rows, %s columns", table, len(sheets[table]), len(sheets[table].columns))
    conn.close()

    _check_date_table_gaps(sheets["dim_date"])
    _check_compliance_ref_corruption(sheets)
    sheets = {n: _prepare(n, d) for n, d in sheets.items()}

    full_rows = []
    for name in list(sheets):
        df, cut_cols = enforce_cell_limit(name, sheets[name])
        if name == "recall_compliance_ref":
            long_ids = sheets[name].loc[sheets[name]["code_info"].map(lambda v: isinstance(v, str) and len(v) > EXCEL_TEXT_LIMIT), ["recall_id", "code_info"]]
            df.insert(list(df.columns).index("code_info") + 1, "code_info_is_truncated", df["recall_id"].isin(long_ids["recall_id"]).astype(int))
            full_rows = long_ids
        sheets[name] = df
    pd.DataFrame(full_rows if len(full_rows) else {"recall_id": [], "code_info": []}).to_csv(CODE_INFO_FULL_CSV, index=False)
    log.info("Complete text of %s over-long code_info value(s) written to %s", len(full_rows), CODE_INFO_FULL_CSV.name)

    sheets["data_coverage"] = _coverage_table(sheets)
    sheets["field_guide"] = _field_guide_df(sheets)

    engine = _pick_excel_engine()
    log.info("Writing %s sheets (%s rows) with the %s engine...", len(sheets), f"{sum(len(d) for d in sheets.values()):,}", engine)
    with pd.ExcelWriter(OUTPUT_PATH, engine=engine, datetime_format="yyyy-mm-dd", date_format="yyyy-mm-dd") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name[:31], index=False)
            if engine == "xlsxwriter":
                text_fmt = writer.book.add_format({"num_format": "@"})
                for col in TEXT_COLUMNS.get(name, []):
                    if col in df.columns:
                        i = list(df.columns).index(col)
                        writer.sheets[name[:31]].set_column(i, i, None, text_fmt)
    log.info("Wrote workbook to %s (%s sheets).", OUTPUT_PATH, len(sheets))

    with zipfile.ZipFile(OUTPUT_PATH) as z:
        wbxml = z.read("xl/workbook.xml").decode("utf-8")
    missing = [n[:31] for n in sheets if f'name="{n[:31]}"' not in wbxml]
    if missing:
        raise RuntimeError(f"Export verification FAILED: missing sheet(s) {missing}")
    problems = verify_text_cells(OUTPUT_PATH, TEXT_COLUMNS)
    if problems:
        log.error("TEXT-CELL CHECK FAILED -- identifier columns stored as numbers: %s", problems)
    else:
        log.info("Identifier columns verified as text cells on disk.")

    _say("\n" + "=" * 60)
    _say("ROW / COLUMN COUNT SUMMARY (per tab in PowerBI_Export.xlsx)")
    _say("=" * 60)
    for name, df in sheets.items():
        _say(f"  {name:30} {len(df):>8,} rows   {len(df.columns):>3} columns")
    _say("=" * 60)
    dd = sheets["dim_device"]
    if len(dd):
        _say(f"GUDID enrichment: {100 * (dd['gudid_enriched'] == 1).mean():.1f}% of {len(dd):,} devices (any level) | "
             f"{100 * (dd['gudid_match_level'] == 'Model').mean():.1f}% model-level | {dd['match_method'].value_counts(dropna=False).to_dict()}")
        dupes = int((dd.groupby(["product_code", "brand_name_norm", "model_norm"])["device_key"].nunique() > 1).sum())
        _say(f"Duplicate device check: {dupes} (must be 0)")
        if dupes:
            log.error("DATA QUALITY REGRESSION: %s duplicate device combinations.", dupes)
    dm = sheets["dim_manufacturer"]
    n_case = int((dm["canonical_name"].str.upper().value_counts() > 1).sum())
    _say(f"Manufacturer case-duplicate check: {n_case} (must be 0)")
    if n_case:
        log.error("MANUFACTURER CASE-DUPLICATION: %s group(s).", n_case)
    return OUTPUT_PATH


if __name__ == "__main__":
    run()
