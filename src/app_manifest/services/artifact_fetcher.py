"""Download artifacts and create mini-manifests.

Helm charts: downloads via helm CLI, extracts Chart.yaml,
values.schema.json, resource-profiles, computes SHA-256.

Docker images: if a component has a reference in the config,
creates a minimal mini-manifest from the reference without a hash
(hash is unknown without pulling the image).
"""

import base64
import hashlib
import subprocess
import tarfile
import tempfile
from pathlib import Path

import yaml

from app_manifest.models.cyclonedx import (
    CdxAttachment,
    CdxComponent,
    CdxDataContents,
    CdxDataEntry,
    CdxHash,
    CycloneDxBom,
    CdxMetadata,
    CdxMetadataComponent,
    CdxTool,
    CdxToolsWrapper,
    _make_bom_ref,
)
from app_manifest.models.config import BuildConfig, ComponentConfig, MimeType
from app_manifest.models.regdef import RegistryDefinition
from app_manifest.services.purl import make_docker_purl, make_helm_purl, parse_docker_reference

_HELM_TYPES = {MimeType.HELM_CHART, MimeType.Q_HELM_CHART}
_DOCKER_TYPES = {MimeType.DOCKER_IMAGE}


def fetch_components_from_config(
    config: BuildConfig,
    regdef: RegistryDefinition | None = None,
) -> list[tuple[str, CycloneDxBom]]:
    """Process all components with a reference defined in the config.

    - Helm charts: pulls via helm pull, creates a full mini-manifest.
    - Docker images: creates a minimal mini-manifest from the reference (no hash).

    Returns a list of (config_name, bom) for each processed component.
    """
    results = []
    for comp in config.components:
        if not comp.reference:
            continue
        if comp.mime_type in _HELM_TYPES:
            bom = fetch_helm_component(comp.reference, regdef, mime_type=comp.mime_type.value)
            results.append((comp.name, bom))
        elif comp.mime_type in _DOCKER_TYPES:
            bom = fetch_docker_component_from_reference(comp, regdef)
            results.append((comp.name, bom))
    return results


# Backwards compatibility alias
fetch_helm_components_from_config = fetch_components_from_config


def fetch_docker_component_from_reference(
    comp_config: ComponentConfig,
    regdef: RegistryDefinition | None = None,
) -> CycloneDxBom:
    """Create a mini-manifest for a Docker image from a reference.

    Hash is not computed (image is not pulled).
    name, version, group are taken from the reference;
    name is taken from the config so that generate can match it.
    """
    from datetime import datetime, timezone

    _, version, group = parse_docker_reference(comp_config.reference)
    if not group:
        import sys
        print(
            f"WARNING: no group for component '{comp_config.name}' "
            f"(reference '{comp_config.reference}' has no namespace/org)",
            file=sys.stderr,
        )
    purl = make_docker_purl(comp_config.reference, regdef)

    component = CdxComponent(
        bom_ref=_make_bom_ref(comp_config.name),
        type="container",
        mime_type=comp_config.mime_type.value,
        name=comp_config.name,
        version=version,
        group=group or None,
        purl=purl,
    )

    meta = CdxMetadata(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        component=CdxMetadataComponent(
            bom_ref=_make_bom_ref("am-build-cli"),
            name="am-build-cli",
            version="0.1.0",
        ),
        tools=CdxToolsWrapper(
            components=[CdxTool(name="am-build-cli", version="0.1.0")]
        ),
    )

    return CycloneDxBom(
        metadata=meta,
        components=[component],
        dependencies=[],
    )


