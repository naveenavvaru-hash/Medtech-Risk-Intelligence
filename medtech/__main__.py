"""Alternative command line (the root scripts run_pipeline.py / resume_pipeline.py / validate_export.py do the same).

    python -m medtech run [--from bronze|silver|gold]    run the pipeline (default: everything, starting with the openFDA download)
    python -m medtech status                             what each layer currently holds
    python -m medtech validate [export.xlsx] [--log pipeline.log] [--json out.json]

Exit code of `run` and `validate`: 0 clean, 1 a HIGH finding, 2 a CRITICAL finding.
"""
import argparse
import sys


def build_parser():
    ap = argparse.ArgumentParser(prog="python -m medtech", description="MedTech Risk & Market Intelligence pipeline")
    sub = ap.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the pipeline")
    run.add_argument("--from", dest="start", choices=["bronze", "silver", "gold"], default="bronze",
                     help="bronze = download + everything after (default); silver = re-clean the downloaded data; "
                          "gold = rebuild the warehouse and the export from the existing Silver files")
    sub.add_parser("status", help="show what each layer holds")
    val = sub.add_parser("validate", help="validate a PowerBI_Export.xlsx (standalone quality gate)")
    val.add_argument("export", nargs="?", help="path to the workbook (default: data/gold/PowerBI_Export.xlsx)")
    val.add_argument("--log", metavar="PATH", help="also scan this pipeline.log for credentials")
    val.add_argument("--json", metavar="PATH", help="write the findings as JSON")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "validate":                 # imported lazily: the validator does not need the pipeline's logging setup
        from medtech.export import validate_export
        return validate_export.validate(args.export or str(validate_export.DEFAULT_EXPORT), json_path=args.json, log_path=args.log)
    from medtech import pipeline
    pipeline.status()
    if args.command == "status":
        return 0
    return pipeline.run(args.start)


if __name__ == "__main__":
    sys.exit(main())
