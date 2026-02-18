"""Валидация Application Manifest по JSON Schema."""

import json
from pathlib import Path

import jsonschema

_SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "application-manifest.schema.json"


def validate_manifest(manifest: dict) -> list[str]:
    """Валидировать манифест по JSON Schema.

    Возвращает список ошибок (пустой если манифест валиден).
    """
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(manifest), key=lambda e: list(e.path))
    return [_format_error(e) for e in errors]


def _format_error(error: jsonschema.ValidationError) -> str:
    path = " -> ".join(str(p) for p in error.absolute_path) if error.absolute_path else "root"
    return f"{path}: {error.message}"