def fetch_helm_component(
    reference: str,
    regdef: RegistryDefinition | None = None,
    mime_type: str = "application/vnd.nc.helm.chart",
) -> CycloneDxBom:
    """Pull a Helm chart and create a CycloneDX mini-manifest.

    reference — OCI URL (e.g. oci://registry.example.com/charts/my-chart:1.0)
    regdef — Registry Definition for PURL (optional)
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # 1. Pull the chart
        tgz_path = _helm_pull(reference, tmp_path)

        # 2. Compute the archive hash
        chart_hash = _compute_sha256(tgz_path)

        # 3. Extract the archive
        extract_dir = tmp_path / "extracted"
        _extract_chart(tgz_path, extract_dir)

        # 4. Find the chart root directory
        chart_dir = _find_chart_dir(extract_dir)

        # 5. Read Chart.yaml
        chart_yaml = _read_chart_yaml(chart_dir)

        # 6. Collect data
        name = chart_yaml.get("name", "unknown")
        version = chart_yaml.get("version", "")
        app_version = chart_yaml.get("appVersion", version)

        # 7. PURL
        purl = make_helm_purl(reference, regdef) if reference else None

        # 8. Nested components (values.schema.json, resource-profiles)
        nested = _extract_nested_components(chart_dir)

        # 9. Build the component
        component = CdxComponent(
            bom_ref=_make_bom_ref(name),
            type="application",
            mime_type=mime_type,
            name=name,
            version=app_version or version,
            purl=purl,
            hashes=[CdxHash(alg="SHA-256", content=chart_hash)],
            components=nested,
        )

        # 10. Build the mini-manifest
        from datetime import datetime, timezone

        meta = CdxMetadata(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            component=CdxMetadataComponent(
                bom_ref=_make_bom_ref("am-build-cli"),
                name="am-build-cli",
                version="0.1.0",
            ),
            tools=CdxToolsWrapper(
                components=[CdxTool(name="am-build-cli", version="0.1.0")]
            ),
        )

        return CycloneDxBom(
            metadata=meta,
            components=[component],
            dependencies=[],
        )


def _helm_pull(reference: str, dest: Path) -> Path:
    """Pull a Helm chart via the helm CLI."""
    try:
        result = subprocess.run(
            ["helm", "pull", reference, "--destination", str(dest)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "helm CLI not found. Install helm: https://helm.sh/docs/intro/install/"
        )

    if result.returncode != 0:
        raise RuntimeError(f"helm pull failed: {result.stderr.strip()}")

    # Find the downloaded .tgz file
    tgz_files = sorted(dest.glob("*.tgz"))
    if not tgz_files:
        raise RuntimeError(f"No .tgz file found after helm pull in {dest}")
    if len(tgz_files) > 1:
        import warnings
        warnings.warn(
            f"Multiple .tgz files found after helm pull, using first: "
            + ", ".join(f.name for f in tgz_files),
            stacklevel=2,
        )

    return tgz_files[0]


def _compute_sha256(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _extract_chart(tgz_path: Path, dest: Path) -> None:
    """Extract a .tgz archive."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tgz_path, "r:gz") as tar:
        tar.extractall(dest, filter="data")


def _find_chart_dir(extract_dir: Path) -> Path:
    """Find the chart root directory in the extracted archive."""
    # Helm charts typically have the structure: chart-name/Chart.yaml
    for child in extract_dir.iterdir():
        if child.is_dir() and (child / "Chart.yaml").exists():
            return child

    # Chart.yaml may also be directly in extract_dir
    if (extract_dir / "Chart.yaml").exists():
        return extract_dir

    raise RuntimeError(f"Chart.yaml not found in extracted chart at {extract_dir}")


def _read_chart_yaml(chart_dir: Path) -> dict:
    """Read Chart.yaml."""
    chart_file = chart_dir / "Chart.yaml"
    with open(chart_file, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _extract_nested_components(chart_dir: Path) -> list[CdxComponent]:
    """Extract nested components: values.schema.json and resource-profiles."""
    result: list[CdxComponent] = []

    # values.schema.json
    schema_file = chart_dir / "values.schema.json"
    if schema_file.exists():
        content_b64 = base64.b64encode(
            schema_file.read_bytes()
        ).decode("ascii")

        result.append(CdxComponent(
            bom_ref=_make_bom_ref("values.schema.json"),
            type="data",
            mime_type="application/vnd.nc.helm.values.schema",
            name="values.schema.json",
            data=[CdxDataEntry(
                type="configuration",
                name="values.schema.json",
                contents=CdxDataContents(
                    attachment=CdxAttachment(
                        content_type="application/json",
                        encoding="base64",
                        content=content_b64,
                    )
                ),
            )],
        ))

    # resource-profiles
    profiles_dir = chart_dir / "resource-profiles"
    if profiles_dir.exists() and profiles_dir.is_dir():
        data_entries: list[CdxDataEntry] = []
        for profile_file in sorted(profiles_dir.glob("*.yaml")):
            content_b64 = base64.b64encode(
                profile_file.read_bytes()
            ).decode("ascii")
            data_entries.append(CdxDataEntry(
                type="configuration",
                name=profile_file.name,
                contents=CdxDataContents(
                    attachment=CdxAttachment(
                        content_type="application/yaml",
                        encoding="base64",
                        content=content_b64,
                    )
                ),
            ))

        if data_entries:
            result.append(CdxComponent(
                bom_ref=_make_bom_ref("resource-profile-baselines"),
                type="data",
                mime_type="application/vnd.nc.resource-profile-baseline",
                name="resource-profile-baselines",
                data=data_entries,
            ))

    return result
