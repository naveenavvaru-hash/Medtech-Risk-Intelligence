"""
ingest_maude.py -- FDA MAUDE adverse events: 24 COMPLETE calendar months, 3-month slices, one query per product code.
The window starts on the 1st of a month and ends on the last day of the previous month (the current month is still filling).
Slices do not overlap (each ends the day before the next one starts).
"""
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from medtech.common import openfda_paginate, land_bronze, get_logger, OpenFDARateLimitError

log = get_logger("ingest_maude")
ENDPOINT = "device/event.json"

PRODUCT_CODES = ["DXY", "QJP", "MAX", "HCG", "ODP"]
PRODUCT_CODE_CATEGORY = {"DXY": "Cardiovascular", "QJP": "Neurovascular", "MAX": "Orthopedic",
                         "HCG": "Neurovascular", "ODP": "Orthopedic"}
LOOKBACK_MONTHS = 24
WINDOW_MONTHS = 3


def complete_month_window(today=None, months=LOOKBACK_MONTHS):
    """(first day of the month `months` ago, last day of the previous month) -- complete calendar months only."""
    today = today or date.today()
    this_month = today.replace(day=1)
    return this_month - relativedelta(months=months), this_month - timedelta(days=1)


START_DATE, END_DATE = complete_month_window()


def window_ranges(start, end, months=WINDOW_MONTHS):
    """Non-overlapping inclusive (window_start, window_end) pairs covering start..end."""
    cur = start
    while cur <= end:
        win_end = min(cur + relativedelta(months=months) - timedelta(days=1), end)
        yield cur, win_end
        cur = win_end + timedelta(days=1)


def build_search(product_code, start, end):
    return (f'device.device_report_product_code:"{product_code}" '
            f'AND date_received:[{start.strftime("%Y%m%d")} TO {end.strftime("%Y%m%d")}]')


def run():
    log.info("MAUDE window: %s .. %s (complete calendar months)", START_DATE, END_DATE)
    landed, skipped = [], []
    for code in PRODUCT_CODES:
        for w_start, w_end in window_ranges(START_DATE, END_DATE):
            log.info("Pulling MAUDE product_code=%s window=%s..%s", code, w_start, w_end)
            try:
                records = list(openfda_paginate(ENDPOINT, search=build_search(code, w_start, w_end)))
            except OpenFDARateLimitError as exc:
                log.error("Skipping slice %s %s..%s: %s", code, w_start, w_end, exc)
                skipped.append((code, w_start, w_end))
                continue
            if records:
                landed.append(str(land_bronze(records, f"maude_{code}_{w_start:%Y%m%d}_{w_end:%Y%m%d}")))
    log.info("MAUDE ingestion complete. %s slices landed, %s skipped.", len(landed), len(skipped))
    return landed


if __name__ == "__main__":
    run()
