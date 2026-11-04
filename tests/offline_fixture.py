"""
Synthetic Bronze JSON in the real openFDA shapes (no network needed).

    MEDTECH_OFFLINE=1 python tests/offline_fixture.py      # MEDTECH_OFFLINE=1 skips the openFDA registry lookups
    MEDTECH_OFFLINE=1 python run_pipeline.py --from silver

It replaces the *_fixture.json files in data/bronze and refuses to run when that folder holds anything else (real downloaded data).

It deliberately reproduces every pathology the v10 / v11 / v12 fixes address:
  partial first / last months; a category-level spike (a real breach); placeholders ('NA', 'UNKNOWN') in age / lot / catalog / brand; a report
  with NO usable brand and NO usable generic name; lost characters in many contexts (apostrophe, range, bullet, quote before [redacted],
  quote after ']' and '?'); legal boilerplate of several firms; sentences repeated across many narratives AND repeated inside one narrative
  (the second copy starting in lower case); journal-literature reports (template, citation and 'reported through literature review');
  probable duplicates (identical), similar duplicates (same start, ~85% similar text) and same-lot duplicates; PMA-style and multi-lot values;
  'Prefer Not To Disclose'; manufacturers that differ by a 'dba' tail / holdings suffix / 'Division of'; a silent manufacturer;
  MAUDE manufacturer countries as FIPS codes including 'CH' (= China under FIPS) and the placeholder 'UNKNOWN' manufacturer WITH a country;
  device names that differ by a space before a unit; descriptions that are the FDA name in another word order; recalls with total / bracketed
  quantities, a 40,000-character lot list, vague reasons and very long product descriptions.
"""
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from dateutil.relativedelta import relativedelta

random.seed(42)
LOST = "\u00bf"

ROOT = Path(__file__).resolve().parents[1]
BRONZE = ROOT / "data" / "bronze"
BRONZE.mkdir(parents=True, exist_ok=True)
_real = [f.name for f in BRONZE.glob("*.json") if not f.name.endswith("_fixture.json")]
if _real:       # this script replaces the Bronze folder: refuse to do that to real downloaded data
    sys.exit(f"Refusing to continue: data/bronze holds {len(_real)} file(s) that are not offline-fixture files (for example {_real[0]}).\n"
             "Run the offline test in a clean checkout, or move data/ away first.")
for f in BRONZE.glob("*_fixture.json"):
    f.unlink()

CODES = ["DXY", "QJP", "MAX", "HCG", "ODP"]
BRANDS = {
    "DXY": ["Assurity Pacemaker", "ASSURITY PACEMAKER", "Endurity MRI", "Accent DR"],
    "QJP": ["Phenom Catheter", "PHENOM CATHETER", "Trevo ProVue", "Solitaire X", "HYDROSOFT-3D", "HYDROSOFT 3D"],
    "MAX": ["Capstone Spinal System", "CAPSTONE SPINAL SYSTEM", "Clydesdale PTC", "CAVUX CERVICAL CAGE-X, 4MM", "CAVUX CERVICAL CAGE-X. 4MM",
            "TRIAL H 10MM 4 DEG 30/9", "TRIAL H 10MM 4DEG 30/9"],
    "HCG": ["Onyx Liquid Embolic", "Axium Coil", "Target Detachable Coil", "Optima Coil System"],
    "ODP": ["Cornerstone PSR", "Anatomic PEEK", "Zevo Cervical Plate"],
}
FDA_NAMES = {"DXY": "Implantable Pacemaker Pulse-Generator", "QJP": "Catheter, Percutaneous, Neurovasculature",
             "MAX": "Intervertebral Fusion Device With Bone Graft, Lumbar", "HCG": "Device, Neurovascular Embolization",
             "ODP": "Intervertebral Fusion Device With Bone Graft, Cervical"}
