"""
Unit tests for every v10 / v11 / v12 / v12.1 fix (no network, no data files needed).

    python tests/unit_tests.py          # from anywhere (or press Run in VS Code); exit code 0 = all passed

Section letters match the 'Verified by' column of docs/ISSUE_LOG.md (section L: v12.1). Several cases are REAL strings found in the
production exports (quantities, lost-character contexts, manufacturer names, device names, narratives).
"""
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # lets this file run directly as well as with `python -m tests.unit_tests`

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from medtech import common  # noqa: E402
from medtech import reference_data as rd  # noqa: E402
from medtech import text_normalize as tn  # noqa: E402
from medtech import country_normalize as cn  # noqa: E402
from medtech.country_normalize import normalize_country  # noqa: E402
from medtech.silver import narrative_clean as nc  # noqa: E402
from medtech.silver import quality_checks as qc  # noqa: E402
from medtech.gold import root_cause as rcz  # noqa: E402

failures, passed = [], 0


def check(label, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  ok   {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label} {detail}")


def section(title):
    print(f"\n[{title}]")


class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


L = nc.LOST_CHAR
_K = "api" + "_key"                      # assembled at runtime: no credential-shaped literal in this file
_V = "ABCD" + "1234" + "EFGH" + "5678"

# ============================================================================================ A
section("A. text type boundary, placeholders, device keys (DEV-05, N6)")
check("bytes are decoded, never repr'd", tn.safe_str(b"K841075") == "K841075" and not str(tn.safe_str(b"p\xb2\xb0?\x88\x02")).startswith("b'"))
check("list joined with '; '", tn.safe_str(["K1", None, "K2"]) == "K1; K2")
for tok in ("NA", "N/A", "UNKNOWN", "unknown", "-", "None", "", "(B)(6)"):
    check(f"null_if_placeholder({tok!r}) -> None", tn.null_if_placeholder(tok) is None)
check("leading zeros survive", tn.null_if_placeholder("0002210171") == "0002210171")
check("device key: punctuation is a separator", tn.normalize_device_key("HYDROSOFT-3D") == tn.normalize_device_key("HYDROSOFT 3D") == "HYDROSOFT 3D")
check("device key: trailing comma / full stop ignored",
      tn.normalize_device_key("NUVASIVE MODULUS TLIF INTERBODY SYSTEM,") == tn.normalize_device_key("NuVasive Modulus TLIF Interbody System"))
check("device key: CAGE-X, 4MM = CAGE-X. 4MM", tn.normalize_device_key("CAVUX CERVICAL CAGE-X, 4MM") == tn.normalize_device_key("CAVUX CERVICAL CAGE-X. 4MM"))
check("device key keeps '+' (ASSURITY + is a different product)", tn.normalize_device_key("Assurity +") != tn.normalize_device_key("Assurity"))
check("device key strips the broken registered-trademark symbol", tn.normalize_device_key(f"ENDURITY{L} CORE SR") == "ENDURITY CORE SR")
check("device key of None is empty", tn.normalize_device_key(None) == "")
check("N6 (real): 'TRIAL H 10MM 4 DEG 30/9' = 'TRIAL H 10MM 4DEG 30/9'", tn.normalize_device_key("TRIAL H 10MM 4 DEG 30/9") == tn.normalize_device_key("TRIAL H 10MM 4DEG 30/9"))
check("N6 (real): 'TRIAL H 9MM 8 DEG 32/12' = '... 8DEG 32/12'", tn.normalize_device_key("TRIAL H 9MM 8 DEG 32/12") == tn.normalize_device_key("TRIAL H 9MM 8DEG 32/12"))
check("N6: a number + unit is joined ('10 MM' = '10MM')", tn.normalize_device_key("CAGE 10 MM") == tn.normalize_device_key("CAGE 10MM") == "CAGE 10MM")
check("N6: a number followed by an ordinary word is NOT joined", tn.normalize_device_key("SYSTEM 3 PLUS") == "SYSTEM 3 PLUS")
check("N6: different sizes stay different devices", tn.normalize_device_key("TRIAL 8 DEG") != tn.normalize_device_key("TRIAL 4 DEG"))

# ============================================================================================ A2
section("A2. credentials never reach the log (N1)")
msg = "500 Server Error for url: https://api.fda.gov/device/udi.json?limit=1000&skip=1000&search=a&" + _K + "=" + _V + "ZZ. Retrying in 2s"
red = common.redact_secrets(msg)
check("api_key=... is masked", "ABCD1234" not in red and "api_key=***" in red and "skip=1000" in red and "Retrying in 2s" in red, red)
check("the surrounding URL and text are kept", red.startswith("500 Server Error for url: https://api.fda.gov/device/udi.json?limit=1000"))
check("API_KEY= (any case) and 'api-key=' are masked too", "SECRET99" not in common.redact_secrets("x " + _K.upper() + "=" + "SECRET" + "99 y") and "SECRET99" not in common.redact_secrets("a?api-" + "key=" + "SECRET" + "99"))
_old = os.environ.get("OPENFDA_API_KEY")
os.environ["OPENFDA_API_KEY"] = "LITERAL" + "SECRET99"
check("the literal key value is masked wherever it appears", common.redact_secrets("header Authorization " + "LITERAL" + "SECRET99" + " end") == "header Authorization *** end")
if _old is None:
    os.environ.pop("OPENFDA_API_KEY", None)
else:
    os.environ["OPENFDA_API_KEY"] = _old
check("text without a key is returned unchanged", common.redact_secrets("no secrets here") == "no secrets here" and common.redact_secrets(None) is None)
lg0 = ListHandler()
lg0.addFilter(common.RedactingFilter())
lgr = logging.getLogger("test_redact")
lgr.addHandler(lg0)
lgr.setLevel(logging.INFO)
lgr.propagate = False
lgr.warning("openFDA request failed (attempt %s/%s): %s. Retrying in %ss", 1, 6, "500 Server Error for url: https://api.fda.gov/x?limit=1&" + _K + "=" + _V + "&skip=0", 2)
check("the logging filter masks the key in a %-formatted warning (the v11 leak)", "ABCD1234" not in lg0.records[-1].getMessage() and "api_key=***" in lg0.records[-1].getMessage())
check("both real log handlers carry the redacting filter", all(any(isinstance(f, common.RedactingFilter) for f in h.filters) for h in common._handlers))

# ============================================================================================ B
section("B. narrative cleaning (EVT-01 / 02 / 03, TXT-02 / 03, NUL-09)")
cases = [
    (f"392{L}402", "392-402"), (f"PATIENT{L}S", "PATIENT'S"), (f"DON{L}T", "DON'T"), (f"VEIN{L}SUPERIOR", "VEIN-SUPERIOR"),
    (f"HEART RHYTHM. 2026. 23:E615{L}E623. DOI", "HEART RHYTHM. 2026. 23:E615-E623. DOI"),           # real: letter-digit range
    (f"23:EE25{L}EE35.", "23:EE25-EE35."),                                                          # real
    (f"PRODUCT ANALYSIS: {L} EQUIPMENT USED: VIDEO", "PRODUCT ANALYSIS: - EQUIPMENT USED: VIDEO"),   # real: list bullet
    (f"RELEASED IN ONE BATCH. {L} BATCH1: LOT QTY", "RELEASED IN ONE BATCH. - BATCH1: LOT QTY"),     # real
    (f'SAID {L}THE LEAD{L} WAS WORN', 'SAID "THE LEAD" WAS WORN'),
    (f'RIGHT MCA ANEURYSM?{L} A MANUFACTURING RECORD', 'RIGHT MCA ANEURYSM?" A MANUFACTURING RECORD'),    # real (EVT-01 v12): closing quote after ?
    (f'WHAT HAPPENED!{L} NEXT', 'WHAT HAPPENED!" NEXT'),
]
for src, exp in cases:
    check(f"lost symbol: {src[:42]!r}", nc.fix_lost_characters(src) == exp, nc.fix_lost_characters(src))
check("quote before [redacted] (real)", nc.fix_lost_characters(f'CORRECTED TO {L}[redacted]"') == 'CORRECTED TO "[redacted]"')
check("closing quote after [redacted] (real, EVT-01 v12)", nc.fix_lost_characters(f'CORRECTED TO "[redacted]{L}. D4 GTIN') == 'CORRECTED TO "[redacted]". D4 GTIN')
nc.reset_stats()
nc.fix_lost_characters(f"PAIN (LLIF{L}43.8%, ALIF{L}75.0%) AND 0.8{L}ML OF ONYX")
check("undecidable symbols (LLIF<sym>43.8%) are left alone and COUNTED", nc.STATS["lost_char_unresolved"] == 3)
check("undecided contexts are collected for the log", len(nc.unresolved_samples(5)) >= 1 and all(isinstance(c, int) for _, c in nc.unresolved_samples(5)))
nc.reset_stats()
check("reset_stats clears the collected contexts", nc.unresolved_samples(5) == [])
check("redaction first, so the quote rule sees the marker", nc.prepare_narrative(f'TO {L}(B)(6)" END') == 'TO "[redacted]" END')
check("redaction marker replaced", nc.strip_redaction_markers("ON (B)(6) 2024") == "ON [redacted] 2024")
txt = ("THE LEAD FAILED TO CAPTURE. THE SUBMISSION OF THIS REPORT DOES NOT CONSTITUTE AN ADMISSION BY CARLSMED, ITS EMPLOYEES, OR THE FDA. "
       "MEDTRONIC SUBMITS THIS REPORT TO COMPLY WITH FDA REGULATIONS 21 CFR PARTS 4 AND 803. THE LEAD FAILED TO CAPTURE. "
       "ANY REQUIRED FIELDS THAT ARE UNPOPULATED ARE BLANK BECAUSE THE INFORMATION IS CURRENTLY UNKNOWN.")
out = nc.strip_boilerplate_and_repeats(txt)
check("legal boilerplate of ANY firm is removed (CARLSMED, MEDTRONIC)", "ADMISSION" not in out and "SUBMITS THIS REPORT" not in out and "UNPOPULATED" not in out)
check("a repeated sentence collapses to one", out.count("THE LEAD FAILED TO CAPTURE") == 1)
happe = ("THE IMPLANT WAS REMOVED. HAPPE SPINE HAS MADE REASONABLE EFFORTS TO PROVIDE AS MUCH INFORMATION AS IS AVAILABLE TO THE COMPANY "
         "AT THE TIME OF THE SUBMISSION DATE OF THIS REPORT")
check("EVT-02 (real): 'reasonable efforts to provide as much information' (no word 'relevant') is boilerplate", "REASONABLE EFFORTS" not in nc.strip_boilerplate_and_repeats(happe))
check("the 'relevant' variant is still removed", "REASONABLE EFFORTS" not in nc.strip_boilerplate_and_repeats("X FAILED. FIRM HAS MADE REASONABLE EFFORTS TO PROVIDE AS MUCH RELEVANT INFORMATION AS POSSIBLE."))
check("repeats are detected after stripping punctuation", nc.strip_boilerplate_and_repeats("THE DEVICE WAS REPLACED , TODAY. THE DEVICE WAS REPLACED TODAY.") == "THE DEVICE WAS REPLACED , TODAY.")
check("EVT-02: a repeat that STARTS IN LOWER CASE is removed", nc.strip_boilerplate_and_repeats("REVIEW OF IMAGING SHOWED NO ISSUE. review of imaging showed no issue.") == "REVIEW OF IMAGING SHOWED NO ISSUE.")
check("short fragments ('DR.', 'NO.') are never treated as repeats", nc.strip_boilerplate_and_repeats("SEE NO. 5. AND NO. 6 TOO.") == "SEE NO. 5. AND NO. 6 TOO.")
S = "SINCE THERE WAS NO EVIDENCE TO SUGGEST THE EVENT WAS RELATED TO A MANUFACTURING OR DESIGN ISSUE, NO CORRECTIVE ACTIONS WILL BE TAKEN."
KEEP = "NO PATIENT COMPLICATIONS HAVE BEEN REPORTED AS A RESULT OF THIS EVENT."
texts = [f"CASE {i} SHOWED A UNIQUE FAILURE MODE NUMBER {i}. {S} {KEEP}" for i in range(100)]
common_s = nc.build_common_sentences(texts)
check("a sentence found in >= 3% of narratives is 'common'", nc.sentence_key(S) in common_s)
check("a clinically meaningful frequent sentence is exempt", nc.sentence_key(KEEP) not in common_s)
fin = nc.finalize_narrative(texts[0], Counter(), common_s)
check("common sentence removed, specific + clinical sentences kept", S not in fin and "UNIQUE FAILURE MODE" in fin and "NO PATIENT COMPLICATIONS" in fin)
check("a narrative made only of common text is NOT emptied", nc.finalize_narrative(S, Counter(), common_s) == S)
check("tiny data sets remove nothing (min count)", nc.build_common_sentences([f"{S} X{i}" for i in range(5)]) == {})
check("narrative that is only boilerplate -> None", nc.finalize_narrative("MEDTRONIC SUBMITS THIS REPORT TO COMPLY WITH FDA REGULATIONS 21 CFR PARTS 4 AND 803.", Counter()) is None)
vocab = Counter({"ADDITIONAL": 300, "ADDI": 1, "TIONAL": 1, "THE": 9000})
check("split word re-joined", nc.repair_split_words("ADDI TIONAL INFORMATION", vocab) == "ADDITIONAL INFORMATION")
check("normal words are not merged", nc.repair_split_words("THE DEVICE WAS REPLACED", vocab) == "THE DEVICE WAS REPLACED")
check("literature: template", nc.literature_basis("THIS INFORMATION IS BASED ENTIRELY ON JOURNAL LITERATURE.") == "Template")
check("literature: citation (real: 'review of the literature article found')", nc.literature_basis("MEDTRONIC REVIEW OF THE LITERATURE ARTICLE FOUND A RETROSPECTIVE REVIEW OF 88 PATIENT") == "Citation")
check("literature: citation (real: 'TOKUDOME, DAIGO, ET AL. (2026)')", nc.literature_basis("NO ADVERSE PATIENT EFFECTS WERE REPORTED. TOKUDOME, DAIGO, ET AL. (2026). EXPERIMENTAL") == "Citation")
check("literature: doi.org link", nc.literature_basis("SEE HTTPS://DOI.ORG/10.1016/J.JIMED.2023.07.004. THE") == "Citation")
check("EVT-03 (real): 'reported through literature review that patient's pacemaker was eroded'", nc.literature_basis("IT WAS REPORTED THROUGH LITERATURE REVIEW THAT PATIENT'S PACEMAKER WAS ERODED.") == "Citation")
check("EVT-03 (real): 'reported via literature review titled ...'", nc.literature_basis("IT WAS REPORTED VIA LITERATURE REVIEW TITLED \"TRIPLE PACING SPIKES\"") == "Citation")
check("EVT-03 (real): 'reported in a literature article titled ...'", nc.literature_basis("IT WAS REPORTED IN A LITERATURE ARTICLE TITLED \"ACUTE FRAME COIL MIGRATION\"") == "Citation")
check("EVT-03 (real): 'It was reported in the literature review, ...' (mixed case)", nc.literature_basis("It was reported in the literature review, \"An Unexpected Encounter\"") == "Citation")
check("EVT-03 probe: 'technical services provided literature regarding this issue' is NOT literature", nc.literature_basis("TECHNICAL SERVICES (TS) PROVIDED LITERATURE REGARDING THIS ISSUE. THIS PACEMAKER REMAINS IN-SERVICE.") is None)
check("EVT-03 probe: 'as per scientific literature, ... is common' is NOT literature", nc.literature_basis("AS PER SCIENTIFIC LITERATURE, ADJACENT-SEGMENT DISEASE (ASD) IS A RELATIVELY COMMON SIDE EFFECT.") is None)
check("an ordinary report is not literature", nc.literature_basis("IT WAS REPORTED THAT THE LEAD FRACTURED DURING THE PROCEDURE.") is None)
check("the word ARTICLE alone is not enough", nc.literature_basis("THE ARTICLE NUMBER ON THE LABEL WAS NOT LEGIBLE.") is None)
cut = nc.truncate_at_word("word " * 100, 300)
check("sample text cut on a word with an ellipsis", cut.endswith("...") and len(cut) <= 304)

# ============================================================================================ C
section("C. quality_checks rules (formats, quantities, scope, events)")
for raw, exp in [("529912 units", 529912), ("12 cartons, 600 units", 600), ("unknown", None), ("N/A - Labeling Correction", None), ("1,234", 1234),
                 ("50 boxes of 10", None), (None, None), ("45,302 (8,188 in the U.S.) - total, all model numbers", 45302), ("315 (172 in USA)", 315),
                 ("1,127 loose implants; 10/24-implant kits; and 4/36-implant kits (1,511 total implants)", 1511), ("total: 600 units", 600),
                 ("316 total, both models", 316), ("130 units total", 130)]:
    check(f"quantity {str(raw)[:48]!r} -> {exp}", qc.parse_product_quantity_units(raw) == exp, str(qc.parse_product_quantity_units(raw)))
for raw, exp in [("Nationwide and international", "International"), ("US distribution only", "Domestic"), ("Worldwide Distribution", "Worldwide"),
                 ("Product distributed to Germany and Japan", "International"), ("Product distributed to CA, TX, NY", "Domestic"),
                 ("See attached list", "Unknown"), (None, None)]:
    check(f"distribution {raw!r} -> {exp}", qc.derive_distribution_scope(raw) == exp)
check("age '56 YR' -> 56.0, '2 DA' -> 0.01, None -> None", qc.parse_patient_age_years("56 YR") == 56.0 and qc.parse_patient_age_years("2 DA") == 0.01 and qc.parse_patient_age_years(None) is None)
check("outcomes decoded, unique, SORTED", qc.decode_patient_outcomes("Hospitalization; H; R") == ["Hospitalization", "Required Intervention"])
check("problem codes sorted and unique", qc.split_dedup_sorted("Fracture; Fracture; Break") == ["Break", "Fracture"])
check("'Prefer Not To Disclose' sex -> 'Unknown'", qc._normalize_sex("Prefer Not To Disclose") == "Unknown" and qc._normalize_sex(None) == "Unknown" and qc._normalize_sex("Female") == "Female")
for lot, exp in [("P000121175", "PMA code"), ("LOT9", "Lot"), ("0002210171", "Lot"), ("L1, L2", "Multiple"), (None, None)]:
    check(f"lot_kind({lot!r}) = {exp}", rd.lot_kind(lot) == exp)

fake = [{
    "report_number": "R2", "date_received": "20250105", "event_type": "Injury", "manufacturer_name": "Acme", "manufacturer_g1_country": "EI",
    "device": [{"manufacturer_d_name": "Acme", "device_report_product_code": "DXY", "brand_name": "UNKNOWN", "model_number": "M", "catalog_number": "NA",
                "lot_number": "UNKNOWN", "generic_name": "Pacemaker lead"}],
    "mdr_text": [{"text": f"NO ADVERSE PATIENT EFFECTS WERE REPORTED. TOKUDOME, DAIGO, ET AL. (2026). AGE 68. PATIENT{L}S COURSE ON (B)(6). "
                          "MEDTRONIC SUBMITS THIS REPORT TO COMPLY WITH FDA REGULATIONS 21 CFR PARTS 4 AND 803."}],
    "patient": [{"patient_age": "68 YR", "patient_sex": "Prefer Not To Disclose", "sequence_number_outcome": "Required Intervention; Hospitalization"}],
    "product_problems": ["Fracture", "Fracture"]}, {
    "report_number": "R1", "date_received": "20250101", "event_type": "Malfunction", "manufacturer_name": "Acme",
    "device": [{"manufacturer_d_name": "Acme", "manufacturer_d_country": "SZ", "device_report_product_code": "DXY", "brand_name": "Lead", "model_number": "M",
                "catalog_number": "CAT-1", "lot_number": "P000121175", "generic_name": "lead"}],
    "mdr_text": [], "patient": [{"patient_age": "NA", "patient_sex": "Female"}], "product_problems": []}, {
    "report_number": "R3", "date_received": "20250102", "event_type": "Malfunction", "manufacturer_name": "Acme", "manufacturer_country": "CH",
    "device": [{"manufacturer_d_name": "Acme", "device_report_product_code": "DXY", "brand_name": "UNKNOWN", "generic_name": "N/A", "model_number": "NA"}],
    "mdr_text": [], "patient": [{}], "product_problems": []}]
qc._load_bronze = lambda prefix: fake if prefix == "maude_" else []
mdf = qc.extract_maude()
r1, r2, r3 = mdf.iloc[0], mdf.iloc[2], mdf.iloc[1]
check("Silver sorted by date then report number", mdf["report_number"].tolist() == ["R1", "R3", "R2"])
check("'NA' age / catalog and 'UNKNOWN' lot become null", r1["patient_age"] is None and r2["catalog_number"] is None and r2["lot_number"] is None)
check("placeholder brand 'UNKNOWN' becomes null; the generic name is the fallback", r2["brand_name"] is None and r2["device_name"] == "Pacemaker lead")
check("N5: no usable brand AND no usable generic name -> device_name is None (never 'UNKNOWN')", r3["brand_name"] is None and r3["device_name"] is None)
check("citation-style literature report flagged, age text AND years blank", bool(r2["is_literature_report"]) and r2["literature_basis"] == "Citation" and r2["patient_age"] is None and pd.isna(r2["patient_age_years"]))
check("narrative repaired: apostrophe, redaction, no boilerplate", "PATIENT'S" in r2["narrative"] and "[redacted]" in r2["narrative"] and "SUBMITS THIS REPORT" not in r2["narrative"])
check("lot_kind assigned; PMA-style lot recognised", r1["lot_kind"] == "PMA code" and r2["lot_kind"] is None)
check("sex normalised", r2["patient_sex"] == "Unknown" and r1["patient_sex"] == "Female")
check("outcomes sorted, problem codes unique", r2["patient_outcome"] == "Hospitalization; Required Intervention" and r2["product_problem_codes"] == "Fracture")
check("MAUDE country read from the candidate fields; FIPS codes EI / SZ decode", r1["manufacturer_country"] == "Switzerland" and r2["manufacturer_country"] == "Ireland")
check("N2 (in extract_maude): the data contains FIPS-only codes (SZ, EI), so 'CH' decodes to CHINA", r3["manufacturer_country"] == "China", str(r3["manufacturer_country"]))
check("has_narrative false when nothing is left", bool(r1["has_narrative"]) is False and bool(r2["has_narrative"]) is True)

# duplicates (EVT-05 / N4)
def dup_df(rows):
    d = pd.DataFrame(rows, columns=["narrative", "lot_number", "date_received"])
    d["manufacturer_canonical"], d["brand_name"], d["model"] = "Acme", "X", "M1"
    return d[["manufacturer_canonical", "brand_name", "model", "date_received", "lot_number", "narrative"]]


A = "THE LEAD SHOWED INTERMITTENT LOSS OF CAPTURE DURING THE FOLLOW UP VISIT AND THE PHYSICIAN REVIEWED THE ELECTROGRAMS AT LENGTH WITH THE REPRESENTATIVE. "
P = A * 3
tail = lambda n, ch: (ch * n)                                                    # noqa: E731
rows = [
    (P + "ZZZ", None, "20250101"),                              # 0  first report            -> never flagged
    (P + "ZZZ", None, "20250101"),                              # 1  identical                -> Same narrative
    (P + "999999", None, "20250101"),                           # 2  tail differs by 6 chars  -> >= 95% -> Same narrative
    (P + "9" * 60, None, "20250101"),                           # 3  ~85% similar             -> Similar narrative
    ("A COMPLETELY DIFFERENT NARRATIVE ABOUT ANOTHER DEVICE FAILURE ON THE SAME DAY WITH OTHER DETAILS AND TEXT " * 4, None, "20250101"),   # 4 -> not flagged
    ("THIS EVENT IS A DUPLICATE OF FDA REPORT 123. FURTHER TEXT FOLLOWS HERE IN THE NARRATIVE", None, "20250101"),   # 5 -> Text reference
    (P + "ZZZ", None, "20250102"),                              # 6  other day                -> not flagged
    ("KINKED.", None, "20250101"),                              # 7  too short to compare     -> not flagged
]
fd = qc.flag_duplicates(dup_df(rows))
exp = [None, "Same narrative", "Same narrative", "Similar narrative", None, "Text reference", None, None]
check("duplicate basis per report (95% / 80% / reference / different day / too short)", [None if pd.isna(x) else x for x in fd["duplicate_basis"]] == exp, str(fd["duplicate_basis"].tolist()))
check("EVT-05: the START of the narrative need not match (a difference inside the first 200 characters is still compared)",
      [None if pd.isna(x) else x for x in qc.flag_duplicates(dup_df([("AAA " + P, None, "20250101"), ("BBB " + P, None, "20250101")]))["duplicate_basis"]] == [None, "Same narrative"])
sim = qc.text_similarity(P + "ZZZ", P + "9" * 60)
check("the similar example really sits between 80% and 95%", 80 <= sim < 95, f"{sim:.1f}")
check("probable = text reference / same narrative; possible = similar narrative / same lot only",
      fd["is_probable_duplicate"].tolist() == [False, True, True, False, False, True, False, False] and fd["is_possible_duplicate"].tolist() == [False, False, False, True, False, False, False, False])
lots = qc.flag_duplicates(dup_df([(P, "L1", "20250101"), ("COMPLETELY OTHER STORY WITH DIFFERENT WORDS ABOUT A LONG PROCEDURE AND FOLLOW UP " * 3, "L1", "20250101")]))
check("same lot but a different narrative -> 'Same lot only' (possible)", lots["duplicate_basis"].tolist()[1] == "Same lot only" and bool(lots["is_possible_duplicate"].iloc[1]) and not bool(lots["is_probable_duplicate"].iloc[1]))
check("N4: a narrative that is 70% similar is NO LONGER flagged (threshold is 80%)", qc.DUP_POSSIBLE_SIMILARITY == 80.0 and qc.DUP_PROBABLE_SIMILARITY == 95.0)
check("identical text scores exactly 100", qc.text_similarity("ABC DEF", "ABC DEF") == 100.0)

# ============================================================================================ C2
section("C2. MAUDE country codes: standard detection (N2)")
check("detect: only FIPS-only codes (EI, JA, SZ) -> FIPS", cn.detect_code_standard(["EI", "JA", "SZ", "US", "CH"])[0] == "FIPS")
check("detect: only ISO-only codes (IE, DE, JP) -> ISO", cn.detect_code_standard(["IE", "DE", "JP", "US", "CH"])[0] == "ISO")
check("detect: no distinguishing codes -> UNKNOWN", cn.detect_code_standard(["US", "FR", "IT", "CH"])[0] == "UNKNOWN")
check("detect: both present in similar numbers -> MIXED", cn.detect_code_standard(["EI"] * 3 + ["IE"] * 3)[0] == "MIXED")
check("detect: one standard 10x more frequent wins despite a stray code", cn.detect_code_standard(["EI"] * 40 + ["DE"])[0] == "FIPS" and cn.detect_code_standard(["DE"] * 40 + ["EI"])[0] == "ISO")
check("detect: blanks and names are ignored", cn.detect_code_standard([None, "", "Germany", "EI"])[0] == "FIPS")
check("FIPS: CH -> China (the real case: MicroPort NeuroTech (Shanghai))", normalize_country("CH", "maude_native", standard="FIPS") == "China")
check("ISO: CH -> Switzerland", normalize_country("CH", "maude_native", standard="ISO") == "Switzerland")
check("standard unknown: the conflicting code CH is NOT guessed", normalize_country("CH", "maude_native") is None and normalize_country("CH", "maude_native", standard="MIXED") is None)
check("FIPS: SZ -> Switzerland, GM -> Germany, EI -> Ireland, JA -> Japan", [normalize_country(c, "maude_native", standard="FIPS") for c in ("SZ", "GM", "EI", "JA")] == ["Switzerland", "Germany", "Ireland", "Japan"])
check("ISO: DE -> Germany, IE -> Ireland, JP -> Japan", [normalize_country(c, "maude_native", standard="ISO") for c in ("DE", "IE", "JP")] == ["Germany", "Ireland", "Japan"])
check("FIPS: the ambiguous CN is never guessed", normalize_country("CN", "maude_native", standard="FIPS") is None)
check("unambiguous codes decode without a standard (US, FR, EI)", [normalize_country(c, "maude_native") for c in ("US", "FR", "EI")] == ["United States", "France", "Ireland"])
check("a country NAME stays unchanged under any standard", normalize_country("Germany", "maude_native", standard="FIPS") == "Germany")
rows_c = [{"manufacturer_country_raw": c} for c in ("EI", "JA", "CH", "CH", "XX", "N/A", None, "US")]
std, fips_n, iso_n, undec, dec = qc.decode_maude_countries(rows_c)
check("decode_maude_countries: FIPS detected, CH -> China, an unknown code is reported as undecided (not as a raw value)",
      std == "FIPS" and [r["manufacturer_country"] for r in rows_c] == ["Ireland", "Japan", "China", "China", None, None, None, "United States"] and dict(undec) == {"XX": 1})
rows_u = [{"manufacturer_country_raw": c} for c in ("CH", "US", "FR")]
std2, _, _, undec2, _ = qc.decode_maude_countries(rows_u)
check("with no evidence about the standard, CH stays blank and is reported", std2 == "UNKNOWN" and rows_u[0]["manufacturer_country"] is None and dict(undec2) == {"CH": 1})

# ============================================================================================ D
section("D. manufacturer entity resolution (MFR-01) and DUNS logging (LOG-06)")
names = ["Xtant Medical Holdings, Inc.", "XTANT MEDICAL INC", "RTI Surgical, Inc. (dba Pioneer Surgical Technology, Inc.)", "Rti Surgical, Inc.",
         "4-Web Inc.", "4WEB, INC.", "TITAN SPINE, LLC", "Titan Spine, a Division of Medtronic", "Micro Therapeutics, Inc. dba ev3 Neurovascular",
         "MICRO THERAPEUTICS, INC. DBA EV3 NEUROVA", "Micro Therapeutics, Inc.", "Spine Smith Holdings, LLC", "Spine Smith Partners LP",
         "Penumbra, Inc.", "Stryker Corporation", "Boston Scientific Corporation", "Medtronic, Inc.", "ABBOTT", "ABBOTT LABORATORIES"]
cmap, _ = qc.canonicalize_manufacturers_global(names)
for a, b in [("Xtant Medical Holdings, Inc.", "XTANT MEDICAL INC"), ("RTI Surgical, Inc. (dba Pioneer Surgical Technology, Inc.)", "Rti Surgical, Inc."),
             ("4-Web Inc.", "4WEB, INC."), ("TITAN SPINE, LLC", "Titan Spine, a Division of Medtronic"),
             ("Micro Therapeutics, Inc. dba ev3 Neurovascular", "Micro Therapeutics, Inc."), ("MICRO THERAPEUTICS, INC. DBA EV3 NEUROVA", "Micro Therapeutics, Inc."),
             ("Spine Smith Holdings, LLC", "Spine Smith Partners LP"), ("ABBOTT", "ABBOTT LABORATORIES")]:
    check(f"merged: {a[:34]!r} + {b[:30]!r}", cmap[a] == cmap[b])
check("different companies stay separate", len({cmap[n] for n in ("Penumbra, Inc.", "Stryker Corporation", "Boston Scientific Corporation", "Medtronic, Inc.")}) == 4)
check("'Titan Spine, a Division of Medtronic' is not merged into Medtronic, Inc. by name", cmap["Titan Spine, a Division of Medtronic"] != cmap["Medtronic, Inc."])
bio, _ = qc.canonicalize_manufacturers_global(["BIOTRONIK SE & Co. KG", "BIOTRONIK, INC."])
check("BIOTRONIK SE & Co. KG and BIOTRONIK, INC. merge (your decision)", bio["BIOTRONIK SE & Co. KG"] == bio["BIOTRONIK, INC."])
check("matching form ignores the comma that used to block suffix stripping", qc._normalize_for_mfr_matching("Xtant Medical Holdings, Inc.") == "xtant medical")
lg = ListHandler()
logging.getLogger("quality_checks").addHandler(lg)
qc._apply_duns_backstop_merge([[n] for n in ["ABBOTT", "Abbott Medical (CRM-SYLMAR)", "K2M, Inc.", "Penumbra, Inc."]], {"abbott": "078934512", "abbott medical (crm-sylmar)": "078934512"})
check("a refused DUNS merge is logged exactly once", len([r for r in lg.records if "Refusing DUNS" in r.getMessage()]) == 1)

# ============================================================================================ E
section("E. country normalisation (LOG-04)")
check("a country NAME stays unchanged (no warning)", normalize_country("United States", "registrationlisting_api") == "United States")
check("registry (ISO): CH -> Switzerland, DE -> Germany", normalize_country("CH", "registrationlisting_api") == "Switzerland" and normalize_country("DE", "registrationlisting_api") == "Germany")
check("default (FIPS): CH -> China; ambiguous CN not guessed", normalize_country("CH") == "China" and normalize_country("CN") is None)
check("ISO: CN -> China", normalize_country("CN", "registrationlisting_api") == "China")
check("placeholders -> None", normalize_country("UNKNOWN") is None and normalize_country("") is None and normalize_country(None) is None and normalize_country(float("nan")) is None)

# ============================================================================================ F
section("F. reference data (SEM-06, DEV-02, DEV-04, REC-04, EVT-07, TXT-06)")
for name, parent in [("Micro Therapeutics, Inc. dba ev3 Neurovascular", "Medtronic"), ("MDT SOFAMOR DANEK PUERTO RICO MFG", "Medtronic"), ("Warsaw Orthopedic, Inc.", "Medtronic"),
                     ("TITAN SPINE, LLC", "Medtronic"), ("Titan Spine, a Division of Medtronic", "Medtronic"), ("SORIN CRM SAS", "MicroPort"),
                     ("MEDOS INTERNATIONAL S\u00e0RL CH", "Synthes (DePuy Synthes)"), ("Abbott Medical (CRM-SYLMAR)", "Abbott"), ("Penumbra, Inc.", "Penumbra, Inc.")]:
    check(f"parent of {name[:40]!r} = {parent!r}", rd.derive_parent_company(name) == parent)
check("ev3 is NOT under MicroPort", rd.derive_parent_company("MICRO THERAPEUTICS, INC. DBA EV3 NEUROVA") != "MicroPort")
check("display_name strips a trailing country code", rd.display_name("MEDOS INTERNATIONAL S\u00e0RL CH") == "MEDOS INTERNATIONAL S\u00e0RL" and rd.display_name("Medtronic, Inc.") == "Medtronic, Inc.")
long_desc = ("Assurity Model Numbers: PM1240, PM2240; Assurity + Model Number: PM2260; Accent Model Numbers: PM1210. Implantation of a single-chamber "
             "pulse generator is indicated in one or more of the following permanent conditions: Syncope, Presyncope, Fatigue.")
check("recall text -> short device name", rd.short_recall_name(long_desc) == "Assurity")
check("short name keeps a real product phrase", rd.short_recall_name("Surgisis peripheral vascular patch; catalog number C-VSLH-4S-1x10.") == "Surgisis peripheral vascular patch")
check("short name is never longer than 80 characters", len(rd.short_recall_name("Scanlan " * 40)) <= 80)
check("short name never returns a placeholder", rd.short_recall_name("UNKNOWN Model Numbers: PM1240").upper().startswith("UNKNOWN MODEL"))
check("reason_group: Other / Under investigation / Unknown are Vague", [rd.reason_group(x) for x in ("Other", "Under Investigation by firm", "Unknown/Undetermined by firm", "Device Design")] == ["Vague", "Vague", "Vague", "Specific"])
check("problem codes: generic ones are Uninformative", all(rd.problem_code_group(c) == "Uninformative" for c in
      ("Insufficient Device Problem Information", "No Apparent Adverse Event", "Appropriate Device Problem Term/Code Not Available", "Other")) and rd.problem_code_group("Fracture") == "Informative")
check("brand typo override and broken-symbol removal", rd.brand_display("VERTIY ADX") == "Verity ADX" and rd.brand_display(f"CAPSURE{L} LEAD") == "CAPSURE LEAD")
# DEV-04
check("token_key: same words in any order, case and punctuation", rd.token_key("PULSE-GENERATOR, PACEMAKER, IMPLANTABLE") == rd.token_key("Implantable Pacemaker Pulse-Generator"))
check("token_key (real): 'DEVICE NEUROVASCULAR EMBOLIZATION' = 'Device, Neurovascular Embolization'", rd.token_key("DEVICE NEUROVASCULAR EMBOLIZATION") == rd.token_key("Device, Neurovascular Embolization"))
check("token_key: a different device stays different", rd.token_key("Neurovascular embolization coil") != rd.token_key("Device, Neurovascular Embolization"))
check("smart_case: ALL CAPS -> Title Case", rd.smart_case("INTERBODY FUSION DEVICE") == "Interbody Fusion Device" and rd.smart_case("INSTALLER") == "Installer")
check("smart_case: hyphens, small words and acronyms (MRI, PEEK)", rd.smart_case("PULSE-GENERATOR, PACEMAKER, IMPLANTABLE") == "Pulse-Generator, Pacemaker, Implantable"
      and rd.smart_case("CAGE WITH PEEK AND MRI") == "Cage with PEEK and MRI")
check("smart_case: words with digits are kept, mixed case is untouched", rd.smart_case("TRIAL H 10MM") == "Trial H 10MM" and rd.smart_case("Optima Coil") == "Optima Coil")
check("config map: spinal-cage wording under MAX / ODP -> the FDA name (real: 'POLYMERIC SPINAL INTERBODY FUSION CAGE')",
      rd.apply_description_rules("POLYMERIC SPINAL INTERBODY FUSION CAGE", "ODP", "FDA NAME") == "FDA NAME" and rd.apply_description_rules("INTERBODY FUSION DEVICE", "MAX", "FDA NAME") == "FDA NAME")
check("config map: the same wording under another product code is NOT mapped", rd.apply_description_rules("INTERBODY FUSION DEVICE", "DXY", "FDA NAME") is None)
check("config map (real): 'Intervertebal Fusion Device w/Bone Graft, Lumbar' (typo) under MAX is mapped",
      rd.apply_description_rules("Intervertebal Fusion Device w/Bone Graft, Lumbar", "MAX", "FDA NAME") == "FDA NAME" and rd.apply_description_rules("Intervertebral Body Fusion Device", "ODP", "FDA NAME") == "FDA NAME")
check("config map: unrelated descriptions are not mapped", rd.apply_description_rules("Neurovascular embolization coil", "HCG", "FDA NAME") is None)

# ============================================================================================ F2
section("F2. country enrichment order and the placeholder entity (N2, N3)")
from medtech.gold import enrich_manufacturer_country as emc  # noqa: E402
c1 = emc.candidate_names("Micro Therapeutics, Inc. dba ev3 Neurovascular")
check("registry lookup tries the cleaned base name and the parent company", "Micro Therapeutics" in c1 and "Medtronic" in c1 and c1[0] == "Micro Therapeutics, Inc. dba ev3 Neurovascular", str(c1))
check("a country suffix is removed in a candidate", any(c.startswith("MEDOS INTERNATIONAL") and not c.endswith("CH") for c in emc.candidate_names("MEDOS INTERNATIONAL S\u00e0RL CH")))
econn = sqlite3.connect(":memory:")
econn.executescript("CREATE TABLE dim_manufacturer (mfr_key INTEGER PRIMARY KEY, canonical_name TEXT, country TEXT, country_source TEXT, is_placeholder_entity INTEGER DEFAULT 0);")
econn.executemany("INSERT INTO dim_manufacturer VALUES (?,?,?,?,?)", [
    (1, "MicroPort NeuroTech (Shanghai)", "CH", "maude_native", 0), (2, "Real GmbH", "Germany", "maude_native", 0),
    (3, "Unknown", "Italy", "maude_native", 1), (4, "Registry Co", "Switzerland", "registrationlisting_api", 0), (5, "Odd Co", "ZZ", "maude_native", 0)])
emc.normalize_existing_countries(econn)
got = {r[0]: (r[1], r[2]) for r in econn.execute("SELECT mfr_key, country, country_source FROM dim_manufacturer")}
check("N2: a raw code that cannot be decided ('CH', 'ZZ') is BLANKED so the registry can fill it - never left as a code", got[1] == (None, None) and got[5] == (None, None))
check("country names are kept untouched", got[2] == ("Germany", "maude_native") and got[4] == ("Switzerland", "registrationlisting_api"))
check("N3: the 'Unknown' placeholder manufacturer loses its country ('Italy')", got[3] == (None, None))

# ============================================================================================ G
section("G. risk flags: partial months, rolling baseline, watch, direction, parent level (FLG-01..05)")
check("complete months: data from the 27th -> first month is partial", rcz.complete_month_range(date(2024, 10, 27), date(2026, 9, 30)) == (2024 * 12 + 10, 2026 * 12 + 8))
check("complete months: data from the 1st..3rd counts as complete", rcz.complete_month_range(date(2024, 10, 2), date(2026, 9, 30))[0] == 2024 * 12 + 9)
check("complete months: last month ending on the 27th is partial", rcz.complete_month_range(date(2024, 10, 1), date(2026, 10, 27))[1] == 2026 * 12 + 8)
check("no complete month -> None", rcz.complete_month_range(date(2024, 10, 20), date(2024, 10, 28)) is None)


def mc(cat, mfr, key, rows):
    return pd.DataFrame([{"category": cat, "manufacturer": mfr, "mfr_key": key, "yr": y, "mo": m, "event_count": n,
                          "literature_event_count": 0, "probable_duplicate_count": 0, "possible_duplicate_count": 0} for (y, m, n) in rows])


def idx(y, m):
    return y * 12 + m - 1


part = mc("C", "A", 1, [(2024, 10, 3)] + [(2024 + (10 + k) // 12, (10 + k) % 12 + 1, 10) for k in range(0, 8)])
fl_no, _, _ = rcz.compute_flags(part)
fl_yes, allm, _ = rcz.compute_flags(part, complete=(idx(2024, 11), idx(2025, 12)))
check("WITHOUT the filter the partial month produces a false spike (the v10 behaviour)", len(fl_no[(fl_no.yr == 2024) & (fl_no.mo == 11)]) == 1)
check("WITH the filter there is no November spike and no velocity", len(fl_yes[(fl_yes.yr == 2024) & (fl_yes.mo == 11)]) == 0
      and pd.isna(allm[(allm.yr == 2024) & (allm.mo == 11)].iloc[0]["risk_velocity_pct"]) and not (allm.yr * 12 + allm.mo - 1 < idx(2024, 11)).any())
counts = pd.concat([mc("Cardio", "GapMfr", 1, [(2024, 11, 5), (2025, 2, 10)]), mc("Cardio", "ConsecMfr", 2, [(2024, 11, 10), (2024, 12, 20)])], ignore_index=True)
flagged, monthly, _ = rcz.compute_flags(counts)
gap = monthly[monthly["manufacturer"] == "GapMfr"].sort_values(["yr", "mo"])
check("gap months are filled with zero rows", gap["event_count"].tolist() == [5, 0, 0, 10])
feb = gap[(gap.yr == 2025) & (gap.mo == 2)].iloc[0]
check("velocity after a gap is blank, never +100%", pd.isna(feb["risk_velocity_pct"]) and int(feb["prev_month_count"]) == 0 and not (flagged["manufacturer"] == "GapMfr").any())
dec = flagged[(flagged["manufacturer"] == "ConsecMfr") & (flagged["mo"] == 12)].iloc[0]
check("a real consecutive-month +100% IS a velocity spike with a clean label", dec["flag_reason"] == "VELOCITY_SPIKE" and dec["flag_label"] == "Velocity spike" and dec["velocity_direction"] == "Rise")
fl_d, _, _ = rcz.compute_flags(mc("C", "D", 1, [(2025, 1, 12), (2025, 2, 3)]))
row = fl_d.iloc[0]
check("a fall of 75% is a 'Velocity drop', not a spike", row["is_velocity_drop"] == 1 and row["is_velocity_spike"] == 0 and row["flag_label"] == "Velocity drop" and row["flag_reason"] == "VELOCITY_DROP" and row["velocity_direction"] == "Fall")
base = [100, 102, 98, 101, 99, 100, 103, 97, 100, 101]


def cat_series(last):
    return mc("C", "E", 1, [(2025 + k // 12, k % 12 + 1, v) for k, v in enumerate(base + [last])])


fl_w, allw, _ = rcz.compute_flags(cat_series(105))
last = allw.sort_values(["yr", "mo"]).iloc[-1]
check("z-score is reported and 2 <= z < 3 is a WATCH, not a breach", 2 <= last["category_z_score"] < 3 and last["is_statistical_watch"] == 1 and last["is_statistical_breach"] == 0)
check("watch row is flagged with its own label", fl_w.iloc[0]["flag_label"] == "Statistical watch" and fl_w.iloc[0]["flag_reason"] == "STATISTICAL_WATCH")
fl_b, allb, _ = rcz.compute_flags(cat_series(115))
lb = allb.sort_values(["yr", "mo"]).iloc[-1]
check("a count above mean + 3 sd is a BREACH (z > 3)", lb["is_statistical_breach"] == 1 and lb["category_z_score"] > 3 and lb["trailing_ucl"] < 115)
both = mc("C", "H", 1, [(2025, k + 1, v) for k, v in enumerate([20, 21, 19, 20, 22, 20, 21, 20, 19, 20, 70])])
fl_both, _, _ = rcz.compute_flags(both)
check("a rise that is also a breach carries BOTH tags and a combined label", fl_both.iloc[0]["flag_reason"] == "STATISTICAL_BREACH + VELOCITY_SPIKE" and fl_both.iloc[0]["flag_label"] == "Statistical breach + velocity spike")
few = mc("C", "F", 1, [(2025, k, v) for k, v in enumerate([100, 102, 98, 140], start=1)])
fl_f, allf, sup = rcz.compute_flags(few)
check("fewer than 6 prior months: no breach, z blank, is_low_history, reported as suppressed",
      int(allf.is_statistical_breach.sum()) == 0 and allf.category_z_score.isna().all() and (allf.is_low_history == 1).all() and len(sup) >= 1)
_, allr, _ = rcz.compute_flags(mc("C", "G", 1, [(2024 + (k // 12), k % 12 + 1, 50 + (k % 3)) for k in range(24)]))
check("baseline is rolling: at most 12 prior months", int(allr.trailing_history_months.max()) == 12)
check("baseline is the mean of the PRIOR 12 months only", abs(allr.sort_values(["yr", "mo"]).iloc[20]["trailing_mean"] - np.mean([50 + (k % 3) for k in range(8, 20)])) < 1e-9)
unk = pd.concat([mc("C", "Unknown", 9, [(2025, k, 5) for k in range(1, 12)] + [(2025, 12, 60)]), mc("C", "Real", 1, [(2025, k, 5) for k in range(1, 12)] + [(2025, 12, 40)])], ignore_index=True)
fl_u, _, _ = rcz.compute_flags(unk, exclude={"Unknown"})
check("the 'Unknown' entity is never flagged and never attributed a breach", not (fl_u["manufacturer"] == "Unknown").any() and (fl_u["manufacturer"] == "Real").any())
two = pd.concat([mc("C", "Ent1", 1, [(2025, 1, 3), (2025, 2, 6)]), mc("C", "Ent2", 2, [(2025, 1, 3), (2025, 2, 6)])], ignore_index=True)
par = two.assign(manufacturer="Parent").groupby(["category", "manufacturer", "yr", "mo"], as_index=False)[rcz.COUNT_COLS].sum()
fl_ent, _, _ = rcz.compute_flags(two)
fl_par, _, _ = rcz.compute_flags(par, gcols=("category", "manufacturer"))
check("entity level: two entities each +100% (3 -> 6); parent level: ONE row 6 -> 12, +100%",
      len(fl_ent) == 2 and len(fl_par) == 1 and fl_par.iloc[0]["event_count"] == 12 and fl_par.iloc[0]["prev_month_count"] == 6 and fl_par.iloc[0]["risk_velocity_pct"] == 100.0)
extra = dict(top_keywords="", narrative_count=0, sample_narrative_1="", sample_narrative_2="", sample_narrative_3="")
parent_out = rcz.finalize_columns(fl_par.assign(**extra), "parent")
check("parent output has parent_company and no mfr_key", "parent_company" in parent_out.columns and "mfr_key" not in parent_out.columns and "manufacturer" not in parent_out.columns)
ent_out = rcz.finalize_columns(fl_ent.assign(**extra))
check("entity output columns / types", list(ent_out.columns) == rcz.output_columns() and str(ent_out["prev_month_count"].dtype) == "Int64" and (ent_out["risk_velocity_ratio"] - ent_out["risk_velocity_pct"] / 100).abs().max() < 1e-3)
check("empty result still has the full column list", list(rcz.finalize_columns(None).columns) == rcz.output_columns())
kconn = sqlite3.connect(":memory:")
kconn.execute("CREATE TABLE fact_events (narrative_text TEXT)")
for i in range(100):
    kconn.execute("INSERT INTO fact_events VALUES (?)", (f"The template phrase appears here. Failure number{i} occurred with unusualword{i % 5}.",))
stop = rcz.keyword_stopwords(kconn)
check("a word present in > 15% of narratives becomes a stop word; 'redacted' and generic words too; rare words stay",
      "template" in stop and "phrase" in stop and "redacted" in stop and "regarding" in stop and "unusualword1" not in stop)

# ============================================================================================ H
section("H. ingestion window, 404, log rotation")
from medtech.bronze import ingest_maude as im  # noqa: E402
s, e = im.complete_month_window(date(2026, 10, 27))
check("window = 24 COMPLETE months: starts on the 1st, ends on the last day of the previous month", s == date(2024, 10, 1) and e == date(2026, 9, 30))
wins = list(im.window_ranges(s, e))
check("8 windows of 3 months, contiguous, no overlap, no gap, each starting on the 1st",
      len(wins) == 8 and wins[0][0] == s and wins[-1][1] == e and all(b[0] == a[1] + timedelta(days=1) for a, b in zip(wins, wins[1:])) and all(w[0].day == 1 for w in wins))
lg2 = ListHandler()
logging.getLogger("openfda_get").addHandler(lg2)


class FakeResp:
    status_code, text, url = 404, "", "x"


orig_get = common.requests.get
common.requests.get = lambda *a, **k: FakeResp()
common._throttle = lambda: None
common.openfda_get("device/typo.json")
n1 = len([r for r in lg2.records if r.levelno >= logging.ERROR])
common.openfda_get("device/event.json", search='x:"y"')
n2 = len([r for r in lg2.records if r.levelno >= logging.ERROR])
check("404 without a search filter is an ERROR; with a filter it stays quiet", n1 == 1 and n2 == 1)


class Fake500:
    status_code, url = 500, "https://api.fda.gov/x?" + _K + "=" + _V + ""
    text = '{\n  "error": {\n    "code": "SERVER_ERROR",\n    "message": "boom"\n  }\n}'

    def raise_for_status(self):
        raise common.requests.exceptions.HTTPError("500 Server Error for url: " + self.url)


calls = {"n": 0}


def get500(*a, **k):
    calls["n"] += 1
    return Fake500()


common.requests.get = get500
_orig_sleep = common.time.sleep
common.time.sleep = lambda s: None
lg5 = ListHandler()
lg5.addFilter(common.RedactingFilter())
logging.getLogger("openfda_get").addHandler(lg5)
try:
    common.openfda_get("device/event.json", search='x:"y"', max_retries=2)
except common.OpenFDARateLimitError:
    pass
msgs = [r.getMessage() for r in lg5.records if r.levelno == logging.WARNING]
bodies = [m for m in msgs if "5xx body" in m]
check("N1: the failed-request warning (real leak) is masked", msgs and all("ABCD1234" not in m for m in msgs) and any("api_key=***" in m for m in msgs))
check("a 5xx body is logged on ONE line", bodies and all("\n" not in m for m in bodies), str(bodies[:1]))
common.requests.get = orig_get
common.time.sleep = _orig_sleep
tmp = Path(tempfile.mkdtemp())
common.LOG_FILE, common.ARCHIVE_DIR = tmp / "pipeline.log", tmp / "archive"
common.LOG_FILE.write_text("old run\n")
common._rotate_log()
check("previous log archived, none left in place", not common.LOG_FILE.exists() and len(list(common.ARCHIVE_DIR.glob("pipeline_*.log"))) == 1)
shutil.rmtree(tmp, ignore_errors=True)

# ============================================================================================ I
section("I. warehouse loading (DEV-01..05, REC-05, DAT-01, MFR-03, GUDID levels)")
from medtech.gold import load_gold as lgd  # noqa: E402


def fresh_db():
    lgd.reset_caches()
    conn = sqlite3.connect(":memory:")
    conn.executescript(lgd.SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


tmpd = Path(tempfile.mkdtemp())
lgd.SILVER_DIR = tmpd
pd.DataFrame({"date_received": ["20240110", "20260920"]}).to_parquet(tmpd / "maude_silver.parquet", index=False)
lo, hi = lgd._compute_date_bounds()
check("calendar ends at the END OF THE LAST MONTH WITH DATA, not at the run date", lo == date(2024, 1, 10) and hi == date(2026, 9, 30) and hi < date.today())
shutil.rmtree(tmpd, ignore_errors=True)
conn = fresh_db()
lgd.populate_full_calendar(conn, date(2024, 12, 25), date(2025, 1, 8))
row = conn.execute("SELECT week, iso_year, year, year_month_label, year_month_sort FROM dim_date WHERE full_date='2024-12-30'").fetchone()
check("ISO week, iso_year, month label and sort key", row == (1, 2025, 2024, "Dec 2024", 202412) and conn.execute("SELECT COUNT(*) FROM dim_date").fetchone()[0] == 15)

conn = fresh_db()
k_unknown = lgd._get_or_create_mfr(conn, None, country="Italy", country_source="maude_native")
k_unknown2 = lgd._get_or_create_mfr(conn, None, country="Italy", country_source="maude_native")
k_real = lgd._get_or_create_mfr(conn, "Titan Spine, a Division of Medtronic", country="United States", country_source="maude_native")
rows = {r[0]: r[1:] for r in conn.execute("SELECT canonical_name, is_placeholder_entity, country FROM dim_manufacturer")}
check("N3: the 'Unknown' placeholder is marked AND never receives a country (even when one is passed, even on a later call)",
      rows["Unknown"] == (1, None) and k_unknown == k_unknown2, str(rows))
check("a real manufacturer keeps its country; parent_company comes from the mapping file", rows["Titan Spine, a Division of Medtronic"] == (0, "United States")
      and conn.execute("SELECT parent_company FROM dim_manufacturer WHERE mfr_key = ?", (k_real,)).fetchone()[0] == "Medtronic")
conn.execute("UPDATE dim_manufacturer SET country = NULL WHERE mfr_key = ?", (k_unknown,))
lgd._maybe_set_country(conn, k_unknown, "Italy", "maude_native")
check("N3: even a direct _maybe_set_country cannot give the placeholder a country", conn.execute("SELECT country FROM dim_manufacturer WHERE mfr_key = ?", (k_unknown,)).fetchone()[0] is None)

conn = fresh_db()
conn.execute("INSERT INTO dim_device (product_code, brand_name, brand_name_norm, model_norm, category) VALUES ('DXY','ASSURITY','ASSURITY','','Cardiovascular')")
conn.execute("INSERT INTO fact_events (report_number, device_key) VALUES ('R1', 1)")
lgd._event_device_cache = None
dk = lgd._resolve_recall_device(conn, "DXY", "Merlin PCS 3650 programmer Model 3330 software", catalog_number="Serial Numbers: " + "1234567 " * 500)
dev = conn.execute("SELECT brand_name, device_description, catalog_number_observed FROM dim_device WHERE device_key = ?", (dk,)).fetchone()
check("recall-created device gets a SHORT name and NO catalog number", dev == ("Merlin PCS 3650 programmer", "Merlin PCS 3650 programmer", None), str(dev))
check("a recall-created device is not added to the candidate list", all(c[0] != dk for c in lgd._load_event_devices(conn, "DXY")))
lgd._get_or_create_device(conn, "DXY", "SOME BRAND", "x", catalog_number="Lots " + "AB12345, " * 30)
check("a catalog value over 60 characters is dropped and counted", conn.execute("SELECT catalog_number_observed FROM dim_device WHERE brand_name='SOME BRAND'").fetchone()[0] is None and lgd._stats["catalog_dropped_too_long"] == 1)
lgd._get_or_create_device(conn, "DXY", "REAL BRAND", "x", catalog_number="CAT-777")
check("a real catalog number is kept", conn.execute("SELECT catalog_number_observed FROM dim_device WHERE brand_name='REAL BRAND'").fetchone()[0] == "CAT-777")
check("whole-word matching only", lgd._contains_whole("ASSURITY PACEMAKER, STERILE", "ASSURITY") and not lgd._contains_whole("DRIVER SET", "DR"))
check("a real brand is still linked to the device that has events", lgd._resolve_recall_device(conn, "DXY", "ASSURITY PACEMAKER, STERILE, MODEL 4") == 1)
dd_a = lgd._get_or_create_device(conn, "MAX", "HYDROSOFT-3D", "d")
dd_b = lgd._get_or_create_device(conn, "MAX", "HYDROSOFT 3D", "d")
check("brands that differ only by punctuation are ONE device", dd_a == dd_b)
t1 = lgd._get_or_create_device(conn, "MAX", "TRIAL H 10MM 4 DEG 30/9", "d")
t2 = lgd._get_or_create_device(conn, "MAX", "TRIAL H 10MM 4DEG 30/9", "d")
t3 = lgd._get_or_create_device(conn, "MAX", "TRIAL H 9MM 8 DEG 32/12", "d")
check("N6 (real): the two 'TRIAL H' spellings are ONE device; a different size is another device", t1 == t2 and t1 != t3)
n_rep = lgd._get_or_create_device(conn, "DXY", lgd.NOT_REPORTED, None)
n_rep2 = lgd._get_or_create_device(conn, "DXY", lgd.NOT_REPORTED, None)
check("N5: reports with no usable name share ONE '(not reported)' device per product code (never 'UNKNOWN')", n_rep == n_rep2 and conn.execute(
    "SELECT brand_name FROM dim_device WHERE device_key = ?", (n_rep,)).fetchone()[0] == "(not reported)" and conn.execute("SELECT COUNT(*) FROM dim_device WHERE UPPER(brand_name) = 'UNKNOWN'").fetchone()[0] == 0)

tmpd = Path(tempfile.mkdtemp())
lgd.SILVER_DIR = tmpd
base_g = dict(issuing_agency="GS1", commercial_distribution_status="On", is_kit="false", is_single_use="true", is_sterile="true")
pd.DataFrame([
    dict(device_key_raw="g1", product_code="ZZZ", brand_name_norm="ALPHA", sku="CAT-9", primary_di="DI1", mri_safety="MR Safe", product_type="T1", brand_family="Alpha", **base_g),
    dict(device_key_raw="g2", product_code="ZZZ", brand_name_norm="BETA", sku="X1", primary_di="DI2", mri_safety="MR Unsafe", product_type="T2", brand_family="Beta", **base_g),
    dict(device_key_raw="g3", product_code="ZZZ", brand_name_norm="ALPHA", sku="Z9", primary_di="DI9", mri_safety="MR Unsafe", product_type="T9", brand_family="Alpha", **base_g),
]).to_parquet(tmpd / "gudid_silver.parquet", index=False)
conn = fresh_db()
for brand, model, cat in [("ALPHA", "A1", "CAT-9"), ("BETA", "B1", None), ("BETA", "B2", None), ("GAMMA", "G1", None)]:
    conn.execute("INSERT INTO dim_device (product_code, brand_name, brand_name_norm, model_norm, catalog_number_observed, match_method, gudid_match_level, category) "
                 "VALUES ('ZZZ', ?, ?, ?, ?, 'not_matched', 'None', 'X')", (brand, brand, model, cat))
lgd.enrich_devices_with_gudid(conn)
dev = {r[0]: r for r in conn.execute("SELECT brand_name_norm || model_norm, match_method, primary_di, mri_safety, product_type, gudid_enriched, gudid_match_level FROM dim_device")}
check("catalog match: model-level data, level 'Model'", dev["ALPHAA1"][1:4] == ("catalog_number", "DI1", "MR Safe") and dev["ALPHAA1"][6] == "Model")
check("brand-only match: no model-level fields, brand fields filled, level 'Brand'", dev["BETAB1"][2] is None and dev["BETAB1"][3] is None and dev["BETAB1"][4] == "T2" and dev["BETAB1"][6] == "Brand")
check("a brand match enriches every model of that brand", dev["BETAB2"][5] == 1 and dev["BETAB2"][6] == "Brand")
check("unmatched stays level 'None'", dev["GAMMAG1"][1] == "not_matched" and dev["GAMMAG1"][5] == 0 and dev["GAMMAG1"][6] == "None")
shutil.rmtree(tmpd, ignore_errors=True)

# DEV-04
conn = fresh_db()
for pc, name in [("DXY", "Implantable Pacemaker Pulse-Generator"), ("MAX", "Intervertebral Fusion Device With Bone Graft, Lumbar"),
                 ("ODP", "Intervertebral Fusion Device With Bone Graft, Cervical"), ("HCG", "Device, Neurovascular Embolization")]:
    conn.execute("INSERT INTO dim_classification (product_code, device_name_fda) VALUES (?, ?)", (pc, name))
cases_d = [("DXY", "implantable pacemaker Pulse-generator", "A"), ("DXY", "IMPLANTABLE PACEMAKER PULSE-GENERATOR", "B"),            # 0 1  case variants
           ("MAX", "INTERVERTEBRAL FUSION DEVICE WITH BONE GRAFT,", "C"), ("ODP", "INTERVERTEBRAL FUSION DEVICE WITH BONE GRAFT,", "D"),   # 2 3  cut copies
           ("HCG", "HCG, KRD", "E"), ("DXY", "OPTIMA", "Optima"),                                                                          # 4 5  code list / copy of brand
           ("HCG", "Wallaby Avenir Coil system", "F"), ("HCG", "WALLABY AVENIR COIL SYSTEM", "G"), ("HCG", "Wallaby Avenir Coil system", "H"),   # 6 7 8
           ("DXY", "PULSE-GENERATOR, PACEMAKER, IMPLANTABLE", "I"),                                                                        # 9  same words, other order
           ("HCG", "DEVICE NEUROVASCULAR EMBOLIZATION", "J"), ("HCG", "Neurovascular Embolization Device", "K"),                          # 10 11 same words
           ("HCG", "Optima", "OPTIMA COIL SYSTEM"),                                                                                        # 12 first word(s) of the brand
           ("MAX", "INTERBODY FUSION DEVICE", "L"), ("ODP", "POLYMERIC SPINAL INTERBODY FUSION CAGE", "M"),                                # 13 14 config map
           ("MAX", "Intervertebal Fusion Device w/Bone Graft, Lumbar", "N"),                                                               # 15 config map (typo)
           ("HCG", "Neurovascular embolization coil", "O"),                                                                                # 16 a SPECIFIC term, must stay
           ("DXY", "INSTALLER", "P"), ("HCG", None, "Q")]                                                                                  # 17 all caps single word, 18 missing
for i, (pc, desc, brand) in enumerate(cases_d):
    conn.execute("INSERT INTO dim_device (product_code, device_description, brand_name, brand_name_norm, model_norm) VALUES (?,?,?,?,?)", (pc, desc, brand, brand, str(i)))
lgd.standardize_device_descriptions(conn)
got = [r[0] for r in conn.execute("SELECT device_description FROM dim_device ORDER BY device_key")]
PM, LUM, CER, EMB = "Implantable Pacemaker Pulse-Generator", "Intervertebral Fusion Device With Bone Graft, Lumbar", "Intervertebral Fusion Device With Bone Graft, Cervical", "Device, Neurovascular Embolization"
check("case variants of the FDA name become the FDA name", got[0] == got[1] == PM, str(got[:2]))
check("cut text 'BONE GRAFT,' becomes the FDA name of ITS OWN product code", got[2] == LUM and got[3] == CER)
check("a product-code list ('HCG, KRD') and a copy of the brand become the FDA name", got[4] == EMB and got[5] == PM)
check("remaining case variants share one mixed-case spelling", got[6] == got[7] == got[8] == "Wallaby Avenir Coil system")
check("DEV-04: the SAME WORDS in another order become the FDA name (real: 'PULSE-GENERATOR, PACEMAKER, IMPLANTABLE')", got[9] == PM)
check("DEV-04: 'DEVICE NEUROVASCULAR EMBOLIZATION' and 'Neurovascular Embolization Device' become the FDA name", got[10] == EMB and got[11] == EMB)
check("DEV-04: a description that is the first word of the brand ('Optima' / 'OPTIMA COIL SYSTEM') becomes the FDA name", got[12] == EMB)
check("DEV-04: config-map descriptions become the FDA name of their product code (spinal cage wording, typo variant)", got[13] == LUM and got[14] == CER and got[15] == LUM)
check("DEV-04: a genuinely SPECIFIC term ('Neurovascular embolization coil') is kept", got[16] == "Neurovascular embolization coil")
check("DEV-04: remaining ALL-CAPS text is Title-Cased", got[17] == "Installer")
check("DEV-04: a missing description becomes the FDA name", got[18] == EMB)
check("device_type_fda is filled for every device", conn.execute("SELECT COUNT(*) FROM dim_device WHERE device_type_fda IS NULL").fetchone()[0] == 0)
check("no description is left ALL CAPS, empty or ending in punctuation", all(g and not (g.isupper() and len(g.split()) >= 2) and not g.rstrip().endswith(",") for g in got))

# ============================================================================================ J
section("J. export typing, the Excel cell limit and the credential check (REC-01, FMT-07/08/10, N1)")
from medtech.export import export_to_powerbi as exp  # noqa: E402
from medtech.export import validate_export as ve  # noqa: E402
big = pd.DataFrame({"recall_id": ["1", "2"], "code_info": ["x" * 40000, "short"]})
cut_df, cut_cols = exp.enforce_cell_limit("recall_compliance_ref", big)
check("a 40,000-character value is cut below Excel's limit with a marker", cut_cols == ["code_info"] and len(cut_df.loc[0, "code_info"]) < exp.EXCEL_CELL_LIMIT and "[truncated" in cut_df.loc[0, "code_info"])
check("short values are untouched", cut_df.loc[1, "code_info"] == "short")
prep = exp._prepare("dim_manufacturer", pd.DataFrame({"duns_number": ["006261481", "078934512"]}))
check("identifier column stays text with leading zeros", prep["duns_number"].tolist() == ["006261481", "078934512"])
check("dim_date.full_date becomes a real datetime", pd.api.types.is_datetime64_any_dtype(exp._prepare("dim_date", pd.DataFrame({"date_key": [1], "full_date": ["2024-10-04"]}))["full_date"]))
check("float counts become nullable integers", str(exp._prepare("root_cause_flags", pd.DataFrame({"prev_month_count": [1.0, np.nan], "trailing_history_months": [2.0, 3.0]}))["prev_month_count"].dtype) == "Int64")
tdir = Path(tempfile.mkdtemp())
good, bad = tdir / "good.xlsx", tdir / "bad.xlsx"
with pd.ExcelWriter(good, engine="xlsxwriter") as w:
    pd.DataFrame({"duns_number": ["006261481", "078934512"]}).to_excel(w, sheet_name="dim_manufacturer", index=False)
with pd.ExcelWriter(bad, engine="xlsxwriter") as w:
    pd.DataFrame({"duns_number": [6261481, 78934512]}).to_excel(w, sheet_name="dim_manufacturer", index=False)
check("on-disk check passes for text cells and catches numeric cells", exp.verify_text_cells(good, {"dim_manufacturer": ["duns_number"]}) == []
      and len(exp.verify_text_cells(bad, {"dim_manufacturer": ["duns_number"]})) == 1)
leaky, clean = tdir / "leaky.log", tdir / "clean.log"
leaky.write_text("INFO | ok\nWARNING | request failed: 500 for url: https://api.fda.gov/x?limit=1&" + _K + "=" + _V + ". Retrying\n", encoding="utf-8")
clean.write_text("INFO | ok\nWARNING | request failed: 500 for url: https://api.fda.gov/x?limit=1&api_key=***. Retrying\n", encoding="utf-8")
rp1, rp2, rp3 = ve.Report(), ve.Report(), ve.Report()
ve.check_log_secrets(leaky, rp1)
ve.check_log_secrets(clean, rp2)
ve.check_log_secrets(tdir / "missing.log", rp3)
check("N1: the validator flags a log that contains an api_key value (HIGH)", [c for c, _ in rp1.findings["HIGH"]] == ["log-secrets"])
check("N1: a log with masked keys passes", [c for c, _ in rp2.findings["PASS"]] == ["log-secrets"] and not rp2.findings["HIGH"])
check("a missing log is reported, not a crash", [c for c, _ in rp3.findings["LOW"]] == ["log-secrets"])
shutil.rmtree(tdir, ignore_errors=True)

# ============================================================================================ K
section("K. GUDID pagination regression (v9, retained)")
from medtech.bronze import ingest_gudid as ig  # noqa: E402
import random  # noqa: E402
random.seed(7)
total = 42000
span = (date.today() - date(2010, 1, 1)).days
recs = sorted(({"public_device_record_key": f"F{i}", "publish_date": (date(2010, 1, 1) + timedelta(days=random.randint(0, span))).isoformat()} for i in range(total)),
              key=lambda r: r["publish_date"])


def fake_get(endpoint, search="", limit=1000, skip=0, extra_params=None, max_retries=6):
    import re as _re
    m = _re.search(r"publish_date:\[(\d{4}-\d{2}-\d{2}) TO (\d{4}-\d{2}-\d{2})\]", search)
    sel = [r for r in recs if m.group(1) <= r["publish_date"] <= m.group(2)] if m else recs
    return {"meta": {"results": {"total": len(sel)}}, "results": sel[skip:skip + limit]}


common.openfda_get = fake_get
ig.openfda_get = fake_get
ig.openfda_paginate = common.openfda_paginate
common.time.sleep = lambda s: None
lg3 = ListHandler()
logging.getLogger("openfda_paginate").addHandler(lg3)
unchunked = list(common.openfda_paginate("device/udi.json", search='product_codes.code:"ZZZ"'))
check("unchunked query is truncated and the detector logs it", len(unchunked) < total and len([r for r in lg3.records if "TRUNCATION" in r.getMessage()]) == 1)
lg3.records.clear()
chunked = ig._fetch_chunked("ZZZ", ig.GUDID_EARLIEST_DATE, date.today())
keys = [r["public_device_record_key"] for r in chunked]
check("chunked fetch returns the full registry with no duplicates and no truncation", len(chunked) == total and len(set(keys)) == total
      and not [r for r in lg3.records if "TRUNCATION" in r.getMessage()])

## ============================================================================================ L

section("L. Gold flag tables, export reading, runner and CLI (repository restructure)")
import subprocess  # noqa: E402
from medtech import pipeline as pl  # noqa: E402
from medtech import __main__ as cli  # noqa: E402

# L1 -- the two flag tables are Gold tables in the warehouse (they used to be CSV files)
fconn = sqlite3.connect(":memory:")
flag_in = rcz.finalize_columns(fl_ent.assign(**extra)).reset_index(drop=True)
flag_in.loc[0, "top_keywords"] = ""                                    # blank text must come back as NULL, not as an empty string
rcz.write_flag_table(fconn, rcz.FLAG_TABLES["manufacturer"], flag_in)
flag_out = pd.read_sql_query("SELECT * FROM root_cause_flags", fconn)
check("L1: the flag tables are named root_cause_flags / root_cause_flags_parent", rcz.FLAG_TABLES == {"manufacturer": "root_cause_flags", "parent": "root_cause_flags_parent"})
check("L1: write_flag_table keeps every row and column, in the output order", list(flag_out.columns) == rcz.output_columns() and len(flag_out) == len(flag_in))
check("L1: numbers round-trip exactly, blank text becomes NULL",
      bool((flag_out["event_count"] == flag_in["event_count"]).all()) and bool((flag_out["risk_velocity_pct"].fillna(-1) == flag_in["risk_velocity_pct"].astype(float).fillna(-1)).all())
      and flag_out.loc[0, "top_keywords"] is None)
rcz.write_flag_table(fconn, "root_cause_flags", flag_in.head(1))
check("L1: a second write replaces the table (no rows left over from the previous run)", fconn.execute("SELECT COUNT(*) FROM root_cause_flags").fetchone()[0] == 1)
rcz.write_flag_table(fconn, "root_cause_flags_parent", rcz.finalize_columns(None, "parent"))
check("L1: an empty result still creates the table with its columns",
      [r[1] for r in fconn.execute("PRAGMA table_info(root_cause_flags_parent)")] == rcz.output_columns("parent"))

# L2 -- the export reads the flags from the warehouse; a missing flag table gives an empty sheet, any other missing table is an error
econn2 = sqlite3.connect(":memory:")
rcz.write_flag_table(econn2, "root_cause_flags", flag_in)
check("L2: export reads a flag table from the warehouse", len(exp.read_gold_table(econn2, "root_cause_flags")) == len(flag_in))
empty_parent = exp.read_gold_table(econn2, "root_cause_flags_parent")
check("L2: a missing flag table becomes an EMPTY sheet with the right columns (the validator then reports it)", empty_parent.empty and list(empty_parent.columns) == rcz.output_columns("parent"))
try:
    exp.read_gold_table(econn2, "fact_events")
    raised = False
except Exception:      # noqa: BLE001
    raised = True
check("L2: any other missing Gold table is an error, not a silent empty sheet", raised)
check("L2: the export sheet list includes both flag tables and no CSV path is left",
      "root_cause_flags" in exp.TABLES and "root_cause_flags_parent" in exp.TABLES and not hasattr(exp, "ROOT_CAUSE_CSV") and not hasattr(rcz, "OUTPUT_PATH"))

# L3 -- the runner: stage order, clean Gold rebuild, quality-gate exit code
calls = []
class FakeStage:
    def __init__(self, name):
        self.name = name
    def run(self):
        calls.append(self.name)
        return 2 if self.name == pl.QUALITY_GATE else None
class FakeImportlib:
    @staticmethod
    def import_module(name):
        return FakeStage(name)
real_importlib, real_reset = pl.importlib, pl.reset_gold
resets = []
pl.importlib, pl.reset_gold = FakeImportlib, (lambda: resets.append(True))
try:
    code_gold = pl.run("gold")
    calls_gold, resets_gold = list(calls), len(resets)
    calls.clear(); resets.clear()
    code_full = pl.run("bronze")
    calls_full, resets_full = list(calls), len(resets)
    calls.clear(); resets.clear()
    pl.run("silver")
    calls_silver = list(calls)
finally:
    pl.importlib, pl.reset_gold = real_importlib, real_reset
check("L3: 'run' returns the quality gate's exit code (it used to be dropped, so a CRITICAL finding still exited 0)", code_gold == 2 and code_full == 2)
check("L3: from=gold starts at the Gold load, from=silver at the quality checks, from=bronze at the first download",
      calls_gold[0] == pl.LOAD_GOLD and calls_silver[0] == "medtech.silver.quality_checks" and calls_full[0] == "medtech.bronze.ingest_maude" and calls_gold[-1] == pl.QUALITY_GATE)
check("L3: the 10 stages run in order and the layers are bronze -> silver -> gold -> export",
      len(calls_full) == 10 and [s[0] for s in pl.STAGES] == ["bronze"] * 4 + ["silver"] + ["gold"] * 3 + ["export"] * 2)
check("L3: Gold is reset for every run that includes the Gold load", resets_gold == 1 and resets_full == 1)
gdir = Path(tempfile.mkdtemp())
for n in ("medtech_warehouse.db", "medtech_warehouse.db-journal", "PowerBI_Export.xlsx"):
    (gdir / n).write_text("x")
real_gold = pl.GOLD_DIR
pl.GOLD_DIR = gdir
try:
    pl.reset_gold()
finally:
    pl.GOLD_DIR = real_gold
check("L3: reset_gold removes the warehouse (and its journal) but never the export", sorted(p.name for p in gdir.iterdir()) == ["PowerBI_Export.xlsx"])
shutil.rmtree(gdir, ignore_errors=True)

# L4 -- command line
check("L4: 'run --from gold' and the default 'run' parse", cli.build_parser().parse_args(["run", "--from", "gold"]).start == "gold" and cli.build_parser().parse_args(["run"]).start == "bronze")
check("L4: 'validate' takes a path, --log and --json", (lambda a: (a.export, a.log, a.json))(cli.build_parser().parse_args(["validate", "x.xlsx", "--log", "p.log", "--json", "o.json"])) == ("x.xlsx", "p.log", "o.json"))
try:
    cli.build_parser().parse_args(["run", "--from", "nowhere"])
    bad_choice = False
except SystemExit:
    bad_choice = True
check("L4: an unknown layer is rejected", bad_choice)

# L5 -- the offline fixture refuses to delete real Bronze data
sandbox = Path(tempfile.mkdtemp())
(sandbox / "tests").mkdir()
(sandbox / "data" / "bronze").mkdir(parents=True)
shutil.copy(ROOT / "tests" / "offline_fixture.py", sandbox / "tests" / "offline_fixture.py")
real_file = sandbox / "data" / "bronze" / "maude_DXY_20250101_20250331_20261104T101010Z.json"
real_file.write_text("[]")
proc = subprocess.run([sys.executable, str(sandbox / "tests" / "offline_fixture.py")], capture_output=True, text=True)
check("L5: the fixture stops when Bronze holds real data, and leaves that data untouched", proc.returncode != 0 and real_file.exists() and "Refusing" in (proc.stderr + proc.stdout))
real_file.unlink()
proc = subprocess.run([sys.executable, str(sandbox / "tests" / "offline_fixture.py")], capture_output=True, text=True)
n_fix = len(list((sandbox / "data" / "bronze").glob("*_fixture.json")))
check("L5: on an empty Bronze folder the fixture writes its 55 files", proc.returncode == 0 and n_fix == 55, f"{proc.returncode} {n_fix} {proc.stderr[-200:]}")
shutil.rmtree(sandbox, ignore_errors=True)

## ============================================================================================ M

section("M. root scripts: python run_pipeline.py / resume_pipeline.py / validate_export.py (also from the VS Code Run button)")
import run_pipeline as rp  # noqa: E402  (the project root is on sys.path)

elsewhere = tempfile.mkdtemp()      # VS Code may start a script with any working directory
def run_script(name, *args):
    return subprocess.run([sys.executable, str(ROOT / name), *args], capture_output=True, text=True, cwd=elsewhere)

for name, token in (("run_pipeline.py", "--from"), ("resume_pipeline.py", "--status"), ("validate_export.py", "--log")):
    check(f"M1: {name} exists in the project root", (ROOT / name).is_file())
    proc = run_script(name, "--help")
    check(f"M1: python {name} --help works from another working directory (no 'No module named medtech')",
          proc.returncode == 0 and token in proc.stdout and "No module named" not in proc.stderr, proc.stderr[-200:])
shutil.rmtree(elsewhere, ignore_errors=True)

check("M2: run_pipeline: default start is bronze; --from silver / gold accepted", rp.build_parser().parse_args([]).start == "bronze"
      and rp.build_parser().parse_args(["--from", "gold"]).start == "gold")
resume_parser = rp.build_parser("silver", ("silver", "gold"))
try:
    resume_parser.parse_args(["--from", "bronze"])
    resume_rejects = False
except SystemExit:
    resume_rejects = True
check("M2: resume_pipeline: default start is silver and 'bronze' is not offered (it has no download)", resume_parser.parse_args([]).start == "silver" and resume_rejects)

seen = {}
real_run, real_status = pl.run, pl.status
def fake_run(start="bronze"):
    seen["start"] = start
    return 2


pl.run = fake_run
pl.status = lambda: seen.update(status=True)
try:
    code_full = rp.main([])
    start_full = seen.get("start")
    seen.clear()
    code_gold = rp.main(["--from", "gold"])
    start_gold = seen.get("start")
    seen.clear()
    code_status = rp.main(["--status"])
    ran_on_status = "start" in seen
    seen.clear()
    code_resume = rp.main([], default_start="silver", layers=("silver", "gold"))
    start_resume = seen.get("start")
finally:
    pl.run, pl.status = real_run, real_status
check("M3: python run_pipeline.py runs from bronze and returns the quality gate's exit code", start_full == "bronze" and code_full == 2)
check("M3: --from gold passes 'gold' to the pipeline", start_gold == "gold" and code_gold == 2)
check("M3: --status only shows the layers: nothing is run, exit code 0", code_status == 0 and not ran_on_status)
check("M3: resume_pipeline defaults to silver", start_resume == "silver" and code_resume == 2)

print("\n" + "=" * 70)
if failures:
    print(f"RESULT: {len(failures)} FAILED, {passed} passed")
    for f in failures:
        print("   -", f)
    sys.exit(1)
print(f"RESULT: ALL {passed} CHECKS PASSED")
sys.exit(0)
