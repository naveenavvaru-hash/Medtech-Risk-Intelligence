"""
narrative_clean.py -- cleaning of free-text MAUDE narratives (Bronze text is never altered).

v12 changes:
  EVT-01  two more lost-character rules: a symbol after ']' '?' or '!' is a closing quote; contexts that still cannot be decided are
          collected (unresolved_samples) so the log shows the most frequent ones.
  EVT-02  the 'reasonable efforts to provide as much information' boilerplate is matched with or without the word 'relevant'; repeated
          sentences are found with a finer sentence split (a sentence that starts in lower case is no longer missed).
  EVT-03  literature detector knows 'reported through literature review' style citations (and is tested NOT to fire on
          'technical services provided literature' or 'as per scientific literature').
v11     data-driven removal of corpus-frequent sentences, literature_basis (Template / Citation), word-boundary truncation.
"""
import re
from collections import Counter

LOST_CHAR = "\u00bf"
STATS = Counter()
UNRESOLVED = Counter()
COMMON_SENTENCE_SHARE = 0.03
COMMON_SENTENCE_MIN_COUNT = 20
MIN_SENTENCE_LEN = 40
MIN_DEDUP_LEN = 20


def reset_stats():
    STATS.clear()
    UNRESOLVED.clear()


def unresolved_samples(n=5):
    """Most frequent contexts in which the lost-character symbol could not be decided: [(context, count), ...]."""
    return UNRESOLVED.most_common(n)


# ------------------------------------------------------------------ lost characters
def fix_lost_characters(s):
    if not s or LOST_CHAR not in s:
        return s
    before = s.count(LOST_CHAR)
    s = re.sub(r"\u00bf(?=\[redacted\])", '"', s)                                         # quote before [redacted]
    s = re.sub(r"(?<=[\]?!])\u00bf", '"', s)                                              # closing quote after ] ? !
    s = re.sub(r"(?<=\d)\u00bf(?=\d)", "-", s)                                            # 1585<sym>1592
    s = re.sub(r"(?<=\d)\u00bf(?=[A-Za-z]{1,2}\d)", "-", s)                              # E615<sym>E623
    s = re.sub(r"(?<=[A-Za-z])\u00bf(?=(?:S|T|LL|RE|VE|D|M)\b)", "'", s, flags=re.I)      # PATIENT<sym>S
    s = re.sub(r"(?<=[A-Za-z])\u00bf(?=[A-Za-z])", "-", s)                                # VEIN<sym>SUPERIOR
    s = re.sub(r"(?<=\s)\u00bf(?=\s)", "-", s)                                            # list bullet
    s = re.sub(r"(?<![A-Za-z0-9])\u00bf(?=[A-Za-z0-9])", '"', s)                          # opening quote
    s = re.sub(r"(?<=[A-Za-z0-9.,;:)])\u00bf(?![A-Za-z0-9])", '"', s)                     # closing quote
    remaining = s.count(LOST_CHAR)
    STATS["lost_char_fixed"] += before - remaining
    STATS["lost_char_unresolved"] += remaining
    if remaining and len(UNRESOLVED) < 500:
        for m in re.finditer(LOST_CHAR, s):
            UNRESOLVED[s[max(0, m.start() - 10):m.end() + 10]] += 1
    return s


_REDACTION_RE = re.compile(r"\(B\)\(\d\)", re.I)


def strip_redaction_markers(s):
    if not s:
        return s
    new, n = _REDACTION_RE.subn("[redacted]", s)
    STATS["redaction_markers"] += n
    return new


# ------------------------------------------------------------------ boilerplate and repeats
_BOILERPLATE = [re.compile(p, re.I) for p in [
    r"\bSUBMITS? THIS REPORT TO COMPLY WITH FDA REGULATIONS",
    r"\bIS SUBMITTING THIS (?:MDR|REPORT)\b",
    r"\b(?:THIS|THE) (?:REPORT|MDR) IS BEING SUBMITTED (?:PURSUANT|IN ACCORDANCE|TO COMPLY)",
    r"\bHAS MADE REASONABLE EFFORTS TO PROVIDE AS MUCH (?:RELEVANT )?INFORMATION",
    r"\bDOES NOT CONSTITUTE AN ADMISSION\b",
    r"\bSUBMISSION OF THIS REPORT DOES NOT\b",
    r"\bDOES NOT REFLECT A CONCLUSION BY\b",
    r"\bANY REQUIRED FIELDS THAT ARE UNPOPULATED ARE BLANK\b",
    r"\bWILL SUBMIT A SUPPLEMENTAL REPORT IF (?:ADDITIONAL|NEW)\b",
    r"\bTHIS REPORT MAY BE BASED ON INFORMATION WHICH\b",
    r"\bIF (?:THE )?INFORMATION IS UNKNOWN, NOT AVAILABLE OR DOES NOT APPLY\b",
    r"\bIF INFORMATION IS OBTAINED THAT WAS NOT AVAILABLE FOR THE INITIAL\b",
    r"\bMISSING INFORMATION FROM THIS REPORT IS IDENTIFIED AS BLANK\b",
    r"\bINFORMATION REGARDING PATIENT WEIGHT, HEIGHT, MEDICAL HISTORY, RACE, AND ETHNICITY WAS NOT REPORTED\b",
    r"\bIF ADDITIONAL INFORMATION IS OBTAINED REGARDING THIS EVENT, IT WILL BE ADDED\b",
]]
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def sentence_key(s):
    """Punctuation- and spacing-insensitive key, so 'X.' and 'X ,' count as the same sentence."""
    return re.sub(r"[^A-Z0-9]+", "", s.upper())


def split_sentences(text):
    return [s.strip() for s in _SENT_SPLIT.split(text) if s and s.strip()]


