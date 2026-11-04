"""
ingest_gudid.py -- GUDID device registry. Codes larger than openFDA's skip cap (MAX 174,971; ODP 55,733) are fetched by
recursive date-range bisection on publish_date so every slice stays under the cap. Coverage < 99.9% is an ERROR.
"""
from datetime import date, timedelta

from medtech.common import openfda_get, openfda_paginate, land_bronze, get_logger, MAX_SKIP, OpenFDARateLimitError
from medtech.bronze.ingest_maude import PRODUCT_CODES

log = get_logger("ingest_gudid")
ENDPOINT = "device/udi.json"
SAFE_CHUNK_LIMIT = 18000
GUDID_EARLIEST_DATE = date(2010, 1, 1)
MAX_RECURSION_DEPTH = 24


def build_search(product_code, start=None, end=None):
    base = f'product_codes.code:"{product_code}"'
    if start is not None and end is not None:
        base += f' AND publish_date:[{start.strftime("%Y-%m-%d")} TO {end.strftime("%Y-%m-%d")}]'
    return base


def _count_only(search):
    return openfda_get(ENDPOINT, search=search, limit=1, skip=0).get("meta", {}).get("results", {}).get("total", 0)


def _fetch_chunked(product_code, start, end, depth=0):
    search = build_search(product_code, start, end)
    total = _count_only(search)
    if total == 0:
        return []
    if total <= SAFE_CHUNK_LIMIT or depth >= MAX_RECURSION_DEPTH or start >= end:
        if total > SAFE_CHUNK_LIMIT:
            log.warning("GUDID chunk %s [%s..%s] still has %s records after %s splits -- fetching anyway.", product_code, start, end, total, depth)
        records = list(openfda_paginate(ENDPOINT, search=search))
        log.info("  GUDID chunk %s [%s..%s]: matched %s, fetched %s", product_code, start, end, total, len(records))
        return records
    mid = start + (end - start) // 2
    log.info("GUDID %s: %s records in [%s..%s] exceeds safe limit %s -- splitting at %s", product_code, total, start, end, SAFE_CHUNK_LIMIT, mid)
    left = _fetch_chunked(product_code, start, mid, depth + 1)
    right_start = mid + timedelta(days=1)
    return left if right_start > end else left + _fetch_chunked(product_code, right_start, end, depth + 1)


def run():
    landed, skipped = [], []
    today = date.today()
    for code in PRODUCT_CODES:
        log.info("Pulling GUDID device records for product_code=%s", code)
        try:
            true_total = _count_only(build_search(code))
            if true_total > SAFE_CHUNK_LIMIT:
                log.info("product_code=%s has %s GUDID records (> safe limit %s, openFDA cap %s) -- date-range chunking.", code, true_total, SAFE_CHUNK_LIMIT, MAX_SKIP)
                records = _fetch_chunked(code, GUDID_EARLIEST_DATE, today)
            else:
                records = list(openfda_paginate(ENDPOINT, search=build_search(code)))
        except OpenFDARateLimitError as exc:
            log.error("Skipping product_code=%s: %s", code, exc)
            skipped.append(code)
            continue
        before, seen, deduped = len(records), set(), []
        for r in records:
            key = r.get("public_device_record_key")
            if key in seen:
                continue
            seen.add(key)
            deduped.append(r)
        if before != len(deduped):
            log.warning("product_code=%s: removed %s duplicate GUDID record(s) after chunking.", code, before - len(deduped))
        records = deduped
        coverage = (100.0 * len(records) / true_total) if true_total else 100.0
        if coverage < 99.9:
            log.error("product_code=%s: only %s of %s GUDID records captured (%.1f%%).", code, len(records), true_total, coverage)
        else:
            log.info("product_code=%s: captured %s of %s GUDID records (%.1f%% coverage).", code, len(records), true_total, coverage)
        if records:
            landed.append(str(land_bronze(records, f"gudid_{code}")))
    log.info("GUDID ingestion complete. %s product codes landed, %s skipped.", len(landed), len(skipped))
    return landed


if __name__ == "__main__":
    run()