GENERIC = {"DXY": ["implantable pacemaker Pulse-generator", "IMPLANTABLE PACEMAKER PULSE-GENERATOR", "PULSE-GENERATOR, PACEMAKER, IMPLANTABLE"],
           "QJP": ["Catheter, percutaneous, neurovasculature"],
           "MAX": ["INTERVERTEBRAL FUSION DEVICE WITH BONE GRAFT,", "Intervertebral Fusion Device With Bone Graft, Lumbar", "INTERBODY FUSION DEVICE",
                   "Intervertebal Fusion Device w/Bone Graft, Lumbar"],
           "HCG": ["HCG, KRD", "Device, Neurovascular Embolization", "Neurovascular Embolization Device", "DEVICE NEUROVASCULAR EMBOLIZATION"],
           "ODP": ["Intervertebral fusion device with bone graft, cervical", "POLYMERIC SPINAL INTERBODY FUSION CAGE"]}
SHARED_MFRS = [
    ("Medtronic, Inc.", "US"), ("MEDTRONIC IRELAND", "EI"), ("Medtronic Sofamor Danek USA, Inc", "US"),
    ("MDT SOFAMOR DANEK PUERTO RICO MFG", "RQ"), ("Micro Therapeutics, Inc. dba ev3 Neurovascular", "US"),
    ("MICRO THERAPEUTICS, INC. DBA EV3 NEUROVA", "US"), ("Micro Therapeutics, Inc.", "US"),
    ("Titan Spine, a Division of Medtronic", "US"), ("TITAN SPINE, LLC", None),
    ("Xtant Medical Holdings, Inc.", "US"), ("XTANT MEDICAL INC", "US"), ("4-Web Inc.", "US"), ("4WEB, INC.", "US"),
    ("Stryker Corporation", "US"), ("Stryker Neurovascular Cork", "EI"), ("ABBOTT", "US"), ("ABBOTT LABORATORIES", "US"),
    ("Abbott Medical (CRM-SYLMAR)", "US"), ("Boston Scientific Corporation", "US"), ("MicroPort CRM S.r.l.", "IT"),
    ("Penumbra, Inc.", "US"), ("MicroVention, Inc.", "JA"), ("Balt USA", "FR"), ("Neuravi Ltd.", "EI"), ("K2M, Inc.", "US"),
    ("MEDOS INTERNATIONAL S\u00e0RL CH", "SZ"), ("UNKNOWN", "IT"), ("N/A", None), ("Terumo Medical Corporation", "JA"),
    ("W. L. Gore & Associates", "US"), ("Warsaw Orthopedic, Inc.", "US"),
    ("MicroPort NeuroTech (Shanghai) Co., Ltd.", "CH"), ("COVIDIEN MEDICAL PRODUCTS", "CH"),
]
_DUNS_GROUPS = [["Xtant Medical Holdings, Inc.", "XTANT MEDICAL INC"], ["4-Web Inc.", "4WEB, INC."], ["ABBOTT", "ABBOTT LABORATORIES"],
                ["Micro Therapeutics, Inc. dba ev3 Neurovascular", "MICRO THERAPEUTICS, INC. DBA EV3 NEUROVA", "Micro Therapeutics, Inc."],
                ["Titan Spine, a Division of Medtronic", "TITAN SPINE, LLC"]]
DUNS_OF = {name: f"{(i + 1) * 7919:09d}" for i, (name, _c) in enumerate(SHARED_MFRS)}
for _g in _DUNS_GROUPS:
    for _n in _g:
        DUNS_OF[_n] = DUNS_OF[_g[0]]
MFRS_MAUDE = SHARED_MFRS + [("Alphatec Spine, Inc.", "US")]
MFRS_RECALLS = SHARED_MFRS + [("ALPHATEC SPINE, INC.", "US")]

