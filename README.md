# deel-converter

Convert employee spreadsheet data (Excel/CSV) into [Deel's People Import CSV format](https://help.letsdeel.com/hc/en-gb/articles/4407286264977).

Configured via a YAML file — one config per client template. No code changes needed to support new source formats.

---

## Requirements

- Python 3.8+
- pip install -r requirements.txt

```bash
pip install -r requirements.txt
```

---

## Quick Start

```bash
python convert.py \
  --source "Apres Capital LLC EE Import.xlsx" \
  --config configs/apres_capital.yaml \
  --output output.csv
```

The output CSV is ready to upload to Deel's People Import tool.

---

## CLI Options

| Flag | Required | Description |
|---|---|---|
| `--source` | Yes | Path to source Excel (`.xlsx`) or CSV file |
| `--config` | Yes | Path to YAML config file |
| `--output` | Yes | Path for output CSV |
| `--template` | No | Path to a real Deel template CSV (preserves DESCRIPTION/CONSTRAINTS metadata rows) |
| `--split-by-entity` | No | Write one CSV per entity value |
| `--entity-col` | No | Target column name to split on (default: `Entity`) |
| `--dry-run` | No | Parse and transform without writing any files |
| `--verbose` | No | Show DEBUG-level log output |

---

## Adding a New Client

1. Copy an existing config:
   ```bash
   cp configs/apres_capital.yaml configs/new_client.yaml
   ```

2. Edit `configs/new_client.yaml`:
   - Update `source.sheet_name` to match the new file's sheet
   - Update `source.header_rows` row numbers if the header structure differs
   - Update `source.sub_header_overrides` for any merged address cells
   - Update `source.data_start_row` to the first data row
   - Update each mapping's `source_col` or `source_header` to match the new source columns
   - Update `transforms` value maps for any different source values

3. Run:
   ```bash
   python convert.py --source new_client.xlsx --config configs/new_client.yaml --output new_client_deel.csv --dry-run --verbose
   ```

---

## Config File Format

```yaml
source:
  sheet_name: "Sheet1"          # Excel sheet name
  header_rows:
    main_header_row: 1          # Row with main column headers (1-based)
    sub_header_row: 2           # Optional: row with sub-headers for merged cells
  sub_header_overrides:         # Use sub-header name for these column indices (0-based)
    3: "Zip/Postal Code"        # Column D → named "Zip/Postal Code"
  data_start_row: 3             # First row of employee data (1-based)

target:
  metadata_rows: 6
  field_column: "FIELD"
  columns:                      # Ordered output column names
    - "FIELD"
    - "Employee first name"
    # ... etc

mappings:
  "Employee first name":
    source_col: "A"             # Map from column A in source

  "State code":
    source_col: "E"
    transform: "state_to_code"  # Apply built-in transform

  "Work location":
    source_header: "Working location"  # Find column by header name

  "County":
    blank: true                 # Always output empty string

  "Benefit group":
    default: "ALL EMPLOYEES"    # Use this static value for all rows

transforms:
  pay_method_map:               # Override/extend built-in value maps
    "Salary": "SALARY"
    "Hourly": "HOURLY"
```

### Mapping options

| Key | Description |
|---|---|
| `source_col: "A"` | Map from column A (Excel letter notation) |
| `source_header: "Name"` | Find column by header text (case-insensitive) |
| `default: "value"` | Static value for all rows |
| `blank: true` | Always output empty string |
| `transform: "name"` | Apply a built-in transformation |

---

## Built-in Transforms

| Name | Input | Output | On unknown value |
|---|---|---|---|
| `state_to_code` | `"Colorado"` | `"CO"` | Keep original + warning |
| `country_to_iso2` | `"United States of America"` | `"US"` | Keep original + warning |
| `excel_date_to_iso` | `44531` or `datetime` | `"2021-12-31"` | Keep original + warning |
| `clean_salary` | `"$225,000.00"` | `"225000"` | Cleaned string + warning |
| `employment_type_map` | `"Full-time"` | `"full-time"` | Keep original + warning |
| `pay_method_map` | `"Salary"` | `"SALARY"` | Keep original + warning |
| `seniority_map` | `"C-Level Executive"` | `"C-Level Executives"` | Keep original + warning |

All transforms degrade gracefully — unknown values are passed through as-is and a warning is logged.

Custom value maps in the `transforms:` section of your YAML are merged on top of the built-in defaults.

---

## Output Format

The output CSV matches Deel's People Import template structure:

```
Row 1:    FIELD | Employee first name | Employee last name | ...
Row 2:          | MANDATORY           | MANDATORY          | ...
Rows 3-6: (metadata — empty unless --template is used)
Row 7:    1     | Mason               | Angel              | ...
Row 8:    2     | Taylor              | Sargent            | ...
```

Use `--template deel_template.csv` to copy DESCRIPTION/CONSTRAINTS/EXAMPLES rows from a real Deel template file.

---

## Troubleshooting

**`source_header 'Working location' not found`**
The column header in the source file doesn't match exactly. Use `--verbose` to see all resolved column names, then update `source_header` in the config to match.

**`state_to_code: unknown value 'Colo'`**
The source data has a non-standard state name. Add it to `transforms.state_to_code` in your config:
```yaml
transforms:
  state_to_code:
    "Colo": "CO"
```

**`excel_date_to_iso: cannot parse 'N/A'`**
The date field contains a non-date value. This is expected for rows without a start date — the value passes through as-is.

**Dates showing as numbers (e.g., `44531`)**
This is an Excel serial date. The `excel_date_to_iso` transform handles this automatically. Make sure `transform: "excel_date_to_iso"` is set in the config for the date column.

---

## Verification Checklist

After running the converter, spot-check the output CSV:

- [ ] Row count matches number of employees in the source
- [ ] Dates are in `YYYY-MM-DD` format
- [ ] State codes are 2-letter (`CO`, `CA`, `NY`, etc.)
- [ ] Nationality is 2-letter ISO code (`US`, `GB`, etc.)
- [ ] Salary is numeric only (no `$` or commas)
- [ ] Pay method is one of: `SALARY`, `HOURLY`, `NON_PAID_OWNER`, `COMMISSION_ONLY`
- [ ] Employment type is `full-time` or `part-time`
- [ ] `FIELD` column numbers sequentially from `1`
