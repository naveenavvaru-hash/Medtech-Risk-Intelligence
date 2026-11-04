"""MedTech Risk & Market Intelligence pipeline.

openFDA (MAUDE adverse events, recalls, GUDID, device classification)
    -> bronze (raw JSON)  -> silver (cleaned parquet)  -> gold (SQLite star schema)  -> export (PowerBI_Export.xlsx)

Layers:  medtech.bronze  medtech.silver  medtech.gold  medtech.export
Shared:  medtech.common (paths, logging, openFDA client)  text_normalize  country_normalize  reference_data
Run:     python run_pipeline.py
"""

__version__ = "12.1.0"