CORES = [
    "THE LEAD EXHIBITED UNDERSENSING DURING FOLLOW UP VISIT {uid} AND WAS REPLACED.",
    "THE PHYSICIAN SAID " + LOST + "THE DEVICE LOOKS WORN" + LOST + " AT CASE {uid}.",
    "IMPLANT MIGRATION WAS NOTED ON IMAGING FOR CASE {uid}. THE INNOMINATE VEIN" + LOST + "SUPERIOR VENA CAVA JUNCTION WAS NARROWED.",
    "THE PATIENT" + LOST + "S CONDITION WAS STABLE AFTER PROCEDURE {uid}.",
    "REFERENCE 2023; 9:E615" + LOST + "E623 FOR CASE {uid}. THE DEVICE WAS EXPLANTED ON (B)(6) 2024.",
    "PRODUCT ANALYSIS FOR CASE {uid}: " + LOST + " EQUIPMENT USED: VIDEO INSPECTION SYSTEM. " + LOST + " NO DEFECT WAS FOUND.",
    "THE SEARCH FIELD WAS CORRECTED TO \"(B)(6)" + LOST + ". CASE {uid} CLOSED.",                  # redaction marker, then a lost closing quote
    "WAS THERE A PRIOR TREATMENT FOR CASE {uid}?" + LOST + " A MANUFACTURING RECORD EVALUATION WAS PERFORMED.",
    "FRACTURE OF THE DISTAL TIP WAS OBSERVED IN CASE {uid}.",
    "ADDITIONAL INFORMATION WAS RECEIVED FROM THE FIELD FOR CASE {uid} AND THE PROCEDURE WAS COMPLETED.",
    "REVIEW OF IMAGING FOR CASE {uid} SHOWED NO ISSUE. review of imaging for case {uid} showed no issue.",          # repeat starting in lower case
]
SPLIT_CORE = "ADDI TIONAL INFORMATION WAS REQUESTED FROM THE FIELD FOR CASE {uid} AND THE PROCEDURE WAS COMPLETED."   # rare, as in real data
COMMON = [
    "IF THE DEVICE RETURNS, A DEVICE INVESTIGATION WILL BE PERFORMED.",
    "NO PATIENT COMPLICATIONS HAVE BEEN REPORTED AS A RESULT OF THIS EVENT.",
    "SINCE THERE WAS NO EVIDENCE TO SUGGEST THE EVENT WAS RELATED TO A MANUFACTURING OR DESIGN ISSUE, NO CORRECTIVE ACTIONS WILL BE TAKEN.",
    "THE MANUFACTURER WILL SUBMIT A SUPPLEMENTAL REPORT IF NEW FACTS ARISE WHICH MATERIALLY ALTER INFORMATION.",
]
BOILER = [
    "MEDTRONIC SUBMITS THIS REPORT TO COMPLY WITH FDA REGULATIONS 21 CFR PARTS 4 AND 803.",
    "THE SUBMISSION OF THIS REPORT DOES NOT CONSTITUTE AN ADMISSION BY CARLSMED, ITS EMPLOYEES, OR THE FDA THAT THE DEVICE CAUSED THE EVENT.",
    "ANY REQUIRED FIELDS THAT ARE UNPOPULATED ARE BLANK BECAUSE THE INFORMATION IS CURRENTLY UNKNOWN.",
    "HAPPE SPINE HAS MADE REASONABLE EFFORTS TO PROVIDE AS MUCH INFORMATION AS IS AVAILABLE TO THE COMPANY AT THE TIME OF THE SUBMISSION DATE OF THIS REPORT.",
]
LIT_TEMPLATE = ("THIS INFORMATION IS BASED ENTIRELY ON JOURNAL LITERATURE. THE BASELINE GENDER/AGE CHARACTERISTICS IS MALE/68 YEARS OLD. "
                "REFERENCED ARTICLE {uid}: DEVICE INFECTION RISK SCORE.")
LIT_CITATION = ["MEDTRONIC REVIEW OF THE LITERATURE ARTICLE FOUND A RETROSPECTIVE REVIEW OF 88 PATIENTS (CASE {uid}).",
                "NO ADVERSE PATIENT EFFECTS WERE REPORTED. TOKUDOME, DAIGO, ET AL. (2026). EVALUATION OF ICD DISCRIMINATION {uid}.",
                "IT WAS REPORTED THROUGH LITERATURE REVIEW THAT THE PATIENT" + LOST + "S DEVICE WAS ERODED (CASE {uid})."]
OUTCOMES = ["Required Intervention", "Hospitalization", "Life Threatening", "Disability"]
CODE_OF = {"Required Intervention": "R", "Hospitalization": "H", "Life Threatening": "L", "Disability": "S"}
PROBLEMS = ["Fracture", "Migration", "Appropriate Device Problem Term/Code Not Available", "Insufficient Device Problem Information", "Over-Sensing", "Other"]

