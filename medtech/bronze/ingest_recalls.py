from medtech.common import openfda_paginate, land_bronze, get_logger, OpenFDARateLimitError
from medtech.bronze.ingest_maude import PRODUCT_CODES

log = get_logger("ingest_recalls")
ENDPOINT = "device/recall.json"


def build_search(product_code):
    return f'product_code:"{product_code}"'


def run():
    landed, skipped = [], []
    for code in PRODUCT_CODES:
        log.info("Pulling recalls for product_code=%s", code)
        try:
            records = list(openfda_paginate(ENDPOINT, search=build_search(code)))
        except OpenFDARateLimitError as exc:
            log.error("Skipping product_code=%s: %s", code, exc)
            skipped.append(code)
            continue
        if records:
            landed.append(str(land_bronze(records, f"recalls_{code}")))
    log.info("Recall ingestion complete. %s landed, %s skipped.", len(landed), len(skipped))
    return landed


if __name__ == "__main__":
    run()
