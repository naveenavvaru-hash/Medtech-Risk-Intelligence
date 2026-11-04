# Reference data

These four files are reviewed reference data. Edit them, not the code.

## `parent_company_map.csv`
| Column | Meaning |
|---|---|
| `pattern` | regular expression, matched case-insensitively against the cleaned manufacturer name |
| `parent_company` | value written to `dim_manufacturer.parent_company` |

Rules are tried **top to bottom** and the first match wins. No match means the manufacturer is its own parent.

## `manufacturer_country_overrides.csv`
| Column | Meaning |
|---|---|
| `canonical_name_upper` | upper-case canonical manufacturer name |
| `country` | full country name |

Used only for manufacturers whose country is still blank after MAUDE, recalls and the openFDA registry lookup. Five rows are pre-filled from
public knowledge and marked VERIFY. The log prints the firms that are still unknown.

## `brand_name_overrides.csv`
| Column | Meaning |
|---|---|
| `brand_name_norm` | upper-case normalised brand name |
| `brand_name_display` | corrected name |

## `device_description_map.csv`
| Column | Meaning |
|---|---|
| `pattern` | regular expression matched against the device description (case-insensitive) |
| `product_codes` | optional product codes the rule applies to, separated by `\|` (blank = all) |
| `label` | the standard description; `{FDA}` means the FDA device name of the product code |

Descriptions that already equal the FDA name (in any word order), or that are the first word(s) of the brand name, are standardised
automatically. This file is only for the leftovers you want to map by hand.