TODAY = date.today()
START = TODAY - relativedelta(months=24)          # mid-month on purpose: the first month is PARTIAL
END = TODAY                                       # the current month is still filling: the last month is PARTIAL
RECALL_START = TODAY - relativedelta(years=12)


def rand_date(a, b):
    return a + timedelta(days=random.randint(0, max((b - a).days, 1)))


def narrative():
    uid = str(random.randint(100000, 999999))
    core = (SPLIT_CORE if random.random() < 0.003 else random.choice(CORES)).format(uid=uid)
    parts = [core] + random.sample(COMMON, k=random.randint(0, 2))
    if random.random() < 0.25:
        parts.append(random.choice(BOILER))
    return " ".join(parts)


def make_event(no, code, mfr, country, d, narr=None, kind="normal"):
    brand = random.choice(BRANDS[code])
    generic = random.choice(GENERIC[code])
    if brand.startswith("Optima") and random.random() < 0.6:
        generic = "Optima"
    unnamed = random.random() < 0.01                    # a report with neither a usable brand nor a usable generic name
    if unnamed:
        brand, generic = "UNKNOWN", "N/A"
    problems = random.sample(PROBLEMS, k=random.randint(0, 2))
    if problems and random.random() < 0.4:
        problems = problems + problems
    outcome = random.choice(OUTCOMES + [None])
    out_field = outcome
    if outcome and random.random() < 0.3:
        out_field = f"{outcome}; {CODE_OF[outcome]}"
    if outcome and random.random() < 0.2:
        second = random.choice(OUTCOMES)
        out_field = f"{outcome}; {second}" if random.random() < 0.5 else f"{second}; {outcome}"
    uid = str(random.randint(100000, 999999))
    if narr is None:
        if kind == "literature":
            narr = LIT_TEMPLATE.format(uid=uid) if random.random() < 0.5 else random.choice(LIT_CITATION).format(uid=uid)
        elif random.random() < 0.1:
            narr = None
        else:
            narr = narrative()
    country_field = {}
    if country:                                    # exercise all three candidate MAUDE country fields
        pick = random.random()
        country_field = ({"device": None} if pick < 0.5 else {"manufacturer_g1_country": country} if pick < 0.8 else {"manufacturer_country": country})
    rec = {
        "report_number": str(no), "date_received": d.strftime("%Y%m%d"),
        "event_type": random.choices(["Malfunction", "Injury", "Death", "Other"], weights=[73, 24, 3, 1])[0],
        "manufacturer_name": mfr, "manufacturer_state": random.choice(["CA", "MN", "MA", None]), "manufacturer_city": random.choice(["Irvine", "Minneapolis", None]),
        "device": [{"manufacturer_d_name": mfr, "device_report_product_code": code, "generic_name": generic, "brand_name": brand,
                    "model_number": random.choice(["M100", "M200", None]), "catalog_number": random.choice(["CAT-1", "CAT-2", "CAT-3", "NA", None]),
                    "lot_number": random.choice(["LOT9", "UNKNOWN", "0002210171", "P000121175", "L1, L2", None]),
                    **({"manufacturer_d_country": country} if country and "device" in country_field else {})}],
        "mdr_text": ([{"text": narr}] if narr else []),
        "patient": [{"patient_age": random.choice(["56 YR", "68 YR", "2 DA", "NA", None]),
                     "patient_sex": random.choice(["Female", "Male", "Unknown", "Prefer Not To Disclose"]), "sequence_number_outcome": out_field}],
        "product_problems": problems,
    }
    for k, v in country_field.items():
        if k != "device":
            rec[k] = v
    return rec


