"""Registry Definition file loader.

Reads a YAML file describing the registry and returns a RegistryDefinition.
"""

from pathlib import Path

import yaml

from app_manifest.models.regdef import RegistryDefinition


def load_registry_definition(path: Path) -> RegistryDefinition:
    """Read a Registry Definition file."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return RegistryDefinition.model_validate(raw)
