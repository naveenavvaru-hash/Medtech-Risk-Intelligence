-- schema.sql (v12)
-- v12: duplicate_basis now has four values (Text reference / Same narrative = probable; Similar narrative / Same lot only = possible);
--      dim_manufacturer.country stays NULL for the 'Unknown' placeholder; devices without any usable name are '(not reported)'.

CREATE TABLE IF NOT EXISTS dim_date (
    date_key INTEGER PRIMARY KEY, full_date TEXT, year INTEGER, quarter INTEGER, month INTEGER, month_name TEXT,
    week INTEGER,              -- ISO week number
    iso_year INTEGER, year_month_label TEXT, year_month_sort INTEGER
);

CREATE TABLE IF NOT EXISTS dim_manufacturer (
    mfr_key INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT UNIQUE,
    display_name TEXT,
    parent_company TEXT,
    country TEXT,                               -- a country NAME (never a 2-letter code); NULL when unknown
    duns_number TEXT,
    country_source TEXT,
    is_placeholder_entity INTEGER DEFAULT 0     -- 1 = the 'Unknown' manufacturer (no name reported); never has a country
);

CREATE TABLE IF NOT EXISTS dim_device (
    device_key INTEGER PRIMARY KEY AUTOINCREMENT,
    product_code TEXT,
    device_description TEXT,    -- cleaned MAUDE term; FDA name when it is a cut text / code list / copy of the brand / same words
    device_type_fda TEXT,       -- FDA device name for the product code (always filled; best column for grouping)
    category TEXT,
    brand_name TEXT,            -- as submitted ('(not reported)' when the report has no usable brand or generic name)
    brand_name_display TEXT,
    model TEXT,
    brand_name_norm TEXT,       -- matching keys: normalize_device_key()
    model_norm TEXT,
    sku TEXT,
    catalog_number_observed TEXT,
    primary_di TEXT,
    issuing_agency TEXT,
    product_type TEXT,
    brand_family TEXT,
    commercial_distribution_status TEXT,
    is_kit TEXT,
    is_single_use TEXT,
    is_sterile TEXT,
    mri_safety TEXT,
    gudid_enriched INTEGER DEFAULT 0,
    match_method TEXT,
    gudid_match_level TEXT,     -- Model (catalog match) / Brand (brand-level only) / None
    UNIQUE(product_code, brand_name_norm, model_norm)
);

CREATE TABLE IF NOT EXISTS fact_events (
    event_key INTEGER PRIMARY KEY AUTOINCREMENT,    -- loaded in date order => chronological
    report_number TEXT UNIQUE,
    device_key INTEGER REFERENCES dim_device(device_key),
    mfr_key INTEGER REFERENCES dim_manufacturer(mfr_key),
    date_key INTEGER REFERENCES dim_date(date_key), -- FDA RECEIPT date
    event_type TEXT,
    narrative_text TEXT,
    lot_number TEXT,
    lot_kind TEXT,                                  -- Lot / PMA code / Multiple
    patient_age TEXT,                               -- blank for journal-literature reports
    patient_age_years REAL,
    patient_sex TEXT,
    patient_outcome TEXT,
    product_problem_codes TEXT,
    has_narrative INTEGER,
    is_literature_report INTEGER DEFAULT 0,
    literature_basis TEXT,                          -- Template / Citation
    is_probable_duplicate INTEGER DEFAULT 0,        -- Text reference / Same narrative (>= 95% similar full text)
    is_possible_duplicate INTEGER DEFAULT 0,        -- Similar narrative (70-95%) / Same lot only
    duplicate_basis TEXT
);

CREATE TABLE IF NOT EXISTS fact_event_outcomes (event_key INTEGER REFERENCES fact_events(event_key), outcome TEXT);
CREATE TABLE IF NOT EXISTS fact_event_problem_codes (
    event_key INTEGER REFERENCES fact_events(event_key), problem_code TEXT, problem_code_group TEXT
);

CREATE TABLE IF NOT EXISTS fact_recalls (
    recall_key INTEGER PRIMARY KEY AUTOINCREMENT,
    recall_id TEXT UNIQUE,
    device_key INTEGER REFERENCES dim_device(device_key),
    mfr_key INTEGER REFERENCES dim_manufacturer(mfr_key),
    date_key INTEGER REFERENCES dim_date(date_key),                  -- posted / created date (see recall_date_source)
    recall_date_source TEXT,
    recall_initiated_date_key INTEGER REFERENCES dim_date(date_key), -- date the firm started the recall (use for trends)
    recall_status TEXT,
    reason TEXT,
    reason_group TEXT,                                               -- Vague / Specific
    action TEXT, country TEXT, state TEXT, city TEXT, product_description TEXT,
    distribution_scope TEXT, product_quantity TEXT, product_quantity_units INTEGER, country_is_derived INTEGER
);

CREATE TABLE IF NOT EXISTS recall_compliance_ref (
    recall_id TEXT PRIMARY KEY REFERENCES fact_recalls(recall_id),
    address TEXT, code_info TEXT, firm_fei_number TEXT, k_numbers TEXT, pma_numbers TEXT, res_event_number TEXT
);

CREATE TABLE IF NOT EXISTS dim_classification (
    product_code TEXT PRIMARY KEY, device_class TEXT, device_class_label TEXT, device_name_fda TEXT, medical_specialty TEXT,
    medical_specialty_description TEXT, implant_flag TEXT, review_panel TEXT, regulation_number TEXT,
    summary_malfunction_reporting TEXT, definition TEXT
);

CREATE TABLE IF NOT EXISTS classification_compliance_ref (
    product_code TEXT PRIMARY KEY REFERENCES dim_classification(product_code),
    k_numbers_ref TEXT, registration_numbers_ref TEXT, fei_numbers_ref TEXT
);

CREATE INDEX IF NOT EXISTS idx_fact_events_date ON fact_events(date_key);
CREATE INDEX IF NOT EXISTS idx_fact_events_mfr ON fact_events(mfr_key);
CREATE INDEX IF NOT EXISTS idx_fact_events_device ON fact_events(device_key);
CREATE INDEX IF NOT EXISTS idx_fact_recalls_date ON fact_recalls(date_key);
CREATE INDEX IF NOT EXISTS idx_fact_recalls_mfr ON fact_recalls(mfr_key);
CREATE INDEX IF NOT EXISTS idx_fact_recalls_device ON fact_recalls(device_key);
CREATE INDEX IF NOT EXISTS idx_dim_manufacturer_duns ON dim_manufacturer(duns_number);
CREATE INDEX IF NOT EXISTS idx_dim_device_product_brand_norm ON dim_device(product_code, brand_name_norm);
CREATE INDEX IF NOT EXISTS idx_fact_event_outcomes_key ON fact_event_outcomes(event_key);
CREATE INDEX IF NOT EXISTS idx_fact_event_problems_key ON fact_event_problem_codes(event_key);
