"""
enrich_manufacturer_country.py -- fill dim_manufacturer.country for manufacturers MAUDE / recalls did not cover.

Order (v12 N2): 1. normalise what is already there: a value that is not a country NAME (a raw 2-letter code that could not be decoded)
                   is BLANKED, so the column never shows 'CH'; the 'Unknown' placeholder never has a country
                2. registry lookup (openFDA registrationlisting) by full name, cleaned base name, then parent company
                3. config/manufacturer_country_overrides.csv for what is still blank
"""
import os
import re
import sqlite3
from collections import Counter

from medtech.common import GOLD_DIR, openfda_paginate, get_logger, OpenFDARateLimitError, OpenFDABadRequestError
from medtech.country_normalize import normalize_country
from medtech.reference_data import country_overrides, display_name, derive_parent_company

log = get_logger("enrich_manufacturer_country")
DB_PATH = GOLD_DIR / "medtech_warehouse.db"
ENDPOINT = "device/registrationlisting.json"
_ALLOWED_CHARS_RE = re.compile(r"[^0-9a-zA-Z@%/',._:()&\[\]\-+><= ]")
_LEGAL_TAIL_RE = re.compile(r"[ ,]+(?:INCORPORATED|INC|LLC|LLP|LTD|LIMITED|GMBH|CORPORATION|CORP|COMPANY|CO|SARL|SA|AG|BV|KG|PLC)\.?$", re.I)


def _sanitize_manufacturer_name(raw_name):
    return _ALLOWED_CHARS_RE.sub("", raw_name.split(";")[0].split(":")[0]).strip()


def candidate_names(canonical_name):
    """Names to try against the registry, most specific first: full name, cleaned base name, parent company."""
    out = []

    def add(n):
        n = (n or "").strip()
        if n and n.upper() not in {x.upper() for x in out}:
            out.append(n)

    add(canonical_name)
    base = re.sub(r"\(.*?\)", " ", display_name(canonical_name) or canonical_name)
    base = re.split(r"\bD/?B/?A\b", base, flags=re.I)[0]
    prev = None
    while prev != base:
        prev = base
        base = _LEGAL_TAIL_RE.sub("", base.strip())
    add(re.sub(r"\s+", " ", base))
    parent = derive_parent_company(canonical_name)
    if parent and parent.upper() != (canonical_name or "").upper():
        add(parent)
    return out


def _get_connection():
    if not DB_PATH.exists():
        raise SystemExit(f"Gold database not found at {DB_PATH}. Run `python run_pipeline.py` first.")
    return sqlite3.connect(DB_PATH)


def _offline():
    """MEDTECH_OFFLINE=1 skips the openFDA registry lookups (offline fixture runs and tests)."""
    return os.getenv("MEDTECH_OFFLINE", "") == "1"


def _lookup_country(manufacturer_name):
    clean_name = _sanitize_manufacturer_name(manufacturer_name)
    if not clean_name or _offline():
        return None
    try:
        records = list(openfda_paginate(ENDPOINT, search=f'registration.owner_operator.firm_name:"{clean_name}"', max_records=50))
    except (OpenFDABadRequestError, OpenFDARateLimitError):
        return None
    codes = [c for c in (rec.get("registration", {}).get("iso_country_code") for rec in records) if c]
    return Counter(codes).most_common(1)[0][0] if codes else None


def normalize_existing_countries(conn):
    """Step 1. Names stay; a raw code that cannot be decoded is blanked (so the registry can fill it); the placeholder entity has no country."""
    rows = conn.execute("SELECT mfr_key, canonical_name, country, country_source FROM dim_manufacturer WHERE country IS NOT NULL").fetchall()
    before, changed, blanked = Counter(r[2] for r in rows), 0, []
    for mfr_key, name, country, source in rows:
        resolved = normalize_country(country, source=source)
        if resolved is None:
            conn.execute("UPDATE dim_manufacturer SET country = NULL, country_source = NULL WHERE mfr_key = ?", (mfr_key,))
            blanked.append((name, country))
        elif resolved != country:
            conn.execute("UPDATE dim_manufacturer SET country = ? WHERE mfr_key = ?", (resolved, mfr_key))
            changed += 1
    cleared = conn.execute("UPDATE dim_manufacturer SET country = NULL, country_source = NULL "
                           "WHERE COALESCE(is_placeholder_entity, 0) = 1 AND country IS NOT NULL").rowcount
    conn.commit()
    if blanked:
        log.warning("%s manufacturer(s) had a country value that is not a country name and could not be decoded; blanked for the registry lookup: %s",
                    len(blanked), blanked[:10])
    if cleared:
        log.info("Removed the country from the 'Unknown' placeholder manufacturer.")
    after = Counter(r[0] for r in conn.execute("SELECT country FROM dim_manufacturer WHERE country IS NOT NULL"))
    log.info("Country normalization: %s values changed, %s blanked. BEFORE %s distinct -> AFTER %s distinct.", changed, len(blanked), len(before), len(after))


def run():
    conn = _get_connection()
    if _offline():
        log.info("MEDTECH_OFFLINE=1: openFDA registry lookups are skipped.")
    normalize_existing_countries(conn)
    rows = conn.execute("SELECT mfr_key, canonical_name FROM dim_manufacturer WHERE country IS NULL AND COALESCE(is_placeholder_entity, 0) = 0").fetchall()
    overrides = country_overrides()
    api_hits = retry_hits = overrides_hit = 0
    missing = []
    for mfr_key, name in rows:
        found = None
        if name.upper() not in overrides:        # a manual override wins over a registry guess
            for i, candidate in enumerate(candidate_names(name)):
                code = _lookup_country(candidate)
                if code:
                    found = (code, i)
                    break
        if found:
            conn.execute("UPDATE dim_manufacturer SET country = ?, country_source = 'registrationlisting_api' WHERE mfr_key = ?",
                         (normalize_country(found[0], source="registrationlisting_api") or found[0], mfr_key))
            api_hits += 1
            retry_hits += 1 if found[1] > 0 else 0
        elif name.upper() in overrides:
            conn.execute("UPDATE dim_manufacturer SET country = ?, country_source = 'manual_override' WHERE mfr_key = ?", (overrides[name.upper()], mfr_key))
            overrides_hit += 1
        else:
            missing.append(name)
    conn.commit()
    log.info("Country enrichment: %s of %s manufacturers resolved via registry (%s only via the base-name/parent retry), %s via manual override, "
             "%s still unknown.", api_hits, len(rows), retry_hits, overrides_hit, len(missing))
    if missing:
        log.warning("Still no country for %s manufacturer(s) -- add them to config/manufacturer_country_overrides.csv. First 15: %s", len(missing), missing[:15])
    conn.close()


if __name__ == "__main__":
    run()
