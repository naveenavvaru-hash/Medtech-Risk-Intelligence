"""
root_cause.py -- monthly risk velocity and control-chart flags per category / manufacturer (and per parent company).

  FLG-01  PARTIAL MONTHS are excluded (first month must start within COMPLETE_MONTH_SLACK_DAYS of the 1st, last month must reach within that
          slack of the month end); excluded months are neither tested, nor a baseline month, nor the 'previous month' of the next one.
  FLG-02  category_z_score on every row, a WATCH tier (z >= 2 but not a breach), a ROLLING baseline of the last 12 months (min 6 months).
  FLG-03  velocity_direction (Rise / Fall); drops are 'Velocity drop' and are not called a spike.
  FLG-04  second table root_cause_flags_parent computes the same flags per PARENT COMPANY.
  FLG-05  keyword lists drop generic words and any word found in > KEYWORD_MAX_DOC_SHARE of all narratives.
  Velocity compares with the previous CALENDAR month (blank when that month is missing); sample standard deviation; is_low_history.
  The 'Unknown' manufacturer is never flagged (it still counts towards the category totals).
"""
import calendar
import re
import sqlite3
from collections import Counter
from datetime import datetime

import numpy as np
import pandas as pd

from medtech.common import GOLD_DIR, get_logger
from medtech.silver.narrative_clean import truncate_at_word

log = get_logger("root_cause")
DB_PATH = GOLD_DIR / "medtech_warehouse.db"
# The two results are Gold-layer TABLES in the warehouse (they used to be CSV files). The Excel export reads them from there.
FLAG_TABLES = {"manufacturer": "root_cause_flags", "parent": "root_cause_flags_parent"}

VELOCITY_THRESHOLD_PCT = 75.0
MIN_EVENTS_FOR_VELOCITY_FLAG = 3
MIN_PREV_MONTH_COUNT = 3
MIN_HISTORY_MONTHS = 6
BASELINE_WINDOW_MONTHS = 12
WATCH_Z = 2.0
COMPLETE_MONTH_SLACK_DAYS = 3
SAMPLE_NARRATIVES = 3
TOP_KEYWORDS = 8
KEYWORD_MAX_DOC_SHARE = 0.15
COUNT_COLS = ["event_count", "literature_event_count", "probable_duplicate_count", "possible_duplicate_count"]

STOPWORDS = set("""
the a an and or of to in on for was were is are with this that it as by at not no be been have has had from which
would could should their there they them then than also into over under about after before during between
device devices reported report reports patient patients event events complaint product products information additional
medtronic abbott boston scientific manufacturer manufacturing received provided investigation review available unknown
further analysis returned evaluation noted occurred indicated stated remains remain used use using time date
redacted regarding representative performed procedure complications will were when while what where after other
""".split())


def _get_connection():
    if not DB_PATH.exists():
        raise SystemExit(f"Gold database not found at {DB_PATH}. Run `python run_pipeline.py` first.")
    return sqlite3.connect(DB_PATH)


# ------------------------------------------------------------------ complete months
def month_index(y, m):
    return int(y) * 12 + int(m) - 1


def complete_month_range(min_date, max_date, slack=COMPLETE_MONTH_SLACK_DAYS):
    """(first, last) month indices whose data coverage is complete, or None."""
    first = month_index(min_date.year, min_date.month)
    if min_date.day > 1 + slack:
        first += 1
    last = month_index(max_date.year, max_date.month)
    if max_date.day < calendar.monthrange(max_date.year, max_date.month)[1] - slack:
        last -= 1
    return (first, last) if first <= last else None


