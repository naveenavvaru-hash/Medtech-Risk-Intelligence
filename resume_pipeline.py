"""Resume from an existing layer:  python resume_pipeline.py [--from silver|gold] [--status]

Same as `python run_pipeline.py --from silver` (default) or `--from gold`, without the download."""
import sys

from run_pipeline import main

if __name__ == "__main__":
    sys.exit(main(default_start="silver", layers=("silver", "gold")))
