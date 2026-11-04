# Changelog

## v12.1 - repository structure and the Gold layer

No data-quality rule changed. On the offline fixture the workbook is identical to v12 (14 sheets, 137,087 cells, values and cell types).

### Structure
- The flat `pipelines/` folder is now the package `medtech` with one sub-package per layer: `bronze`, `silver`, `gold`, `export`.
  Shared helpers (`common`, `text_normalize`, `country_normalize`, `reference_data`) sit at the top of the package. No `sys.path` changes in the package.
- `python run_pipeline.py`, `python resume_pipeline.py` and `python validate_export.py` stay as the commands you run. They are now short scripts in the project root
  that call the package, so they also start from the VS Code Run button. `python -m medtech run | status | validate` does the same.
  `run_pipeline.py` takes `--from silver|gold` and `--status`.
- Tests moved to `tests/` and run with `python tests/unit_tests.py` (19 new checks in section L, 12 in section M).

### Gold tables instead of CSV files (R4)
- `root_cause_flags.csv` and `root_cause_flags_parent.csv` were identical to the workbook sheets of the same name. They are now the Gold tables
  `root_cause_flags` and `root_cause_flags_parent`, and the export reads them from the warehouse.
- `recall_code_info_full.csv` stays: Excel cannot hold more than 32,767 characters in a cell, so the complete text exists only there and in the warehouse.

### Fixes
- **R1** The runner returns the validator's exit code (0 / 1 / 2). It used to discard it, so a CRITICAL finding still exited 0.
- **R2** Every run that includes the Gold load deletes the old warehouse first. Before, a full run kept rows that had left the 24-month window.
- **R3** The offline fixture no longer deletes real Bronze files.

### Housekeeping
- Removed the empty `.gitkeep` files (the folders are created on first run), `docs/build_matrix.py`, and the packaging metadata files
  `[Content_Types].xml`, `_rels/` and `package/` that the zip tool had added.
- README rewritten for a public repository; the issue matrix is now the plain Markdown table `docs/ISSUE_LOG.md`.

## Earlier versions

### v12 - fixes for the 4 partly resolved issues and the 6 new findings from the v11 run

Item-by-item status and evidence: [ISSUE_LOG.md](ISSUE_LOG.md).

#### Security
- **N1** The openFDA API key appeared in the log (failed requests log their URL). A logging filter now masks `api_key=...` and the literal key on both
  handlers; the validator (`log-secrets`) scans the log. **An openFDA key that appeared in a log written before v12 should be replaced.**

#### Data
- **N2** The MAUDE country CODE standard (FIPS vs ISO) is detected from the data and every code is decoded to a name in Silver. An undecidable code is
  blanked and filled by the registry lookup - `dim_manufacturer.country` never holds a raw code. Enrichment normalises before it looks up.
- **N3 / MFR-03** The `Unknown` placeholder manufacturer never receives a country.
- **EVT-05 / N4** Duplicates use the full-text similarity within the same manufacturer, device, model and day (>= 95% probable, 80-95% possible).
- **DEV-04** FDA name for descriptions that are the same words in another order, the first word(s) of the brand, or in `config/device_description_map.csv`;
  missing descriptions filled; ALL-CAPS leftovers Title-Cased.
- **DEV-05 / N5 / N6** no `UNKNOWN` device (`(not reported)`); a number is joined to its unit in device keys.
- **EVT-01/02/03** closing quote after `]`, `?`, `!`; `reasonable efforts to provide as much information` boilerplate; repeats that start in lower case;
  'reported through literature review' citations.
- **LOG-09** 5xx response bodies are logged on one line.

#### Verification
- `tests/unit_tests.py` - 261 checks. Four old bugs were reintroduced in scratch copies (no key redaction, a country on the placeholder, no unit joining,
  no code-standard detection); the suite caught each one.
- `validate_export.py` - new checks (raw country codes, placeholder country, credentials in the log, ALL-CAPS descriptions, four duplicate bases). Six planted
  defects were all caught, and run on the real v11 export + log it reports exactly the known problems.

### v11
Complete-month MAUDE window, partial months excluded from baselines, z-score / WATCH tier / rolling baseline, rises vs drops, parent-company flags, quantity parser,
Excel cell limit handling, recall initiated date, GUDID model-level flag, punctuation-insensitive manufacturer matching, citation-style literature detection.

### v10 and earlier
v10: resolution of the 55-item register. v9: GUDID pagination chunking. v8: byte-string corruption, manufacturer case duplicates, DUNS wiring. v7: contiguous dim_date. v6: column cleanup.