# ------------------------------------------------------------------ baseline
def fill_calendar_gaps(df, group_cols, count_cols):
    """Insert a zero row for every calendar month between a group's first and last month with data."""
    if df.empty:
        return df
    d = df.copy()
    d["_mi"] = d["yr"].astype(int) * 12 + d["mo"].astype(int) - 1
    pieces = []
    for keys, g in d.groupby(group_cols, sort=False):
        keys = keys if isinstance(keys, tuple) else (keys,)
        g = g.set_index("_mi")
        g = g.reindex(pd.RangeIndex(int(g.index.min()), int(g.index.max()) + 1))
        for c, k in zip(group_cols, keys):
            g[c] = k
        for c in count_cols:
            g[c] = g[c].fillna(0).astype(int)
        g["yr"] = (g.index // 12).astype(int)
        g["mo"] = (g.index % 12 + 1).astype(int)
        pieces.append(g.reset_index(drop=True))
    return pd.concat(pieces, ignore_index=True)


def category_baseline(cat_monthly, min_history=MIN_HISTORY_MONTHS, window=BASELINE_WINDOW_MONTHS):
    """Rolling baseline per category and calendar month from the PRIOR `window` months (sample stdev).
    Breach = count above mean + 3 sd with >= min_history prior months; Watch = z >= WATCH_Z, not a breach."""
    out = []
    for _, g in cat_monthly.sort_values(["category", "yr", "mo"]).groupby("category"):
        counts = g["category_event_count"].to_numpy(float)
        n = len(counts)
        mean, sd, hist = np.full(n, np.nan), np.full(n, np.nan), np.zeros(n, dtype=int)
        for i in range(n):
            prior = counts[max(0, i - window):i]
            hist[i] = len(prior)
            if len(prior) >= 1:
                mean[i] = prior.mean()
            if len(prior) >= 2:
                sd[i] = prior.std(ddof=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            ucl = mean + 3 * sd
            z = np.where(sd > 0, (counts - mean) / sd, np.nan)
            enough = hist >= min_history
            breach = enough & (counts > ucl)
            watch = enough & ~breach & (np.nan_to_num(z, nan=-99.0) >= WATCH_Z)
        h = g.copy()
        h["trailing_mean"], h["trailing_stdev"], h["trailing_ucl"] = mean, sd, ucl
        h["trailing_history_months"] = hist
        h["category_z_score"] = np.where(enough, z, np.nan)       # a z-score on < min_history prior months is not meaningful
        h["is_category_breach"], h["is_category_watch"] = breach.astype(int), watch.astype(int)
        out.append(h)
    return pd.concat(out, ignore_index=True)


_TAGS = [("is_statistical_breach", "STATISTICAL_BREACH", "Statistical breach"),
         ("is_statistical_watch", "STATISTICAL_WATCH", "Statistical watch"),
         ("is_velocity_spike", "VELOCITY_SPIKE", "Velocity spike"),
         ("is_velocity_drop", "VELOCITY_DROP", "Velocity drop")]


def compute_flags(monthly_counts, gcols=("category", "manufacturer", "mfr_key"), complete=None, exclude=None):
    """monthly_counts: one row per group / month WITH events (columns: gcols, yr, mo, COUNT_COLS).
    Returns (flagged, all_months, suppressed). `complete` = (first, last) month indices to keep; `exclude` = group names that are never
    flagged / never attributed a breach (they still count towards the category totals)."""
    gcols, exclude = list(gcols), set(exclude or [])
    name_col = gcols[1]
    mc = monthly_counts.copy()
    if complete is not None and not mc.empty:
        mi = mc["yr"].astype(int) * 12 + mc["mo"].astype(int) - 1
        mc = mc[(mi >= complete[0]) & (mi <= complete[1])]
    if mc.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    monthly = fill_calendar_gaps(mc, gcols, COUNT_COLS).sort_values(gcols + ["yr", "mo"]).reset_index(drop=True)
    monthly["prev_month_count"] = monthly.groupby(gcols)["event_count"].shift(1)
    prev, ev = monthly["prev_month_count"].to_numpy(float), monthly["event_count"].to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        monthly["risk_velocity_pct"] = np.round(np.where(prev > 0, (ev - prev) / prev * 100.0, np.nan), 1)

    cat_monthly = monthly.groupby(["category", "yr", "mo"], as_index=False)["event_count"].sum().rename(columns={"event_count": "category_event_count"})
    stats = category_baseline(fill_calendar_gaps(cat_monthly, ["category"], ["category_event_count"]))

    monthly["is_statistical_breach"], monthly["is_statistical_watch"] = 0, 0
    eligible = ~monthly[name_col].isin(exclude)
    for col, flag in (("is_statistical_breach", "is_category_breach"), ("is_statistical_watch", "is_category_watch")):
        for _, p in stats[stats[flag] == 1][["category", "yr", "mo"]].iterrows():
            mask = (monthly["category"] == p["category"]) & (monthly["yr"] == p["yr"]) & (monthly["mo"] == p["mo"]) & eligible & (monthly["event_count"] > 0)
            if mask.any():
                monthly.loc[monthly.loc[mask, "event_count"].idxmax(), col] = 1            # top contributor
    monthly = monthly.merge(stats[["category", "yr", "mo", "category_event_count", "trailing_mean", "trailing_stdev", "trailing_ucl",
                                   "trailing_history_months", "category_z_score"]], on=["category", "yr", "mo"], how="left")

    big = (monthly["event_count"] >= MIN_EVENTS_FOR_VELOCITY_FLAG) & (monthly["prev_month_count"] >= MIN_PREV_MONTH_COUNT)
    monthly["is_velocity_spike"] = (big & (monthly["risk_velocity_pct"] >= VELOCITY_THRESHOLD_PCT)).astype(int)
    monthly["is_velocity_drop"] = (big & (monthly["risk_velocity_pct"] <= -VELOCITY_THRESHOLD_PCT)).astype(int)
    monthly["velocity_direction"] = np.where(monthly["risk_velocity_pct"] > 0, "Rise", np.where(monthly["risk_velocity_pct"] < 0, "Fall", None))
    monthly["is_low_history"] = (monthly["trailing_history_months"] < MIN_HISTORY_MONTHS).astype(int)

    def tags(row, idx):
        return [t[idx] for t in _TAGS if row[t[0]]]

    monthly["flag_reason"] = monthly.apply(lambda r: " + ".join(tags(r, 1)), axis=1)
    monthly["flag_label"] = monthly.apply(lambda r: (lambda p: (p[0] + "".join(" + " + x.lower() for x in p[1:])) if p else "")(tags(r, 2)), axis=1)

    any_flag = monthly[[t[0] for t in _TAGS]].sum(axis=1) > 0
    flagged = monthly[any_flag & eligible].sort_values(["yr", "mo"], ascending=False).copy()
    suppressed = stats[(stats["category_event_count"] > stats["trailing_ucl"]) & (stats["trailing_history_months"] < MIN_HISTORY_MONTHS)]
    return flagged, monthly, suppressed[["category", "yr", "mo", "category_event_count", "trailing_ucl", "trailing_history_months"]]


# ------------------------------------------------------------------ narratives and keywords
def keyword_stopwords(conn, max_share=KEYWORD_MAX_DOC_SHARE):
    """Generic words + any word that appears in more than `max_share` of all narratives (template text)."""
    texts = [r[0] for r in conn.execute("SELECT narrative_text FROM fact_events WHERE narrative_text IS NOT NULL AND narrative_text != ''")]
    if not texts:
        return set(STOPWORDS)
    df = Counter()
    for t in texts:
        df.update(set(re.findall(r"[a-z]{4,}", t.lower())))
    return set(STOPWORDS) | {w for w, c in df.items() if c / len(texts) > max_share}


def _drill_through_narratives(conn, mfr_keys, category, year, month, stop):
    marks = ",".join("?" * len(mfr_keys))
    rows = conn.execute(f"""
        SELECT fe.narrative_text FROM fact_events fe
        JOIN dim_date dt ON fe.date_key = dt.date_key JOIN dim_device dd ON fe.device_key = dd.device_key
        WHERE fe.mfr_key IN ({marks}) AND dd.category = ? AND dt.year = ? AND dt.month = ?
              AND fe.narrative_text IS NOT NULL AND fe.narrative_text != '' ORDER BY fe.event_key
    """, (*mfr_keys, category, year, month)).fetchall()
    narratives = [r[0] for r in rows]
    result = {"top_keywords": "", "narrative_count": len(narratives), **{f"sample_narrative_{i + 1}": "" for i in range(SAMPLE_NARRATIVES)}}
    if not narratives:
        return result
    words = [w for t in narratives for w in re.findall(r"[a-zA-Z]{4,}", t.lower()) if w not in stop]
    result["top_keywords"] = ", ".join(w for w, _ in Counter(words).most_common(TOP_KEYWORDS))
    for i, t in enumerate(narratives[:SAMPLE_NARRATIVES]):
        result[f"sample_narrative_{i + 1}"] = truncate_at_word(t, 300)
    return result


# ------------------------------------------------------------------ output
BASE_COLS = ["category", "manufacturer", "mfr_key", "yr", "mo", "event_count", "prev_month_count", "risk_velocity_pct",
             "is_statistical_breach", "category_event_count", "trailing_mean", "trailing_stdev", "trailing_ucl",
             "trailing_history_months", "flag_reason"]
NEW_COLS = ["risk_velocity_ratio", "velocity_direction", "is_velocity_spike", "is_velocity_drop", "is_statistical_watch",
            "category_z_score", "is_low_history", "flag_label", "literature_event_count", "probable_duplicate_count",
            "possible_duplicate_count"]
TAIL_COLS = ["top_keywords", "narrative_count", "sample_narrative_1", "sample_narrative_2", "sample_narrative_3"]


def output_columns(level="manufacturer"):
    cols = BASE_COLS + NEW_COLS + TAIL_COLS
    if level == "parent":
        cols = ["parent_company" if c == "manufacturer" else c for c in cols if c != "mfr_key"]
    return cols


def finalize_columns(df, level="manufacturer"):
    """Column order, typing and rounding of the output table (also for an empty result)."""
    cols = output_columns(level)
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    df = df.copy()
    if level == "parent":
        df = df.rename(columns={"manufacturer": "parent_company"})
    df["risk_velocity_ratio"] = (df["risk_velocity_pct"] / 100.0).round(4)
    df["category_z_score"] = df["category_z_score"].round(2)
    for c in ("prev_month_count", "trailing_history_months"):
        df[c] = df[c].astype("Int64")
    ints = ["event_count", "category_event_count", "is_statistical_breach", "is_statistical_watch", "is_velocity_spike",
            "is_velocity_drop", "is_low_history", "literature_event_count", "probable_duplicate_count", "possible_duplicate_count", "yr", "mo"]
    if level != "parent":
        ints.append("mfr_key")
    for c in ints:
        df[c] = df[c].astype(int)
    for c in ("trailing_mean", "trailing_stdev", "trailing_ucl"):
        df[c] = df[c].round(2)
    for c in TAIL_COLS:
        if c not in df.columns:
            df[c] = None
    return df[cols]


def _month_bounds(conn):
    row = conn.execute("SELECT MIN(dt.full_date), MAX(dt.full_date) FROM fact_events fe JOIN dim_date dt ON fe.date_key = dt.date_key").fetchone()
    if not row or row[0] is None:
        return None, None
    return datetime.strptime(row[0], "%Y-%m-%d").date(), datetime.strptime(row[1], "%Y-%m-%d").date()


def _monthly_counts(conn):
    return pd.read_sql_query("""
        SELECT dd.category AS category, dm.canonical_name AS manufacturer, dm.mfr_key AS mfr_key, dm.parent_company AS parent_company,
               COALESCE(dm.is_placeholder_entity, 0) AS is_placeholder, dt.year AS yr, dt.month AS mo, COUNT(*) AS event_count,
               SUM(fe.is_literature_report) AS literature_event_count, SUM(fe.is_probable_duplicate) AS probable_duplicate_count,
               SUM(fe.is_possible_duplicate) AS possible_duplicate_count
        FROM fact_events fe JOIN dim_device dd ON fe.device_key = dd.device_key JOIN dim_manufacturer dm ON fe.mfr_key = dm.mfr_key
        JOIN dim_date dt ON fe.date_key = dt.date_key
        GROUP BY dd.category, dm.canonical_name, dm.mfr_key, dm.parent_company, dm.is_placeholder_entity, dt.year, dt.month""", conn)


def build_flag_tables(conn):
    """Returns (manufacturer_flags, parent_flags) as DataFrames before narrative enrichment."""
    counts = _monthly_counts(conn)
    if counts.empty:
        return pd.DataFrame(), pd.DataFrame()
    lo, hi = _month_bounds(conn)
    complete = complete_month_range(lo, hi)
    if complete is None:
        log.error("No complete calendar month in the event data (%s..%s) -- no flags computed.", lo, hi)
        return pd.DataFrame(), pd.DataFrame()
    first_ok, last_ok = complete
    all_months = sorted({month_index(y, m) for y, m in zip(counts["yr"], counts["mo"])})
    dropped = [f"{i // 12}-{i % 12 + 1:02d}" for i in all_months if i < first_ok or i > last_ok]
    log.info("Event data %s .. %s. Complete months used for flags: %s-%02d .. %s-%02d. Partial months excluded from baselines and from "
             "'previous month' comparisons: %s", lo, hi, first_ok // 12, first_ok % 12 + 1, last_ok // 12, last_ok % 12 + 1, dropped or "none")
    placeholders = set(counts.loc[counts["is_placeholder"] == 1, "manufacturer"])
    m_cols = ["category", "manufacturer", "mfr_key", "yr", "mo"] + COUNT_COLS
    m_flagged, m_all, m_sup = compute_flags(counts[m_cols], complete=complete, exclude=placeholders)
    parent = counts.groupby(["category", "parent_company", "yr", "mo"], as_index=False)[COUNT_COLS].sum().rename(columns={"parent_company": "manufacturer"})
    p_flagged, p_all, _ = compute_flags(parent, gcols=("category", "manufacturer"), complete=complete, exclude={"Unknown"})
    n_m = int((m_all["event_count"] > 0).sum()) if not m_all.empty else 0
    n_p = int((p_all["event_count"] > 0).sum()) if not p_all.empty else 0
    log.info("Manufacturer level: flagged %s of %s manufacturer-months (%s statistical breach, %s statistical watch, %s velocity spike, %s velocity drop).",
             len(m_flagged), n_m, int(m_all["is_statistical_breach"].sum()), int(m_all["is_statistical_watch"].sum()),
             int(m_all["is_velocity_spike"].sum()), int(m_all["is_velocity_drop"].sum()))
    log.info("Parent-company level: flagged %s of %s parent-months (%s statistical breach, %s statistical watch, %s velocity spike, %s velocity drop).",
             len(p_flagged), n_p, int(p_all["is_statistical_breach"].sum()), int(p_all["is_statistical_watch"].sum()),
             int(p_all["is_velocity_spike"].sum()), int(p_all["is_velocity_drop"].sum()))
    if not m_all.empty:
        cat = m_all.drop_duplicates(["category", "yr", "mo"]).dropna(subset=["category_z_score"]).sort_values("category_z_score", ascending=False).head(3)
        log.info("Highest category z-scores (>= %.1f is WATCH, count above mean + 3 sd is a BREACH): %s", WATCH_Z,
                 "; ".join(f"{r.category} {int(r.yr)}-{int(r.mo):02d} z={r.category_z_score:.2f}" for r in cat.itertuples()))
    if not m_sup.empty:
        sup = m_sup.drop_duplicates()
        log.warning("%s category-month(s) exceeded the UCL but had fewer than %s prior months (do not read as 'no breach'):\n%s",
                    len(sup), MIN_HISTORY_MONTHS, sup.to_string(index=False))
    return m_flagged, p_flagged


def write_flag_table(conn, table, df):
    """Gold layer: store a flag table in the warehouse, replacing the previous run's table.
    Empty text becomes NULL, so a blank cell stays blank in the Excel export (the CSV round trip did the same)."""
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == object:
            out.loc[out[col] == "", col] = None
    out.to_sql(table, conn, if_exists="replace", index=False)
    conn.commit()


def run():
    conn = _get_connection()
    m_flagged, p_flagged = build_flag_tables(conn)
    stop = keyword_stopwords(conn)
    parent_keys = {}
    for key, parent in conn.execute("SELECT mfr_key, parent_company FROM dim_manufacturer"):
        parent_keys.setdefault(parent, []).append(key)
    out = {}
    for level, flagged in (("manufacturer", m_flagged), ("parent", p_flagged)):
        if flagged is None or flagged.empty:
            out[level] = finalize_columns(None, level)
        else:
            drill = []
            for _, r in flagged.iterrows():
                keys = [int(r["mfr_key"])] if level == "manufacturer" else parent_keys.get(r["manufacturer"], [])
                drill.append(_drill_through_narratives(conn, keys or [-1], r["category"], int(r["yr"]), int(r["mo"]), stop))
            out[level] = finalize_columns(pd.concat([flagged.reset_index(drop=True), pd.DataFrame(drill)], axis=1), level)
        write_flag_table(conn, FLAG_TABLES[level], out[level])
        log.info("Wrote %s flagged rows (%s level) to table %s in %s", len(out[level]), level, FLAG_TABLES[level], DB_PATH.name)
    conn.close()
    return out["manufacturer"]


if __name__ == "__main__":
    df = run()
    if not df.empty:
        print(df[["category", "manufacturer", "yr", "mo", "event_count", "prev_month_count", "risk_velocity_pct", "category_z_score", "flag_label"]].to_string(index=False))
