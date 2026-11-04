"""
country_normalize.py -- FIPS 10-4 / ISO 3166 country normalisation.

v12 N2: the MAUDE manufacturer-country field is a 2-letter code, but FIPS and ISO give some codes different meanings
        (CH = China in FIPS, Switzerland in ISO). detect_code_standard() reads the codes that only exist in ONE standard
        (FIPS: EI JA GM SZ SP SW DA KS PO ... ; ISO: IE DE JP ES SE DK KR GB SG ...) and decides which one the data uses.
        * one standard clearly present  -> every code is decoded with it (CH -> China under FIPS, Switzerland under ISO)
        * mixed / no evidence           -> conflicting codes are NOT guessed; they return None and the manufacturer is left
                                           blank for the registry lookup to fill (never a raw 'CH' in the country column)
v11 LOG-04: a value that is already a country NAME is returned unchanged.
"""
import math

FIPS_TO_COUNTRY = {
    "US": "United States", "EI": "Ireland", "JA": "Japan", "CH": "China", "GM": "Germany", "MX": "Mexico",
    "SZ": "Switzerland", "FR": "France", "IT": "Italy", "SN": "Singapore", "UK": "United Kingdom", "NL": "Netherlands",
    "BE": "Belgium", "SP": "Spain", "SW": "Sweden", "DA": "Denmark", "NO": "Norway", "FI": "Finland", "AU": "Austria",
    "AS": "Australia", "CA": "Canada", "KS": "South Korea", "TW": "Taiwan", "PO": "Portugal", "CS": "Costa Rica",
    "DR": "Dominican Republic", "RQ": "Puerto Rico", "IS": "Israel", "EZ": "Czechia", "PL": "Poland", "HU": "Hungary",
    "TU": "Turkey", "IN": "India", "BR": "Brazil", "MY": "Malaysia", "TH": "Thailand",
}
ALIAS_TO_COUNTRY = {
    "UNITED STATES": "United States", "UNITED STATES OF AMERICA": "United States", "USA": "United States",
    "U.S.": "United States", "U.S.A.": "United States", "FRANCE": "France", "GERMANY": "Germany", "IRELAND": "Ireland",
    "JAPAN": "Japan", "CHINA": "China", "SWITZERLAND": "Switzerland", "MEXICO": "Mexico", "ITALY": "Italy",
    "SINGAPORE": "Singapore", "UNITED KINGDOM": "United Kingdom", "GREAT BRITAIN": "United Kingdom",
    "NETHERLANDS": "Netherlands", "PUERTO RICO": "Puerto Rico", "SOUTH KOREA": "South Korea",
    "KOREA, REPUBLIC OF": "South Korea", "THAILAND": "Thailand",
}
ISO_TO_COUNTRY = {
    "US": "United States", "DE": "Germany", "IE": "Ireland", "JP": "Japan", "CN": "China", "CH": "Switzerland",
    "MX": "Mexico", "FR": "France", "IT": "Italy", "SG": "Singapore", "GB": "United Kingdom", "NL": "Netherlands",
    "BE": "Belgium", "ES": "Spain", "SE": "Sweden", "DK": "Denmark", "NO": "Norway", "FI": "Finland", "AT": "Austria",
    "AU": "Australia", "CA": "Canada", "KR": "South Korea", "TW": "Taiwan", "PT": "Portugal", "CR": "Costa Rica",
    "DO": "Dominican Republic", "PR": "Puerto Rico", "IL": "Israel", "CZ": "Czechia", "PL": "Poland", "HU": "Hungary",
    "TR": "Turkey", "IN": "India", "BR": "Brazil", "MY": "Malaysia", "TH": "Thailand",
}
ISO_SOURCES = {"registrationlisting_api"}
SAFE_SOURCES = {"maude_native"}
AMBIGUOUS_FIPS_CODES = {"CN"}   # Comoros in FIPS, commonly mis-entered for China
CONFLICTING_CODES = {c for c in FIPS_TO_COUNTRY if c in ISO_TO_COUNTRY and FIPS_TO_COUNTRY[c] != ISO_TO_COUNTRY[c]}
PLACEHOLDERS = {"", "UNKNOWN", "N/A", "NA", "NONE", "NULL", "-", "NOT AVAILABLE"}

# Codes that identify the standard: they exist (with these meanings) in only one of the two.
FIPS_DISTINCTIVE = {"EI", "JA", "GM", "SZ", "SP", "SW", "DA", "KS", "PO", "EZ", "TU", "UK", "SN", "RQ", "CS", "DR"}
ISO_DISTINCTIVE = {"IE", "DE", "JP", "ES", "SE", "DK", "KR", "GB", "SG", "CZ", "PT", "AT", "DO"}
DOMINANCE = 10      # one standard must have at least 10x the evidence of the other to be chosen when both appear


def detect_code_standard(codes):
    """('FIPS' | 'ISO' | 'MIXED' | 'UNKNOWN', fips_rows, iso_rows) from an iterable of raw 2-letter codes."""
    fips = iso = 0
    for c in codes:
        c = str(c or "").strip().upper()
        if c in FIPS_DISTINCTIVE:
            fips += 1
        elif c in ISO_DISTINCTIVE:
            iso += 1
    if fips and not iso:
        return "FIPS", fips, iso
    if iso and not fips:
        return "ISO", fips, iso
    if fips and iso:
        if fips >= DOMINANCE * iso:
            return "FIPS", fips, iso
        if iso >= DOMINANCE * fips:
            return "ISO", fips, iso
        return "MIXED", fips, iso
    return "UNKNOWN", fips, iso


def normalize_country(value, source=None, standard=None):
    """Canonical country name, or None when it cannot be decided (never guesses).
    standard: 'FIPS' / 'ISO' as returned by detect_code_standard() -- used for source 'maude_native'."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    s = str(value).strip()
    if not s or s.upper() in PLACEHOLDERS or s.lower() == "nan":
        return None
    if len(s) > 2:                                  # already a name
        return ALIAS_TO_COUNTRY.get(s.upper(), s)
    code = s.upper()
    if source in ISO_SOURCES:
        return ISO_TO_COUNTRY.get(code)
    if source in SAFE_SOURCES:
        if standard == "FIPS":
            return None if code in AMBIGUOUS_FIPS_CODES else FIPS_TO_COUNTRY.get(code)
        if standard == "ISO":
            return ISO_TO_COUNTRY.get(code)
        if code in CONFLICTING_CODES or code in AMBIGUOUS_FIPS_CODES:      # standard not known: never guess
            return None
        return FIPS_TO_COUNTRY.get(code) or ISO_TO_COUNTRY.get(code)
    if code in AMBIGUOUS_FIPS_CODES:
        return None
    return FIPS_TO_COUNTRY.get(code)
