from pathlib import Path

import yaml

from app_manifest.models.config import BuildConfig


def load_build_config(path: Path) -> BuildConfig:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)  # yaml → словарь Python
    return BuildConfig.model_validate(raw)  # словарь → Pydantic модель
