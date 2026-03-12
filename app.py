"""
Deel Converter — Flask web interface
Wraps convert.py logic with a browser-based upload/download UI.
"""

import io
import logging
import os
import tempfile
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

# Re-use all logic from convert.py
from convert import (
    TRANSFORM_REGISTRY,
    build_metadata_rows,
    load_config,
    load_deel_template,
    load_source,
    pre_resolve_source_headers,
    transform_row,
    write_output,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB max upload

CONFIGS_DIR = Path(__file__).parent / "configs"
TEMPLATE_PATH = Path(__file__).parent / "templates" / "deel_template.csv"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("deel_web")


def get_available_configs():
    """Return list of (name, path) tuples for all YAML configs."""
    configs = []
    for f in sorted(CONFIGS_DIR.glob("*.yaml")):
        label = f.stem.replace("_", " ").title()
        configs.append({"name": f.stem, "label": label, "path": str(f)})
    return configs


@app.route("/")
def index():
    configs = get_available_configs()
    return render_template("index.html", configs=configs)


@app.route("/convert", methods=["POST"])
def convert():
    # Validate inputs
    if "source_file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400

    source_file = request.files["source_file"]
    config_name = request.form.get("config_name", "")

    if not source_file.filename:
        return jsonify({"error": "No file selected."}), 400

    suffix = Path(source_file.filename).suffix.lower()
    if suffix not in (".xlsx", ".xls", ".xlsm", ".csv"):
        return jsonify({"error": "Unsupported file type. Please upload .xlsx or .csv."}), 400

    config_path = CONFIGS_DIR / f"{config_name}.yaml"
    if not config_path.exists():
        return jsonify({"error": f"Config '{config_name}' not found."}), 400

    # Save uploaded file to temp
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        source_file.save(tmp.name)
        tmp_path = Path(tmp.name)

    try:
        # Load config
        config = load_config(config_path)

        # Load source data
        data_df, col_name_map = load_source(tmp_path, config)
        if len(data_df) == 0:
            return jsonify({"error": "No employee rows found in the uploaded file."}), 400

        # Pre-resolve header lookups
        mappings = config["mappings"]
        pre_resolve_source_headers(mappings, data_df)

        # Build metadata rows
        if TEMPLATE_PATH.exists():
            metadata_rows = load_deel_template(TEMPLATE_PATH, config)
        else:
            metadata_rows = build_metadata_rows(config)

        # Transform all rows
        output_cols = config["target"]["columns"]
        field_col = config["target"].get("field_column", "FIELD")
        transforms_cfg = config.get("transforms", {})
        data_rows = []
        warnings_list = []

        import logging as _logging

        class WarningCapture(_logging.Handler):
            def emit(self, record):
                if record.levelno == _logging.WARNING:
                    warnings_list.append(record.getMessage())

        handler = WarningCapture()
        logging.getLogger("deel_converter").addHandler(handler)

        for row_num, (_, row) in enumerate(data_df.iterrows(), start=1):
            transformed = transform_row(row, mappings, transforms_cfg, col_name_map, row_num)
            data_rows.append(transformed)

        logging.getLogger("deel_converter").removeHandler(handler)

        # Write to in-memory CSV
        output_buffer = io.StringIO()
        import csv
        writer = csv.writer(output_buffer, quoting=csv.QUOTE_MINIMAL)
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

        output_buffer.seek(0)
        output_bytes = io.BytesIO(output_buffer.getvalue().encode("utf-8"))

        stem = Path(source_file.filename).stem
        download_name = f"{stem}_deel_import.csv"

        return send_file(
            output_bytes,
            mimetype="text/csv",
            as_attachment=True,
            download_name=download_name,
        )

    except Exception as exc:
        logger.exception("Conversion failed")
        return jsonify({"error": str(exc)}), 500

    finally:
        tmp_path.unlink(missing_ok=True)


@app.route("/configs")
def list_configs():
    return jsonify(get_available_configs())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=True, port=port)
