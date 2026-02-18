"""Загрузчик Registry Definition файла.

Читает YAML-файл с описанием реестра и возвращает RegistryDefinition.
"""

from pathlib import Path

import yaml

from app_manifest.models.regdef import RegistryDefinition


def load_registry_definition(path: Path) -> RegistryDefinition:
    """Прочитать файл Registry Definition."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return RegistryDefinition.model_validate(raw)
