"""
deel-converter: Convert employee spreadsheet data to Deel People Import CSV format.

Usage:
    python convert.py --source input.xlsx --config configs/apres_capital.yaml --output output.csv
    python convert.py ... --split-by-entity
    python convert.py ... --template deel_template.csv
    python convert.py ... --dry-run --verbose
"""

import argparse
import csv
import datetime
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yaml

logger = logging.getLogger("deel_converter")

# ── SECTION 1: Lookup tables ────────────────────────────────────────────────

US_STATES: Dict[str, str] = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY", "District of Columbia": "DC",
}

COUNTRY_ISO2: Dict[str, str] = {
    "United States of America": "US", "United States": "US", "USA": "US",
    "United Kingdom": "GB", "Great Britain": "GB", "UK": "GB",
    "Canada": "CA", "Australia": "AU", "Germany": "DE", "France": "FR",
    "Spain": "ES", "Italy": "IT", "Netherlands": "NL", "Belgium": "BE",
    "Switzerland": "CH", "Austria": "AT", "Sweden": "SE", "Norway": "NO",
    "Denmark": "DK", "Finland": "FI", "Portugal": "PT", "Ireland": "IE",
    "New Zealand": "NZ", "Japan": "JP", "China": "CN", "India": "IN",
    "Brazil": "BR", "Mexico": "MX", "Argentina": "AR", "Chile": "CL",
    "Colombia": "CO", "Peru": "PE", "South Africa": "ZA", "Nigeria": "NG",
    "Kenya": "KE", "Ghana": "GH", "Israel": "IL", "Singapore": "SG",
    "Malaysia": "MY", "Philippines": "PH", "Indonesia": "ID", "Thailand": "TH",
    "Vietnam": "VN", "South Korea": "KR", "Taiwan": "TW", "Hong Kong": "HK",
    "Poland": "PL", "Czech Republic": "CZ", "Hungary": "HU", "Romania": "RO",
    "Ukraine": "UA", "Russia": "RU", "Turkey": "TR", "Saudi Arabia": "SA",
    "United Arab Emirates": "AE", "Egypt": "EG", "Pakistan": "PK",
    "Bangladesh": "BD", "Sri Lanka": "LK", "Nepal": "NP",
}

# ── SECTION 2: Transform functions ─────────────────────────────────────────

def transform_state_to_code(
    value: Any,
    value_map: Optional[Dict] = None,
    warn_context: str = "",
) -> str:
    if pd.isna(value) or str(value).strip() == "":
        return ""
    s = str(value).strip()
    if len(s) == 2 and s.upper() == s:
        return s
    merged = {**US_STATES, **(value_map or {})}
    result = merged.get(s) or merged.get(s.title())
    if result is None:
        logger.warning("state_to_code: unknown value %r%s — keeping original", s, warn_context)
        return s
    return result


def transform_country_to_iso2(
    value: Any,
    value_map: Optional[Dict] = None,
    warn_context: str = "",
) -> str:
    if pd.isna(value) or str(value).strip() == "":
        return ""
    s = str(value).strip()
    if len(s) == 2 and s.upper() == s:
        return s
    merged = {**COUNTRY_ISO2, **(value_map or {})}
    result = merged.get(s) or merged.get(s.title())
    if result is None:
        logger.warning("country_to_iso2: unknown value %r%s — keeping original", s, warn_context)
        return s
    return result


