"""Pipeline orchestration: the ordered stages, a clean rebuild of Gold, and the quality-gate exit code."""
import importlib
import time

from medtech.common import BRONZE_DIR, GOLD_DIR, SILVER_DIR, get_logger

log = get_logger("pipeline")

DB_PATH = GOLD_DIR / "medtech_warehouse.db"
LOAD_GOLD = "medtech.gold.load_gold"
QUALITY_GATE = "medtech.export.validate_export"

# (layer, label, module) - every module exposes run()
STAGES = [
    ("bronze", "Ingest MAUDE (adverse events)", "medtech.bronze.ingest_maude"),
    ("bronze", "Ingest Recalls", "medtech.bronze.ingest_recalls"),
    ("bronze", "Ingest GUDID (device master)", "medtech.bronze.ingest_gudid"),
    ("bronze", "Ingest Classification", "medtech.bronze.ingest_classification"),
    ("silver", "Quality checks (Bronze -> Silver)", "medtech.silver.quality_checks"),
    ("gold", "Load Gold warehouse", LOAD_GOLD),
    ("gold", "Enrich manufacturer country", "medtech.gold.enrich_manufacturer_country"),
    ("gold", "Risk flags (Gold tables)", "medtech.gold.root_cause"),
    ("export", "Export to Excel", "medtech.export.export_to_powerbi"),
    ("export", "Validate export (quality gate)", QUALITY_GATE),
]
FIRST_STAGE = {"bronze": 0, "silver": 4, "gold": 5}


def status():
    """Print what each layer currently holds on disk."""
    print("=" * 66)
    bronze = list(BRONZE_DIR.glob("*.json"))
    by_source = {}
    for f in bronze:
        by_source[f.name.split("_")[0]] = by_source.get(f.name.split("_")[0], 0) + 1
    print(f"  BRONZE : {len(bronze)} files {by_source or '(empty)'}")
    silver = list(SILVER_DIR.glob("*.parquet"))
    print(f"  SILVER : {len(silver)} files {[f.stem for f in silver]}")
    print(f"  GOLD   : {'present' if DB_PATH.exists() else 'no warehouse yet'}")
    print(f"  EXPORT : {'present' if (GOLD_DIR / 'PowerBI_Export.xlsx').exists() else 'not yet written'}")
    print("=" * 66)


def reset_gold():
    """Gold is always rebuilt from Silver. Without this, rows that left the 24-month window would stay in the warehouse."""
    for path in GOLD_DIR.glob("medtech_warehouse.db*"):
        path.unlink()


def run(start="bronze"):
    """Run the stages from `start` (bronze | silver | gold) to the end. Returns the quality-gate exit code:
    0 clean, 1 a HIGH finding, 2 a CRITICAL finding (the validator's own codes)."""
    stages = STAGES[FIRST_STAGE[start]:]
    began, exit_code = time.time(), 0
    if any(module == LOAD_GOLD for _, _, module in stages):
        reset_gold()
    for layer, label, module in stages:
        log.info("=" * 70)
        log.info("STAGE [%s]: %s", layer, label)
        log.info("=" * 70)
        result = importlib.import_module(module).run()
        if module == QUALITY_GATE:
            exit_code = int(result or 0)
    log.info("Pipeline finished in %.1f seconds (quality gate exit code %s).", time.time() - began, exit_code)
    return exit_code