report_no = 100000
for code in CODES:
    slices, cur = [], START
    while cur < END:
        nxt = min(cur + relativedelta(months=3), END)
        slices.append((cur, nxt))
        cur = nxt
    records = []
    for idx, (a, b) in enumerate(slices):
        n = random.randint(40 + idx * 2, 80 + idx * 3)
        for _ in range(n):
            report_no += 1
            mfr, country = random.choice(MFRS_MAUDE)
            ev = make_event(report_no, code, mfr, country, rand_date(a, b), kind="literature" if random.random() < 0.04 else "normal")
            records.append(ev)
            roll = random.random()
            if roll < 0.02 and ev["mdr_text"]:                # probable duplicate: same device / day / narrative
                report_no += 1
                dup = json.loads(json.dumps(ev))
                dup["report_number"] = str(report_no)
                records.append(dup)
            elif roll < 0.035 and ev["mdr_text"]:             # similar duplicate: same start, extra text -> roughly 85% similar
                report_no += 1
                dup = json.loads(json.dumps(ev))
                dup["report_number"] = str(report_no)
                dup["mdr_text"] = [{"text": ev["mdr_text"][0]["text"] + " FOLLOW UP CONFIRMED THE FINDINGS."}]
                records.append(dup)
            elif roll < 0.055:                                # same lot and day, different narrative
                report_no += 1
                dup = make_event(report_no, code, mfr, country, rand_date(a, b))
                dup["date_received"] = ev["date_received"]
                dup["device"][0]["brand_name"] = ev["device"][0]["brand_name"]
                dup["device"][0]["model_number"] = ev["device"][0]["model_number"]
                dup["device"][0]["lot_number"] = "LOTX7"
                ev["device"][0]["lot_number"] = "LOTX7"
                records.append(dup)
    if code == "QJP":                                         # a real category spike (breach) in the middle of the window
        spike_month = (START + relativedelta(months=15)).replace(day=1)
        for _ in range(220):
            report_no += 1
            mfr, country = random.choice(MFRS_MAUDE[:20])
            records.append(make_event(report_no, code, mfr, country, spike_month + timedelta(days=random.randint(0, 27))))
    if code == "DXY":                                         # velocity-gap manufacturer: events in month +2 and +5 only
        for off, n in ((2, 8), (5, 12)):
            first = START.replace(day=1) + relativedelta(months=off)
            for _ in range(n):
                report_no += 1
                records.append(make_event(report_no, code, "Gap Test Mfr, Inc.", "US", first + timedelta(days=random.randint(0, 25))))
    by_slice = {}
    for rec in records:
        d = date(int(rec["date_received"][:4]), int(rec["date_received"][4:6]), int(rec["date_received"][6:]))
        for i, (a, b) in enumerate(slices):
            if a <= d <= b:
                by_slice.setdefault(i, []).append(rec)
                break
    for i, recs in by_slice.items():
        a, b = slices[i]
        (BRONZE / f"maude_{code}_{a:%Y%m%d}_{b:%Y%m%d}_fixture.json").write_text(json.dumps(recs), encoding="utf-8")

DIST = ["Nationwide and international", "US distribution only", "Worldwide Distribution", "Product distributed to Germany and Japan",
        "Product distributed to CA, TX, NY", "See attached list", None]
QTY = ["529912 units", "12 cartons, 600 units", "unknown", "N/A - Labeling Correction", "1,234", "50 boxes of 10",
       "45,302 (8,188 in the U.S.) - total, all model numbers", "315 (172 in USA)",
       "1,127 loose implants; 10/24-implant kits; and 4/36-implant kits (1,511 total implants)", None]
