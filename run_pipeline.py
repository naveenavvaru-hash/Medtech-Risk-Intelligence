"""Run the whole pipeline (press the Run button in VS Code, or:  python run_pipeline.py).

    python run_pipeline.py                  download -> Silver -> Gold -> Excel -> validate
    python run_pipeline.py --from silver    re-clean the Bronze files already downloaded, then everything after
    python run_pipeline.py --from gold      rebuild the warehouse and the workbook from the existing Silver files
    python run_pipeline.py --status         show what each layer currently holds

Exit code: 0 = clean (or only MEDIUM / LOW findings), 1 = a HIGH finding, 2 = a CRITICAL finding in the quality gate.
This file sits in the project root, so Python finds the `medtech` package next to it: no setup and no `python -m` needed.
"""
import argparse
import sys

LAYERS = ("bronze", "silver", "gold")


def build_parser(default_start="bronze", layers=LAYERS):
    ap = argparse.ArgumentParser(description="MedTech Risk & Market Intelligence pipeline")
    ap.add_argument("--from", dest="start", choices=layers, default=default_start,
                    help="bronze = download + everything after; silver = re-clean the downloaded data; "
                         "gold = rebuild the warehouse and the export from the existing Silver files (default: %(default)s)")
    ap.add_argument("--status", action="store_true", help="only show what each layer holds")
    return ap


def main(argv=None, default_start="bronze", layers=LAYERS):
    args = build_parser(default_start, layers).parse_args(argv)
    from medtech import pipeline          # imported after the arguments, so --help has no side effects
    pipeline.status()
    if args.status:
        return 0
    return pipeline.run(args.start)


if __name__ == "__main__":
    sys.exit(main())
