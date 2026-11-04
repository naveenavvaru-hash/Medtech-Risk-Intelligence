"""Validate a workbook:  python validate_export.py [PowerBI_Export.xlsx] [--log pipeline.log] [--json out.json]

Exit code: 0 = clean, 1 = a HIGH finding, 2 = a CRITICAL finding."""
import sys

from medtech.export.validate_export import main

if __name__ == "__main__":
    sys.exit(main())
