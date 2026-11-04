"""
text_normalize.py -- matching keys and the type boundary every ID-like field passes through.

v12 N6: normalize_device_key() joins a number to the measurement unit that follows it ('4 DEG' = '4DEG', '10 MM' = '10MM'),
        so 'TRIAL H 10MM 4 DEG 30/9' and 'TRIAL H 10MM 4DEG 30/9' are ONE device.
v11 DEV-05: punctuation is a separator ('HYDROSOFT-3D' = 'HYDROSOFT 3D'); '+', '&' and '#' are kept ('ASSURITY +' is another product).
"""
import math
import re
import unicodedata

LIST_DELIMITER = "; "

_TRADEMARK_SYMBOLS_RE = re.compile(r"[\u00ae\u2122\u00a9]")
_ASCII_TRADEMARK_RE = re.compile(r"\((?:r|tm|c)\)", re.IGNORECASE)
_MOJIBAKE_CHARS_RE = re.compile(r"[\u00bf\ufffd]")
_DEVICE_SEPARATORS_RE = re.compile(r"[^A-Z0-9+&#]+")
_UNIT_JOIN_RE = re.compile(r"\b(\d+)\s+(DEG|MM|CM|ML|MG|MCG|FR|CC|KG|HZ)\b")


def fix_mojibake(s):
    """Repair UTF-8 text wrongly decoded as Latin-1 (returns the input unchanged when it was not double-encoded)."""
    if not s or not isinstance(s, str):
        return s
    try:
        fixed = s.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s
    return fixed if fixed != s else s


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_key(raw):
    """Canonical matching key for a manufacturer / free-text name."""
    if raw is None:
        return ""
    s = fix_mojibake(str(raw))
    s = _ASCII_TRADEMARK_RE.sub("", s)
    s = _TRADEMARK_SYMBOLS_RE.sub("", s)
    s = _MOJIBAKE_CHARS_RE.sub("", s)
    s = strip_accents(s)
    return re.sub(r"\s+", " ", s).strip().upper()


def normalize_device_key(raw):
    """Matching key for device brand / model / catalog numbers (punctuation = separator, number + unit joined)."""
    s = _DEVICE_SEPARATORS_RE.sub(" ", normalize_key(raw))
    s = re.sub(r"\s+", " ", s).strip()
    return _UNIT_JOIN_RE.sub(r"\1\2", s)


_UNKNOWN_PLACEHOLDERS = {"UNKNOWN", "UNK", "N/A", "NA", "NONE", "NOT AVAILABLE", "NOT APPLICABLE", "NR", "TBD", "TEST",
                         "TEST MANUFACTURER", "", "-", "--", ".", "NULL"}


def is_placeholder(normalized_value):
    return normalized_value in _UNKNOWN_PLACEHOLDERS


_NULL_TOKENS = {"", "NA", "N/A", "NONE", "NULL", "UNKNOWN", "UNK", "-", "--", ".", "NOT AVAILABLE", "NOT APPLICABLE",
                "NAN", "NR", "TBD", "(B)(4)", "(B)(6)"}


def safe_str(value):
    """None or a clean str -- never bytes / numpy scalars / lists. Lists are joined with LIST_DELIMITER."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8", errors="replace").strip() or None
        except Exception:
            return None
    if isinstance(value, (list, tuple)):
        parts = [p for p in (safe_str(v) for v in value) if p]
        return LIST_DELIMITER.join(parts) if parts else None
    s = str(value).strip()
    return s or None


def null_if_placeholder(value):
    """safe_str() + 'NA' / 'UNKNOWN' / 'N/A' / '-' ... become a real null."""
    s = safe_str(value)
    if s is None:
        return None
    return None if s.strip().upper() in _NULL_TOKENS else s.strip()
