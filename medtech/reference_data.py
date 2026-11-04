"""
reference_data.py -- reviewed CSV lookups (config/) plus small rule-based helpers.

v12 DEV-04: device descriptions
    token_key()                    word-order-insensitive key ('PULSE-GENERATOR, PACEMAKER, IMPLANTABLE' = 'Implantable Pacemaker Pulse-Generator')
    apply_description_rules()      config/device_description_map.csv (pattern + optional product codes -> label, {FDA} = FDA device name)
    smart_case()                   ALL-CAPS descriptions become Title Case (acronyms such as MRI / CRT / PEEK are kept)
v11     parent company map, country / brand overrides, display_name(), problem_code_group(), reason_group(), lot_kind(), short_recall_name()
"""
import csv
import re

from medtech.common import CONFIG_DIR, get_logger
from medtech.text_normalize import normalize_key

log = get_logger("reference_data")
_PARENT_RULES = None
_COUNTRY_OVERRIDES = None
_BRAND_OVERRIDES = None
_DESC_RULES = None


def _read_csv(name):
    path = CONFIG_DIR / name
    if not path.exists():
        log.warning("Reference file %s not found -- continuing without it.", path)
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def parent_rules():
    global _PARENT_RULES
    if _PARENT_RULES is None:
        rules = []
        for row in _read_csv("parent_company_map.csv"):
            pat, parent = (row.get("pattern") or "").strip(), (row.get("parent_company") or "").strip()
            if not pat or not parent:
                continue
            try:
                rules.append((re.compile(pat, re.I), parent))
            except re.error as exc:
                log.error("Bad regex in parent_company_map.csv (%r): %s", pat, exc)
        _PARENT_RULES = rules
    return _PARENT_RULES


def derive_parent_company(canonical_name):
    if not canonical_name:
        return None
    for rx, parent in parent_rules():
        if rx.search(canonical_name):
            return parent
    return canonical_name


def country_overrides():
    global _COUNTRY_OVERRIDES
    if _COUNTRY_OVERRIDES is None:
        _COUNTRY_OVERRIDES = {(r.get("canonical_name_upper") or "").strip().upper(): (r.get("country") or "").strip()
                              for r in _read_csv("manufacturer_country_overrides.csv")
                              if (r.get("canonical_name_upper") or "").strip() and (r.get("country") or "").strip()}
    return _COUNTRY_OVERRIDES


def brand_overrides():
    global _BRAND_OVERRIDES
    if _BRAND_OVERRIDES is None:
        _BRAND_OVERRIDES = {normalize_key(r.get("brand_name_norm")): (r.get("brand_name_display") or "").strip()
                            for r in _read_csv("brand_name_overrides.csv")
                            if (r.get("brand_name_norm") or "").strip() and (r.get("brand_name_display") or "").strip()}
    return _BRAND_OVERRIDES


def brand_display(brand_name):
    """Display brand: manual override, else broken symbols removed and spaces collapsed."""
    if not brand_name:
        return None
    ov = brand_overrides().get(normalize_key(brand_name))
    if ov:
        return ov
    s = re.sub(r"[\u00bf\ufffd]", "", str(brand_name))
    s = re.sub(r"\s+", " ", s).strip(" ,;")
    return s or None


_LEGAL = (r"(?:SARL|S\.A\.R\.L\.?|S\u00c0RL|GMBH|AG|BV|B\.V\.|SA|S\.A\.|SAS|SRL|S\.R\.L\.|LTD|LLC|INC\.?|KG|SPA|S\.P\.A\.|PLC|NV|AB|AS|OY)")
_CC = r"(?:CH|DE|FR|IT|ES|NL|BE|SE|DK|NO|FI|AT|IE|UK|GB|JP|CN|US|USA|MX|IL|SG|KR|TW|AU|CA|BR|IN|PR|CR)"
_DISPLAY_RX = re.compile(r"^(.*(?<![A-Za-z])" + _LEGAL + r")\s+" + _CC + r"$", re.I)


def display_name(canonical_name):
    """Presentation name: drop a trailing country code that follows a legal suffix."""
    if not canonical_name:
        return None
    s = re.sub(r"\s+", " ", str(canonical_name)).strip()
    m = _DISPLAY_RX.match(s)
    return m.group(1).strip() if m else s


UNINFORMATIVE_PROBLEM_CODES = {
    "APPROPRIATE DEVICE PROBLEM TERM/CODE NOT AVAILABLE",
    "ADVERSE EVENT WITHOUT IDENTIFIED DEVICE OR USE PROBLEM",
    "INSUFFICIENT DEVICE PROBLEM INFORMATION",
    "NO APPARENT ADVERSE EVENT",
    "NO CODE AVAILABLE",
    "OTHER",
}


