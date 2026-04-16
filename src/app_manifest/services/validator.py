"""Application Manifest validation against JSON Schema."""

import json
import sys
from pathlib import Path

import jsonschema


def _schema_path() -> Path:
    # PyInstaller extracts data files to sys._MEIPASS at runtime
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent))
    return base / "app_manifest" / "schemas" / "application-manifest.schema.json"


def validate_manifest(manifest: dict) -> list[str]:
    """Validate manifest against JSON Schema.

    Returns a list of errors (empty if the manifest is valid).
    """
    schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(manifest), key=lambda e: list(e.path))
    return [_format_error(e) for e in errors]


def _format_error(error: jsonschema.ValidationError) -> str:
    path = " -> ".join(str(p) for p in error.absolute_path) if error.absolute_path else "root"
    return f"{path}: {error.message}"