REASONS = ["Error in labeling", "Device Design", "Process control", "Under Investigation by firm", "Other", "Unknown/Undetermined by firm"]
cfres = 50000
for code in CODES:
    records = []
    for i in range(random.randint(40, 70)):
        cfres += 1
        mfr, _c = random.choice(MFRS_RECALLS)
        d = rand_date(RECALL_START, END)
        domestic = random.random() > 0.15
        brand = random.choice(BRANDS[code]).upper()
        long_desc = (f"{brand} Model Numbers: PM1240, PM2240; {brand} + Model Number: PM2260. Implantation of a single-chamber pulse generator is "
                     f"indicated in one or more of the following permanent conditions: syncope, presyncope, fatigue, disorientation. " * 3) if i % 7 == 0 else None
        rec = {
            "cfres_id": str(cfres), "product_code": code, "recalling_firm": mfr, "recall_status": random.choice(["Terminated", "Completed", "Open, Classified"]),
            "root_cause_description": random.choice(REASONS), "action": "Firm issued an Urgent Medical Device Recall letter.",
            "country": "" if domestic else random.choice(["Ireland", "Germany"]), "state": "CA" if domestic else "", "city": random.choice(["Irvine", "Fremont", "Cork"]),
            "address_1": f"{random.randint(1, 999)} Industrial Way", "address_2": "",
            "product_description": long_desc or f"{brand}, sterile, single use, model {random.randint(1, 40)}",
            "code_info": ("Serial Numbers: " + " ".join(str(random.randint(1000000, 9999999)) for _ in range(5000))) if i % 11 == 0 else f"Lot numbers {random.randint(1000, 9999)}",
            "distribution_pattern": random.choice(DIST), "product_quantity": random.choices(QTY, weights=[3, 3, 1, 1, 3, 0.4, 3, 3, 3, 2])[0],
            "firm_fei_number": str(random.randint(1000000, 9999999)),
            "k_numbers": random.choice([f"K{random.randint(100000, 999999)}", None]), "pma_numbers": random.choice([f"P{random.randint(100000, 999999)}", None]),
            "res_event_number": str(random.randint(10000, 99999)),
            "event_date_posted": d.strftime("%Y%m%d"), "event_date_initiated": (d - timedelta(days=random.randint(5, 90))).strftime("%Y%m%d"),
        }
        if random.random() < 0.1:
            rec["event_date_posted"] = None
        records.append(rec)
    (BRONZE / f"recalls_{code}_fixture.json").write_text(json.dumps(records), encoding="utf-8")

for code in CODES:
    records = []
    for i, brand in enumerate(BRANDS[code] * 6):
        mfr, _c = random.choice(SHARED_MFRS)
        records.append({
            "public_device_record_key": f"{code}-key-{i}", "brand_name": brand, "company_name": mfr, "device_description": f"{code} description",
            "catalog_number": random.choice(["CAT-1", "CAT-2", "CAT-3", f"SKU{i}"]), "version_or_model_number": random.choice(["M100", "M200", f"V{i}"]),
            "labeler_duns_number": DUNS_OF[mfr], "commercial_distribution_status": "In Commercial Distribution",
            "is_kit": random.choice(["true", "false"]), "is_single_use": random.choice(["true", "false"]),
            "mri_safety": random.choice(["MR Safe", "MR Conditional"]), "sterilization": {"is_sterile": random.choice(["true", "false"])},
            "identifiers": [{"type": "Primary", "id": f"0088{i:04d}", "issuing_agency": "GS1"}],
            "gmdn_terms": [{"name": random.choice(["Catheter", "Pacemaker", "Spinal implant"])}], "product_codes": [{"code": code}],
        })
    (BRONZE / f"gudid_{code}_fixture.json").write_text(json.dumps(records), encoding="utf-8")

META = {"DXY": ("3", "CV", "Cardiovascular", "Y", "870.3610"), "QJP": ("2", "CV", "Cardiovascular", "N", "870.1250"),
        "MAX": ("2", "OR", "Orthopedic", "Y", "888.3070"), "HCG": ("2", "NE", "Neurology", "Y", "882.5950"), "ODP": ("2", "OR", "Orthopedic", "Y", "888.3080")}
for code, (cls, spec, desc, implant, reg) in META.items():
    (BRONZE / f"classification_{code}_fixture.json").write_text(json.dumps([{
        "product_code": code, "device_class": cls, "device_name": FDA_NAMES[code], "medical_specialty": spec, "medical_specialty_description": desc,
        "implant_flag": implant, "review_panel": spec, "regulation_number": reg, "summary_malfunction_reporting": "Eligible",
        "definition": None if code in ("DXY", "HCG") else f"{desc} definition.",
        "openfda": {"k_number": ["K123456", "K654321"], "registration_number": ["1000001"], "fei_number": ["3000001"]}}]), encoding="utf-8")

print(f"Bronze fixture written: {len(list(BRONZE.glob('*.json')))} files (partial first / last month, a QJP spike month, FIPS country codes incl. CH)")