def transform_excel_date_to_iso(
    value: Any,
    value_map: Optional[Dict] = None,
    warn_context: str = "",
) -> str:
    if pd.isna(value) or str(value).strip() == "":
        return ""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.strftime("%Y-%m-%d")
    try:
        s = str(value).strip()
        # Try parsing as a numeric Excel serial date
        serial = float(s)
        # Excel epoch: 1899-12-30 (accounts for Lotus 1-2-3 leap year bug)
        base = datetime.date(1899, 12, 30)
        d = base + datetime.timedelta(days=int(serial))
        return d.strftime("%Y-%m-%d")
    except (ValueError, OverflowError, OSError):
        pass
    # Try common date string formats
    for fmt in (
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
        "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%B %d, %Y",
    ):
        try:
            return datetime.datetime.strptime(str(value).strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    logger.warning(
        "excel_date_to_iso: cannot parse %r%s — keeping original", value, warn_context
    )
    return str(value).strip()


def transform_clean_salary(
    value: Any,
    value_map: Optional[Dict] = None,
    warn_context: str = "",
) -> str:
    if pd.isna(value) or str(value).strip() == "":
        return ""
    s = str(value).strip()
    s = re.sub(r"[$,\s]", "", s)
    s = re.sub(r"\.0+$", "", s)
    if not re.match(r"^\d+(\.\d+)?$", s):
        logger.warning(
            "clean_salary: unexpected format %r%s", value, warn_context
        )
    return s


def transform_employment_type_map(
    value: Any,
    value_map: Optional[Dict] = None,
    warn_context: str = "",
) -> str:
    if pd.isna(value) or str(value).strip() == "":
        return ""
    s = str(value).strip()
    defaults = {
        "Full-time": "full-time", "Full Time": "full-time", "Fulltime": "full-time",
        "Part-Time": "part-time", "Part Time": "part-time", "Parttime": "part-time",
        "Contract": "contractor", "Contractor": "contractor",
    }
    merged = {**defaults, **(value_map or {})}
    lower_map = {k.lower(): v for k, v in merged.items()}
    result = lower_map.get(s.lower())
    if result is None:
        logger.warning(
            "employment_type_map: unknown value %r%s — keeping original", s, warn_context
        )
        return s
    return result


def transform_pay_method_map(
    value: Any,
    value_map: Optional[Dict] = None,
    warn_context: str = "",
) -> str:
    if pd.isna(value) or str(value).strip() == "":
        return ""
    s = str(value).strip()
    defaults = {
        "Salary": "SALARY", "Salaried": "SALARY",
        "Hourly": "HOURLY", "Hour": "HOURLY",
        "Commission": "COMMISSION_ONLY",
        "Non-Paid": "NON_PAID_OWNER", "Non Paid": "NON_PAID_OWNER",
    }
    merged = {**defaults, **(value_map or {})}
    lower_map = {k.lower(): v for k, v in merged.items()}
    result = lower_map.get(s.lower())
    if result is None:
        logger.warning(
            "pay_method_map: unknown value %r%s — keeping original", s, warn_context
        )
        return s
    return result


def transform_strip_code_prefix(
    value: Any,
    value_map: Optional[Dict] = None,
    warn_context: str = "",
) -> str:
    """Strip a leading 'CODE - ' prefix from coded values.

    Examples:
        'VPIRPD - VP of Institutional Relations'  →  'VP of Institutional Relations'
        'F - Full Time'                           →  'Full Time'
        'REM - Remote'                            →  'Remote'
        '000200 - Institutional Relations'        →  'Institutional Relations'
        'CC GCU - Campus Coordinator, GCU'        →  'Campus Coordinator, GCU'

    Only strips when the text before the first ' - ' is entirely uppercase
    (letters, digits, spaces, slashes, underscores, commas). Values that
    don't match this pattern are returned unchanged.
    """
    if pd.isna(value) or str(value).strip() == "":
        return ""
    s = str(value).strip()
    if " - " in s:
        prefix, _, rest = s.partition(" - ")
        # Codes are always uppercase — digits are unchanged by .upper()
        if prefix == prefix.upper():
            return rest.strip()
    return s


def transform_seniority_map(
    value: Any,
    value_map: Optional[Dict] = None,
    warn_context: str = "",
) -> str:
    if pd.isna(value) or str(value).strip() == "":
        return ""
    s = str(value).strip()
    merged = {**(value_map or {})}
    lower_map = {k.lower(): v for k, v in merged.items()}
    result = lower_map.get(s.lower())
    if result is None:
        logger.warning(
            "seniority_map: unknown value %r%s — keeping original", s, warn_context
        )
        return s
    return result


# ── SECTION 3: Transform registry ──────────────────────────────────────────

TRANSFORM_REGISTRY = {
    "state_to_code": transform_state_to_code,
    "country_to_iso2": transform_country_to_iso2,
    "excel_date_to_iso": transform_excel_date_to_iso,
    "clean_salary": transform_clean_salary,
    "employment_type_map": transform_employment_type_map,
    "pay_method_map": transform_pay_method_map,
    "seniority_map": transform_seniority_map,
    "strip_code_prefix": transform_strip_code_prefix,
}


# ── SECTION 4: Config loader ────────────────────────────────────────────────

def load_config(config_path: Path) -> dict:
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    errors = []
    for key in ("source", "target", "mappings"):
        if key not in cfg:
            errors.append(f"Missing required config key: '{key}'")

    if "source" in cfg:
        src = cfg["source"]
        for k in ("sheet_name", "header_rows", "data_start_row"):
            if k not in src:
                errors.append(f"source.{k} is required")
        if "header_rows" in src:
            for k in ("main_header_row",):
                if k not in src["header_rows"]:
                    errors.append(f"source.header_rows.{k} is required")

    if "target" in cfg:
        tgt = cfg["target"]
        for k in ("columns", "metadata_rows"):
            if k not in tgt:
                errors.append(f"target.{k} is required")

    if "mappings" in cfg:
        valid = set(TRANSFORM_REGISTRY.keys())
        for col, mapping in cfg["mappings"].items():
            if mapping is None:
                errors.append(f"mappings.{col!r}: null mapping — use {{blank: true}} or {{default: ''}}")
                continue
            if "transform" in mapping and mapping["transform"] not in valid:
                errors.append(
                    f"mappings.{col!r}: unknown transform {mapping['transform']!r}. "
                    f"Valid: {sorted(valid)}"
                )
            if "source_col" in mapping and "source_header" in mapping:
                errors.append(
                    f"mappings.{col!r}: cannot specify both source_col and source_header"
                )

    # Normalize sub_header_overrides keys to int
    if "source" in cfg and "sub_header_overrides" in cfg["source"]:
        orig = cfg["source"]["sub_header_overrides"]
        cfg["source"]["sub_header_overrides"] = {int(k): v for k, v in orig.items()}
    else:
        if "source" in cfg:
            cfg["source"].setdefault("sub_header_overrides", {})

    if errors:
        for err in errors:
            logger.error("Config error: %s", err)
        sys.exit(1)

    return cfg


# ── SECTION 5: Column letter helper ────────────────────────────────────────

def col_letter_to_index(letter: str) -> int:
    """Convert Excel column letter(s) to 0-based index. 'A'→0, 'Z'→25, 'AA'→26."""
    letter = letter.upper().strip()
    result = 0
    for ch in letter:
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result - 1


# ── SECTION 6: Source reader ────────────────────────────────────────────────

def load_source(path: Path, config: dict) -> Tuple[pd.DataFrame, Dict[int, str]]:
    """
    Read source file (Excel or CSV) and return:
    - data_df: DataFrame with resolved column names
    - col_name_map: {col_index → resolved_name}
    """
    src_cfg = config["source"]
    header_cfg = src_cfg["header_rows"]
    data_start = src_cfg["data_start_row"]  # 1-based
    sub_overrides: Dict[int, str] = src_cfg.get("sub_header_overrides", {})

    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls", ".xlsm"):
        raw = pd.read_excel(
            path,
            sheet_name=src_cfg["sheet_name"],
            header=None,
            engine="openpyxl",
            dtype=str,  # read everything as string; transforms handle type conversion
        )
    elif suffix == ".csv":
        raw = pd.read_csv(path, header=None, dtype=str)
    else:
        logger.error("Unsupported file type: %s", suffix)
        sys.exit(1)

    main_header_idx = header_cfg["main_header_row"] - 1
    main_header_row = raw.iloc[main_header_idx]

    # For sub_header: optional
    sub_header_idx = None
    if "sub_header_row" in header_cfg:
        sub_header_idx = header_cfg["sub_header_row"] - 1

    data_start_idx = data_start - 1

    # Build column name map
    col_name_map: Dict[int, str] = {}
    seen_names: Dict[str, int] = {}

    # sub_header_for_groups: list of main-header group names (e.g. "Employee address")
    # whose individual sub-header names should be used instead of the group name.
    group_headers_set = set(
        g.strip() for g in src_cfg.get("sub_header_for_groups", [])
    )

    for col_idx in range(len(raw.columns)):
        if col_idx in sub_overrides:
            name = sub_overrides[col_idx]
        else:
            cell = main_header_row.iloc[col_idx]
            name = str(cell).strip() if (cell is not None and str(cell).strip() not in ("", "nan", "None")) else ""

            # If this cell is a group header, replace it with the sub-header name
            if name and name in group_headers_set and sub_header_idx is not None:
                sub_cell = raw.iloc[sub_header_idx, col_idx]
                sub_val = str(sub_cell).strip() if (sub_cell is not None and str(sub_cell).strip() not in ("", "nan", "None")) else ""
                if sub_val:
                    name = sub_val

        if not name:
            # Try sub_header as fallback for empty main header cells
            if sub_header_idx is not None:
                sub_cell = raw.iloc[sub_header_idx, col_idx]
                name = str(sub_cell).strip() if (sub_cell is not None and str(sub_cell).strip() not in ("", "nan", "None")) else ""

        if not name:
            # Use previous name with suffix for continuation (merged cells)
            if col_idx > 0 and col_name_map.get(col_idx - 1):
                name = f"{col_name_map[col_idx - 1]}_cont"
            else:
                name = f"_col{col_idx}"

        # Deduplicate
        base = name
        if name in seen_names:
            seen_names[name] += 1
            name = f"{base}_{seen_names[name]}"
        else:
            seen_names[name] = 1

        col_name_map[col_idx] = name

    data_df = raw.iloc[data_start_idx:].copy()
    data_df.reset_index(drop=True, inplace=True)
    data_df.columns = [col_name_map[i] for i in range(len(data_df.columns))]
    data_df.dropna(how="all", inplace=True)

    return data_df, col_name_map


# ── SECTION 7: Column header search ────────────────────────────────────────

def find_col_by_header(data_df: pd.DataFrame, header_name: str) -> Optional[str]:
    """Find a DataFrame column by name, case-insensitively with whitespace normalization.

    Prefix with '~' for starts-with matching:
        source_header: "~Seniority"  →  matches "Seniority: Junior, Mid, Senior, Lead, ..."
    """
    startswith = header_name.startswith("~")
    if startswith:
        header_name = header_name[1:]
    target = re.sub(r"\s+", " ", header_name.strip().lower())
    for col in data_df.columns:
        normalized = re.sub(r"\s+", " ", str(col).strip().lower())
        if startswith:
            if normalized.startswith(target):
                return col
        else:
            if normalized == target:
                return col
    return None


# ── SECTION 8: Pre-resolve source_header lookups ───────────────────────────

def pre_resolve_source_headers(mappings: dict, data_df: pd.DataFrame) -> None:
    """Cache _resolved_col for each source_header mapping.

    Set optional: true on a mapping to suppress the 'not found' warning —
    useful when a column may not exist in every source file variant.
    """
    for target_col, mapping in mappings.items():
        if mapping is None:
            continue
        if "source_header" in mapping:
            resolved = find_col_by_header(data_df, mapping["source_header"])
            if resolved is None and not mapping.get("optional"):
                logger.warning(
                    "source_header %r not found. Available columns: %s",
                    mapping["source_header"],
                    list(data_df.columns),
                )
            mapping["_resolved_col"] = resolved


# ── SECTION 9: Metadata row builder ────────────────────────────────────────

def build_metadata_rows(config: dict) -> List[List[str]]:
    """Generate the 6 Deel template metadata rows from config."""
    cols = config["target"]["columns"]
    n = len(cols)
    meta_cfg = config["target"].get("metadata", {})
    mandatory_set = set(meta_cfg.get("mandatory_fields", []))

    row1 = list(cols)
    row2 = []
    for col in cols:
        if col == config["target"].get("field_column", "FIELD"):
            row2.append("")
        elif col in mandatory_set:
            row2.append("MANDATORY")
        else:
            row2.append("OPTIONAL")

    # Rows 3-6: empty (description, constraints, examples, separator)
    empty = [""] * n
    return [row1, row2, empty[:], empty[:], empty[:], empty[:]]


def load_deel_template(path: Path, config: dict) -> List[List[str]]:
    """Read metadata rows verbatim from an existing Deel template CSV."""
    meta_count = config["target"]["metadata_rows"]
    metadata_rows: List[List[str]] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i < meta_count:
                metadata_rows.append(row)
            else:
                break
    return metadata_rows


# ── SECTION 10: Row transformer ─────────────────────────────────────────────

def transform_row(
    row: pd.Series,
    mappings: dict,
    transforms_cfg: dict,
    col_name_map: Dict[int, str],
    row_num: int,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {}

    for target_col, mapping in mappings.items():
        if mapping is None:
            result[target_col] = ""
            continue

        # blank
        if mapping.get("blank"):
            result[target_col] = ""
            continue

        # static default with no source
        if "default" in mapping and "source_col" not in mapping and "source_header" not in mapping:
            result[target_col] = mapping["default"]
            continue

        # resolve source value
        value = ""
        if "source_col" in mapping:
            col_idx = col_letter_to_index(mapping["source_col"])
            col_name = col_name_map.get(col_idx)
            if col_name and col_name in row.index:
                value = row[col_name]
            else:
                logger.warning(
                    "Row %d: source_col %r (index %d) not found",
                    row_num, mapping["source_col"], col_idx,
                )
        elif "source_header" in mapping:
            col_name = mapping.get("_resolved_col")
            if col_name and col_name in row.index:
                value = row[col_name]
            elif col_name is None:
                pass  # already warned during pre_resolve
        else:
            value = mapping.get("default", "")

        # apply transform
        transform_id = mapping.get("transform")
        if transform_id:
            fn = TRANSFORM_REGISTRY.get(transform_id)
            if fn is None:
                logger.error("Unknown transform %r for column %r", transform_id, target_col)
                result[target_col] = "" if pd.isna(value) else str(value).strip()
                continue
            yaml_value_map = transforms_cfg.get(transform_id)
            warn_ctx = f" (row {row_num}, col '{target_col}')"
            value = fn(value, value_map=yaml_value_map, warn_context=warn_ctx)
        else:
            value = "" if (value is None or pd.isna(value)) else str(value).strip()

        result[target_col] = value

    return result


# ── SECTION 11: Output writers ──────────────────────────────────────────────

def write_output(
    data_rows: List[Dict[str, Any]],
    metadata_rows: List[List[str]],
    output_cols: List[str],
    path: Path,
    field_col: str = "FIELD",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        for meta_row in metadata_rows:
            writer.writerow(meta_row)
        for field_num, row_dict in enumerate(data_rows, start=1):
            out_row = []
            for col in output_cols:
                if col == field_col:
                    out_row.append(str(field_num))
                else:
                    out_row.append(str(row_dict.get(col, "")))
            writer.writerow(out_row)
    logger.info("Wrote %d data rows → %s", len(data_rows), path)


def write_split_output(
    data_rows: List[Dict[str, Any]],
    metadata_rows: List[List[str]],
    output_cols: List[str],
    output_base: Path,
    entity_col: str,
    field_col: str = "FIELD",
) -> None:
    groups: Dict[str, List[Dict]] = {}
    for row in data_rows:
        entity = str(row.get(entity_col, "")).strip() or "_unknown"
        safe = re.sub(r'[<>:"/\\|?*\s]+', "_", entity)
        groups.setdefault(safe, []).append(row)

    stem = output_base.stem
    suffix = output_base.suffix or ".csv"
    parent = output_base.parent

    for entity_name, rows in groups.items():
        out_path = parent / f"{stem}_{entity_name}{suffix}"
        write_output(rows, metadata_rows, output_cols, out_path, field_col)
        logger.info("Entity %r → %d rows → %s", entity_name, len(rows), out_path)


# ── SECTION 12: CLI entry point ─────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert employee spreadsheet data to Deel People Import CSV format.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python convert.py --source input.xlsx --config configs/apres_capital.yaml --output output.csv
  python convert.py --source input.xlsx --config configs/apres_capital.yaml \\
      --output output.csv --split-by-entity --entity-col "Entity"
  python convert.py --source input.xlsx --config configs/apres_capital.yaml \\
      --output output.csv --template deel_template.csv --verbose
  python convert.py --source input.xlsx --config configs/apres_capital.yaml \\
      --output output.csv --dry-run --verbose
        """,
    )
    parser.add_argument("--source",   required=True, help="Source Excel (.xlsx) or CSV file")
    parser.add_argument("--config",   required=True, help="YAML config file")
    parser.add_argument("--output",   required=True, help="Output CSV file path")
    parser.add_argument("--template", default=None,  help="Deel template CSV (for metadata rows)")
    parser.add_argument("--split-by-entity", action="store_true",
                        help="Generate one output CSV per entity value")
    parser.add_argument("--entity-col", default="Entity",
                        help="Target column name to split on (default: Entity)")
    parser.add_argument("--verbose", action="store_true", help="Enable DEBUG logging")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and transform without writing files")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    source_path = Path(args.source)
    config_path = Path(args.config)
    output_path = Path(args.output)

    if not source_path.exists():
        logger.error("Source file not found: %s", source_path)
        sys.exit(1)
    if not config_path.exists():
        logger.error("Config file not found: %s", config_path)
        sys.exit(1)

    # 1. Load config
    config = load_config(config_path)

    # 2. Load source data
    logger.info("Reading source: %s", source_path)
    data_df, col_name_map = load_source(source_path, config)
    logger.info("Loaded %d rows, %d columns", len(data_df), len(data_df.columns))
    if args.verbose:
        logger.debug("Resolved column names: %s", list(data_df.columns))

    # 3. Pre-resolve source_header lookups
    mappings = config["mappings"]
    pre_resolve_source_headers(mappings, data_df)

    # 4. Build metadata rows
    if args.template:
        template_path = Path(args.template)
        if not template_path.exists():
            logger.error("Template file not found: %s", template_path)
            sys.exit(1)
        metadata_rows = load_deel_template(template_path, config)
        logger.info("Using metadata from: %s", template_path)
    else:
        metadata_rows = build_metadata_rows(config)

    # 5. Transform rows
    output_cols = config["target"]["columns"]
    field_col = config["target"].get("field_column", "FIELD")
    transforms_cfg = config.get("transforms", {})
    data_rows: List[Dict[str, Any]] = []
    skipped = 0

    for row_num, (_, row) in enumerate(data_df.iterrows(), start=1):
        try:
            transformed = transform_row(row, mappings, transforms_cfg, col_name_map, row_num)
            data_rows.append(transformed)
        except Exception as exc:
            logger.error("Row %d: unexpected error — %s", row_num, exc)
            if args.verbose:
                raise
            skipped += 1

    logger.info("Transformed %d rows%s", len(data_rows),
                f" ({skipped} skipped)" if skipped else "")

    # 6. Write or preview
    if args.dry_run:
        logger.info("Dry run — no files written. Preview (first 3 rows):")
        for i, row in enumerate(data_rows[:3], start=1):
            logger.info("  Row %d: %s", i, row)
        return

    if args.split_by_entity:
        write_split_output(
            data_rows, metadata_rows, output_cols, output_path,
            args.entity_col, field_col
        )
    else:
        write_output(data_rows, metadata_rows, output_cols, output_path, field_col)

    logger.info("Done.")


if __name__ == "__main__":
    main()
