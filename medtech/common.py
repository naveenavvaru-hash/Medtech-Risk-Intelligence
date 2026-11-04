"""
common.py -- shared openFDA client, logging and path constants.

v12 N1 (security): the openFDA API key is REMOVED from every log line. Failed requests used to log the full URL, including
      '&api_key=<your key>', in pipeline.log -- a file that gets shared. A logging filter on both handlers now rewrites
      api_key=... to api_key=*** (and masks the literal key value if it appears anywhere), and the validator checks the log.
v12   5xx response bodies are logged on ONE line (they used to add 19 blank-level lines to the log).
v11   one log file per run (older ones archived, last 10 kept); 404 without a search filter is an ERROR;
      openfda_paginate() logs a TRUNCATION error when an unbounded query comes back short.
"""
import os
import re
import json
import time
import shutil
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parents[1]
BRONZE_DIR = ROOT_DIR / "data" / "bronze"
SILVER_DIR = ROOT_DIR / "data" / "silver"
GOLD_DIR = ROOT_DIR / "data" / "gold"
LOG_DIR = ROOT_DIR / "data" / "_logs"
CONFIG_DIR = ROOT_DIR / "config"
LOG_FILE = LOG_DIR / "pipeline.log"
ARCHIVE_DIR = LOG_DIR / "archive"

for _d in (BRONZE_DIR, SILVER_DIR, GOLD_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

RUN_ID = datetime.now().strftime("%Y%m%d_%H%M%S")

_KEY_RE = re.compile(r"(?i)(api[_-]?key=)[^&\s'\")\]]+")


def redact_secrets(text):
    """Mask the openFDA API key in any text: 'api_key=<anything>' -> 'api_key=***', and the literal key value -> '***'."""
    if not isinstance(text, str):
        return text
    text = _KEY_RE.sub(r"\1***", text)
    key = os.getenv("OPENFDA_API_KEY", "")
    if key and len(key) >= 8:
        text = text.replace(key, "***")
    return text


class RedactingFilter(logging.Filter):
    """Rewrites the message of every record so a secret can never reach a log file or the console."""

    def filter(self, record):
        try:
            msg = record.getMessage()
        except Exception:      # noqa: BLE001 - never let logging crash the pipeline
            return True
        clean = redact_secrets(msg)
        if clean != msg:
            record.msg, record.args = clean, ()
        return True


def _rotate_log():
    """Move the previous run's log into the archive so every run starts with a clean file."""
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 0:
            ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.fromtimestamp(LOG_FILE.stat().st_mtime).strftime("%Y%m%d_%H%M%S")
            target = ARCHIVE_DIR / f"pipeline_{stamp}.log"
            n = 1
            while target.exists():
                target = ARCHIVE_DIR / f"pipeline_{stamp}_{n}.log"
                n += 1
            shutil.move(str(LOG_FILE), str(target))
        if ARCHIVE_DIR.exists():
            for old in sorted(ARCHIVE_DIR.glob("pipeline_*.log"))[:-10]:
                old.unlink()
    except OSError:
        pass  # a locked file must never stop the pipeline


_rotate_log()

_handlers = [logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()]
for _h in _handlers:
    _h.addFilter(RedactingFilter())

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", handlers=_handlers)


def get_logger(name):
    return logging.getLogger(name)


get_logger("run").info("RUN START run_id=%s root=%s", RUN_ID, ROOT_DIR)

OPENFDA_BASE = "https://api.fda.gov"
OPENFDA_API_KEY = os.getenv("OPENFDA_API_KEY", "")
MAX_LIMIT = 1000
MAX_SKIP = 25000
_last_request_ts = 0.0


class OpenFDARateLimitError(RuntimeError):
    pass


class OpenFDABadRequestError(RuntimeError):
    pass


def _throttle():
    global _last_request_ts
    min_interval = 0.3 if OPENFDA_API_KEY else 1.2
    elapsed = time.monotonic() - _last_request_ts
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_request_ts = time.monotonic()


def openfda_get(endpoint, search="", limit=MAX_LIMIT, skip=0, extra_params=None, max_retries=6):
    url = f"{OPENFDA_BASE}/{endpoint}"
    params = {"limit": min(limit, MAX_LIMIT), "skip": skip}
    if search:
        params["search"] = search
    if OPENFDA_API_KEY:
        params["api_key"] = OPENFDA_API_KEY
    if extra_params:
        params.update(extra_params)
    log = get_logger("openfda_get")
    for attempt in range(1, max_retries + 1):
        _throttle()
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 404:
                if not search:
                    log.error("openFDA returned 404 for %s with NO search filter -- normally a wrong endpoint, "
                              "not 'no data'.", endpoint)
                return {"meta": {"results": {"total": 0}}, "results": []}
            if resp.status_code == 400:
                log.warning("openFDA 400 Bad Request (not retrying): %s | body: %s", resp.url, " ".join(resp.text.split())[:300])
                raise OpenFDABadRequestError(f"Malformed query for {url}: {resp.text[:300]}")
            if resp.status_code in (403, 429):
                wait = min(5 * attempt, 60)
                log.warning("openFDA returned %s on attempt %s/%s. Waiting %ss.", resp.status_code, attempt, max_retries, wait)
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                log.warning("openFDA 5xx body (attempt %s): %s", attempt, " ".join(resp.text.split())[:300])
            resp.raise_for_status()
            return resp.json()
        except OpenFDABadRequestError:
            raise
        except requests.exceptions.RequestException as exc:
            wait = min(2 ** attempt, 30)
            log.warning("openFDA request failed (attempt %s/%s): %s. Retrying in %ss", attempt, max_retries, exc, wait)
            time.sleep(wait)
    raise OpenFDARateLimitError(f"openFDA request permanently failed for {url} after {max_retries} attempts.")


def openfda_paginate(endpoint, search="", max_records=None, extra_params=None):
    log = get_logger("openfda_paginate")
    skip, fetched = 0, 0
    first_page = openfda_get(endpoint, search=search, limit=1, skip=0, extra_params=extra_params)
    total = first_page.get("meta", {}).get("results", {}).get("total", 0)
    log.info("Query '%s' on %s matches %s total records", search or "*", endpoint, total)
    if total == 0:
        return
    while skip <= MAX_SKIP and (max_records is None or fetched < max_records):
        page = openfda_get(endpoint, search=search, limit=MAX_LIMIT, skip=skip, extra_params=extra_params)
        results = page.get("results", [])
        if not results:
            break
        for r in results:
            yield r
            fetched += 1
            if max_records is not None and fetched >= max_records:
                return
        skip += MAX_LIMIT
        time.sleep(0.2)
    if max_records is None and fetched < total:
        log.error("TRUNCATION: query '%s' on %s matched %s total records but only %s were fetched (%s missing, %.1f%% coverage). "
                  "Split the query into slices under openFDA's skip cap (%s) -- see ingest_gudid._fetch_chunked().",
                  search or "*", endpoint, total, fetched, total - fetched, (100.0 * fetched / total) if total else 0.0, MAX_SKIP)


def land_bronze(records, source_name):
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = BRONZE_DIR / f"{source_name}_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False)
    get_logger("land_bronze").info("Landed %s records to %s", len(records), out_path)
    return out_path