def problem_code_group(code):
    if not code:
        return None
    return "Uninformative" if str(code).strip().upper() in UNINFORMATIVE_PROBLEM_CODES else "Informative"


_VAGUE_REASON_RX = re.compile(r"^(?:other|under investigation(?: by firm)?|unknown(?:/undetermined by firm)?|not yet determined|undetermined)\b", re.I)


def reason_group(reason):
    """'Vague' for catch-all recall reasons, else 'Specific'."""
    if not reason or not str(reason).strip():
        return None
    return "Vague" if _VAGUE_REASON_RX.match(str(reason).strip()) else "Specific"


_PMA_LOT_RX = re.compile(r"^P\d{8,9}$")


def lot_kind(lot):
    """'PMA code' (P#########) / 'Multiple' (several lots in one cell) / 'Lot'."""
    if lot is None or (isinstance(lot, float) and lot != lot) or not str(lot).strip():
        return None
    s = str(lot).strip()
    if _PMA_LOT_RX.match(s):
        return "PMA code"
    if re.search(r"[,;/&]|\s{1,}\S+\s", s) and len([t for t in re.split(r"[,;/&\s]+", s) if t]) > 1:
        return "Multiple"
    return "Lot"


_SHORT_SPLIT_RX = re.compile(
    r"\s+(?:Model|Models|Catalog|Part|Product Code|REF|Size|Indications?|Implantation|Serial|Lot|UDI)\b|[;:\n]|\.\s", re.I)
_SHORT_LABEL_RX = re.compile(r"^\s*(?:Brand Name|Product Name|Device Name)\s*:\s*", re.I)


def short_recall_name(text, max_len=80):
    """Recall product descriptions can be paragraphs. Keep the leading device name only."""
    if not text:
        return None
    s = _SHORT_LABEL_RX.sub("", re.sub(r"\s+", " ", str(text)).strip())
    first = _SHORT_SPLIT_RX.split(s, maxsplit=1)[0].strip(" ,-")
    if len(first) < 5 or first.upper().rstrip(" ,;") in {"UNKNOWN", "N/A", "NA", "NONE", "NOT AVAILABLE", "NOT APPLICABLE"}:
        first = s
    if len(first) > max_len:
        first = first[:max_len].rsplit(" ", 1)[0]
    return first.strip(" ,-;") or None


# ------------------------------------------------------------------ device descriptions (DEV-04)
_ACRONYMS = {"MRI", "CRT", "ICD", "IPG", "PEEK", "TLIF", "ALIF", "LLIF", "PLIF", "PTC", "CRM", "PCS", "USA", "US", "II", "III", "IV",
             "CT", "ECG", "EKG", "LED", "UDI", "PMA", "IFU", "GPS", "HIPOINT"}
_SMALL_WORDS = {"and", "of", "with", "for", "to", "the", "a", "an", "in", "on", "or", "by", "per"}


def token_key(s):
    """Word-order-, case- and punctuation-insensitive key of a description."""
    return " ".join(sorted(re.findall(r"[A-Z0-9]+", str(s or "").upper())))


def smart_case(s):
    """ALL-CAPS text -> Title Case; acronyms (MRI, PEEK ...) and words with digits are kept; mixed-case text is untouched."""
    if not s or not str(s).isupper():
        return s
    out = []
    for i, w in enumerate(str(s).split(" ")):
        core = re.sub(r"[^A-Z0-9]", "", w)
        if core in _ACRONYMS or any(ch.isdigit() for ch in w):
            out.append(w)
        elif i > 0 and core.lower() in _SMALL_WORDS:
            out.append(w.lower())
        else:
            out.append("-".join(p.capitalize() for p in w.split("-")))
    return " ".join(out)


def description_rules():
    """config/device_description_map.csv -> [(compiled regex, set of product codes or None, label)]."""
    global _DESC_RULES
    if _DESC_RULES is None:
        rules = []
        for row in _read_csv("device_description_map.csv"):
            pat, label = (row.get("pattern") or "").strip(), (row.get("label") or "").strip()
            if not pat or not label:
                continue
            codes = {c.strip().upper() for c in (row.get("product_codes") or "").split("|") if c.strip()}
            try:
                rules.append((re.compile(pat, re.I), codes or None, label))
            except re.error as exc:
                log.error("Bad regex in device_description_map.csv (%r): %s", pat, exc)
        _DESC_RULES = rules
    return _DESC_RULES


def apply_description_rules(description, product_code, fda_name):
    """The standard label from the config map for this description, or None. '{FDA}' in a label = the FDA device name."""
    for rx, codes, label in description_rules():
        if codes is not None and (product_code or "").upper() not in codes:
            continue
        if rx.search(description or ""):
            return fda_name if label == "{FDA}" else label.replace("{FDA}", fda_name or "")
    return None