_KEEP_RX = re.compile(r"\bNO (?:PATIENT|ADVERSE|INJUR)|\bPATIENT (?:COMPLICATIONS|INJUR|HARM|OUTCOME|STATUS|CONDITION)|\bDIED\b|\bDEATH\b", re.I)


def build_common_sentences(texts, share=COMMON_SENTENCE_SHARE, min_count=COMMON_SENTENCE_MIN_COUNT):
    """Sentences (>= MIN_SENTENCE_LEN chars) found in at least `share` of the narratives. Returns {key: (count, example)}.
    Clinically meaningful sentences ('no patient complications', death ...) are never treated as template text."""
    texts = [t for t in texts if t]
    n = len(texts)
    if n == 0:
        return {}
    counts, example = Counter(), {}
    for t in texts:
        seen = set()
        for s in split_sentences(t):
            if len(s) < MIN_SENTENCE_LEN or _KEEP_RX.search(s):
                continue
            k = sentence_key(s)
            if k in seen:
                continue
            seen.add(k)
            counts[k] += 1
            example.setdefault(k, s)
    threshold = max(min_count, share * n)
    return {k: (c, example[k]) for k, c in counts.items() if c >= threshold}


def strip_boilerplate_and_repeats(text, common=None):
    """Legal boilerplate and repeated sentences are always removed. Corpus-frequent sentences are removed only when the
    narrative keeps other content (a narrative made of nothing else is left as it is rather than emptied)."""
    if not text:
        return text
    kept, seen = [], set()
    for s in split_sentences(text):
        if any(rx.search(s) for rx in _BOILERPLATE):
            STATS["boilerplate_sentences_removed"] += 1
            continue
        if len(s) >= MIN_DEDUP_LEN:
            k = sentence_key(s)
            if k in seen:
                STATS["repeated_sentences_removed"] += 1
                continue
            seen.add(k)
        kept.append(s)
    if common:
        specific = [s for s in kept if sentence_key(s) not in common]
        if specific:
            STATS["common_sentences_removed"] += len(kept) - len(specific)
            kept = specific
    return " ".join(kept)


# ------------------------------------------------------------------ split words
def build_vocab(texts):
    vocab = Counter()
    for t in texts:
        if t:
            vocab.update(re.findall(r"[A-Za-z]+", t.upper()))
    return vocab


_TOKEN_RE = re.compile(r"^([A-Za-z]+)([^A-Za-z]*)$")


def repair_split_words(text, vocab):
    """Join 'ADDI TIONAL' -> 'ADDITIONAL' when the joined word is frequent and one half is rare."""
    if not text or not vocab:
        return text
    toks, out, i = text.split(" "), [], 0
    while i < len(toks):
        if i + 1 < len(toks):
            ma, mb = _TOKEN_RE.match(toks[i]), _TOKEN_RE.match(toks[i + 1])
            if ma and mb and not ma.group(2):
                a, b, tail = ma.group(1), mb.group(1), mb.group(2)
                fa, fb, fj = vocab.get(a.upper(), 0), vocab.get(b.upper(), 0), vocab.get((a + b).upper(), 0)
                if len(a + b) >= 6 and fj >= 25 and min(fa, fb) <= max(2, fj // 20):
                    out.append(a + b + tail)
                    STATS["split_words_repaired"] += 1
                    i += 2
                    continue
        out.append(toks[i])
        i += 1
    return " ".join(out)


def prepare_narrative(raw):
    """Stage 1 (no corpus statistics needed). Redaction first, so the lost-character rules can see '[redacted]'."""
    if not raw:
        return ""
    return fix_lost_characters(strip_redaction_markers(raw))


def finalize_narrative(prepared, vocab, common=None):
    """Stage 2: boilerplate / repeats / common sentences, then split-word repair. None when nothing is left."""
    if not prepared:
        return None
    s = strip_boilerplate_and_repeats(prepared, common)
    s = repair_split_words(s, vocab)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


# ------------------------------------------------------------------ literature reports
_LIT_TEMPLATE_RE = re.compile(
    r"BASED ENTIRELY ON JOURNAL LITERATURE|LITERATURE WAS REVIEWED REGARDING|LITERATURE ARTICLE IS INCLUDED|"
    r"SEARCH OF LITERATURE PUBLICATIONS", re.I)
_LIT_CITATION_RE = re.compile(
    r"REVIEW OF THE (?:MEDICAL |PUBLISHED )?LITERATURE|LITERATURE (?:REVIEW|SEARCH) (?:FOUND|IDENTIFIED|REVEALED|REPORTED)|"
    r"REPORTED (?:THROUGH|VIA|IN|FROM) (?:A |THE )?(?:LITERATURE|PUBLISHED)\b|"
    r"PUBLISHED LITERATURE|LITERATURE ARTICLE FOUND|\bET AL\b|\bPMID\b|HTTPS?://(?:DX\.)?DOI\.ORG/|\bDOI:\s*10\.\d{4}|"
    r"\bJOURNAL OF\b.{0,80}\b(?:19|20)\d\d\b|MEDTRONIC (?:LITERATURE|REVIEW OF)", re.I)


def literature_basis(raw_text):
    """'Template' | 'Citation' | None -- why a report was recognised as a restated journal article."""
    if not raw_text:
        return None
    if _LIT_TEMPLATE_RE.search(raw_text):
        return "Template"
    if _LIT_CITATION_RE.search(raw_text):
        return "Citation"
    return None


def is_literature_report(raw_text):
    return literature_basis(raw_text) is not None


def truncate_at_word(text, n=300):
    if not text or len(text) <= n:
        return text
    return text[:n].rsplit(" ", 1)[0].rstrip(",;:- ") + "..."
