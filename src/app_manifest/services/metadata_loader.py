import json
from pathlib import Path

from app_manifest.models.cyclonedx import CdxComponent
from app_manifest.models.metadata import ComponentMetadata


def load_component_metadata(path: Path) -> ComponentMetadata:

    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return ComponentMetadata.model_validate(raw)


def _expand_paths(paths: list[Path]) -> list[Path]:
    result = []
    for p in paths:
        if p.is_dir():
            result.extend(sorted(p.glob("*.json")))
        else:
            result.append(p)
    return result


def load_all_metadata(paths: list[Path]) -> dict[str, ComponentMetadata]:
    result = {}
    for p in _expand_paths(paths):
        meta = load_component_metadata(p)
        result[meta.name] = meta
    return result


def load_mini_manifest(path: Path) -> CdxComponent:
    """Load a mini-manifest and extract the component.

    A mini-manifest is a CycloneDX BOM with a single entry in components[].
    """
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    components = raw.get("components") or []
    if not components:
        raise ValueError(f"No components found in mini-manifest {path}")

    comp_data = components[0]
    return CdxComponent.model_validate(comp_data)


def load_all_mini_manifests(
    paths: list[Path],
) -> dict[tuple[str, str], CdxComponent]:
    """Load mini-manifests and index them by (name, mime-type).

    Key: (name, mime-type) — unique component identifier.
    """
    result: dict[tuple[str, str], CdxComponent] = {}
    for p in _expand_paths(paths):
        comp = load_mini_manifest(p)
        key = (comp.name, comp.mime_type)
        result[key] = comp
    return result
