"""DD ↔ AMv2 Converter.

Implements two transformations:
  1. DD → AMv2  (convert_dd_to_amv2)
  2. AMv2 → DD  (convert_amv2_to_dd)

Both transformations are isolated — they do not touch any existing
manifest_builder, artifact_fetcher, or component_builder logic.

DD → AMv2 requires:
  - DeploymentDescriptor (parsed DD JSON)
  - BuildConfig (for dependencies, standalone-runnable, valuesPathPrefix)
  - RegistryDefinition (for full_image_name / full_chart_name → PURL)
  - app_name, app_version (from --name / --version CLI flags)
  - zip_path (optional, for values.schema.json + resource-profiles)

AMv2 → DD requires:
  - CycloneDxBom (parsed AMv2 JSON)
  - RegistryDefinition (for PURL → full_image_name / full_chart_name)
"""

import base64
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app_manifest.models.config import BuildConfig, MimeType
from app_manifest.models.cyclonedx import (
    CdxAttachment,
    CdxComponent,
    CdxDataContents,
    CdxDataEntry,
    CdxDependency,
    CdxHash,
    CdxMetadata,
    CdxMetadataComponent,
    CdxProperty,
    CdxTool,
    CdxToolsWrapper,
    CycloneDxBom,
    _make_bom_ref,
)
from app_manifest.models.dd import DdChart, DdService, DeploymentDescriptor
from app_manifest.models.regdef import RegistryDefinition
from app_manifest.services.purl import make_docker_purl, make_helm_purl

_MIME_DOCKER = MimeType.DOCKER_IMAGE.value
_MIME_HELM = MimeType.HELM_CHART.value
_MIME_STANDALONE = MimeType.STANDALONE_RUNNABLE.value
_MIME_VALUES_SCHEMA = "application/vnd.nc.helm.values.schema"
_MIME_RESOURCE_PROFILE = "application/vnd.nc.resource-profile-baseline"


# ─────────────────────────────────────────────────────────────
# DD → AMv2
# ─────────────────────────────────────────────────────────────

def convert_dd_to_amv2(
    dd: DeploymentDescriptor,
    config: BuildConfig,
    regdef: RegistryDefinition,
    app_name: str,
    app_version: str,
    zip_path: Path | None = None,
) -> tuple[CycloneDxBom, list[str]]:
    """Convert a Deployment Descriptor to an Application Manifest v2.

    Returns:
        (bom, warnings) — assembled CycloneDx BOM and list of warnings.
    """
    warnings: list[str] = []

    # Step 3: Transform services → docker images + service helm charts
    docker_components: list[CdxComponent] = []
    service_helm_charts: list[CdxComponent] = []

    # bom-ref maps by component name for dependency wiring
    docker_bom_refs: dict[str, str] = {}    # image_name → bom-ref
    helm_bom_refs: dict[str, str] = {}      # service_name → bom-ref

    # Map: service_name → docker bom-ref (for artifactMappings)
    service_name_to_docker_ref: dict[str, str] = {}

    for service in dd.services:
        docker_comp = _dd_service_to_docker(service, regdef, warnings)
        docker_components.append(docker_comp)
        docker_bom_refs[service.image_name] = docker_comp.bom_ref

        if service.image_type == "service" and service.service_name:
            helm_comp = _dd_service_to_helm(service, regdef, warnings)
            service_helm_charts.append(helm_comp)
            helm_bom_refs[service.service_name] = helm_comp.bom_ref
            service_name_to_docker_ref[service.service_name] = docker_comp.bom_ref

    # Step 4: Transform charts → app-chart (umbrella)
    app_chart: CdxComponent | None = None
    if dd.charts:
        app_chart = _dd_chart_to_helm(
            dd.charts[0],
            regdef,
            service_helm_charts,
            warnings,
        )

    # Step 5: Extract additional components from ZIP
    if zip_path is not None:
        _attach_zip_components(zip_path, app_chart, service_helm_charts, warnings)

    # Step 2: Create standalone-runnable from Build Config
    standalone_comp = _build_standalone_from_config(config, app_name, app_version)

    # Assemble top-level components:
    # standalone + docker images + app-chart (which contains service charts)
    # If no app-chart — service charts go to top level
    top_level: list[CdxComponent] = [standalone_comp] + docker_components
    if app_chart:
        top_level.append(app_chart)
    else:
        top_level.extend(service_helm_charts)

    # Step 6: Build dependencies from Build Config
    dependencies = _build_dependencies_from_config(
        config=config,
        standalone_bom_ref=standalone_comp.bom_ref,
        docker_bom_refs=docker_bom_refs,
        helm_bom_refs=helm_bom_refs,
        app_chart=app_chart,
        service_name_to_docker_ref=service_name_to_docker_ref,
        warnings=warnings,
    )

    # Step 7: Generate metadata
    app_bom_ref = _make_bom_ref(app_name)
    metadata = CdxMetadata(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        component=CdxMetadataComponent(
            bom_ref=app_bom_ref,
            name=app_name,
            version=app_version,
        ),
        tools=CdxToolsWrapper(
            components=[CdxTool(name="am-build-cli", version="0.1.0")]
        ),
    )

    bom = CycloneDxBom(
        metadata=metadata,
        components=top_level,
        dependencies=dependencies,
    )
    return bom, warnings


def _dd_service_to_docker(
    service: DdService,
    regdef: RegistryDefinition,
    warnings: list[str],
) -> CdxComponent:
    """Convert a DD service entry to a docker CdxComponent."""
    bom_ref = _make_bom_ref(service.image_name)

    purl: str | None = None
    try:
        purl = make_docker_purl(service.full_image_name, regdef)
    except ValueError as e:
        warnings.append(f"WARNING: cannot generate PURL for '{service.image_name}': {e}")

    hashes: list[CdxHash] = []
    if service.docker_digest:
        hashes.append(CdxHash(alg="SHA-256", content=service.docker_digest))

    return CdxComponent(
        bom_ref=bom_ref,
        type="container",
        mime_type=_MIME_DOCKER,
        name=service.image_name,
        group=service.docker_repository_name,
        version=service.docker_tag,
        purl=purl,
        hashes=hashes or None,
    )


def _dd_service_to_helm(
    service: DdService,
    regdef: RegistryDefinition,
    warnings: list[str],
) -> CdxComponent:
    """Convert a DD service entry (image_type=service) to a helm CdxComponent."""
    assert service.service_name is not None
    bom_ref = _make_bom_ref(service.service_name)

    return CdxComponent(
        bom_ref=bom_ref,
        type="application",
        mime_type=_MIME_HELM,
        name=service.service_name,
        version=service.version,
        properties=[CdxProperty(name="isLibrary", value=False)],
        components=[],
    )


def _dd_chart_to_helm(
    chart: DdChart,
    regdef: RegistryDefinition,
    service_helm_charts: list[CdxComponent],
    warnings: list[str],
) -> CdxComponent:
    """Convert a DD chart entry to an umbrella helm CdxComponent."""
    bom_ref = _make_bom_ref(chart.helm_chart_name)

    purl: str | None = None
    try:
        helm_ref = _full_chart_name_to_helm_ref(chart.full_chart_name)
        purl = make_helm_purl(helm_ref, regdef)
    except ValueError as e:
        warnings.append(
            f"WARNING: cannot generate PURL for chart '{chart.helm_chart_name}': {e}"
        )

    return CdxComponent(
        bom_ref=bom_ref,
        type="application",
        mime_type=_MIME_HELM,
        name=chart.helm_chart_name,
        version=chart.helm_chart_version,
        purl=purl,
        hashes=[],
        properties=[CdxProperty(name="isLibrary", value=False)],
        components=list(service_helm_charts),
    )


def _full_chart_name_to_helm_ref(full_chart_name: str) -> str:
    """Convert DD full_chart_name to a helm reference suitable for make_helm_purl.

    Input:  "https://registry.example.com/charts/my-chart-1.0.0.tgz"
    Output: "https://registry.example.com/charts/my-chart:1.0.0"

    Parses chart name and version from the .tgz filename using the convention:
    {name}-{version}.tgz where version starts with a digit or is a known semver-like pattern.
    """
    # Strip .tgz
    without_tgz = full_chart_name
    if without_tgz.endswith(".tgz"):
        without_tgz = without_tgz[:-4]

    # Split into base_url and filename
    last_slash = without_tgz.rfind("/")
    if last_slash == -1:
        raise ValueError(f"Cannot parse full_chart_name: '{full_chart_name}'")

    base_url = without_tgz[:last_slash]
    chart_filename = without_tgz[last_slash + 1:]

    # Parse "{name}-{version}" — version is the last segment starting with a digit
    # or matching a known version pattern (e.g. 0.0.0-release-2025.4-...)
    match = re.match(r"^(.+?)-(\d+\..*)$", chart_filename)
    if not match:
        # Fallback: last "-" separated segment
        parts = chart_filename.rsplit("-", 1)
        if len(parts) == 2:
            chart_name, version = parts
        else:
            raise ValueError(
                f"Cannot parse chart name/version from '{chart_filename}' "
                f"in '{full_chart_name}'"
            )
    else:
        chart_name = match.group(1)
        version = match.group(2)

    return f"{base_url}/{chart_name}:{version}"


def _build_standalone_from_config(
    config: BuildConfig,
    app_name: str,
    app_version: str,
) -> CdxComponent:
    """Create a standalone-runnable component from Build Config."""
    standalone_configs = [
        c for c in config.components
        if c.mime_type == MimeType.STANDALONE_RUNNABLE
    ]

    name = standalone_configs[0].name if standalone_configs else app_name

    return CdxComponent(
        bom_ref=_make_bom_ref(name),
        type="application",
        mime_type=_MIME_STANDALONE,
        name=name,
        version=app_version,
        properties=[],
        components=[],
    )


def _build_dependencies_from_config(
    config: BuildConfig,
    standalone_bom_ref: str,
    docker_bom_refs: dict[str, str],
    helm_bom_refs: dict[str, str],
    app_chart: CdxComponent | None,
    service_name_to_docker_ref: dict[str, str],
    warnings: list[str],
) -> list[CdxDependency]:
    """Build the dependencies array from Build Config.

    Wires:
    - standalone → app-chart (if exists) or all service helm charts + standalone dockers
    - app-chart → service helm charts
    - service helm chart → docker image (via valuesPathPrefix from config)
    """
    dependencies: list[CdxDependency] = []

    # Build config index: (name, mime_type) → ComponentConfig
    config_index = {(c.name, c.mime_type): c for c in config.components}

    # Combined bom-ref lookups: name → bom-ref for both docker and helm
    all_docker_refs = dict(docker_bom_refs)   # image_name → bom-ref
    all_helm_refs = dict(helm_bom_refs)        # service_name → bom-ref

    # Find which docker images are associated with service charts
    docker_names_with_service = set()
    for service_name, docker_ref in service_name_to_docker_ref.items():
        # Reverse lookup: docker_ref → image_name
        for img_name, ref in docker_bom_refs.items():
            if ref == docker_ref:
                docker_names_with_service.add(img_name)

    standalone_deps: list[str] = []

    if app_chart:
        standalone_deps.append(app_chart.bom_ref)
        # standalone also depends on standalone docker images (image_type=image)
        for img_name, ref in docker_bom_refs.items():
            if img_name not in docker_names_with_service:
                standalone_deps.append(ref)

        # app-chart → all service helm charts
        service_chart_refs = [c.bom_ref for c in (app_chart.components or [])]
        if service_chart_refs:
            dependencies.append(CdxDependency(
                ref=app_chart.bom_ref,
                depends_on=service_chart_refs,
            ))
    else:
        # No app-chart: standalone → service helm charts + standalone dockers
        for ref in all_helm_refs.values():
            standalone_deps.append(ref)
        for img_name, ref in docker_bom_refs.items():
            if img_name not in docker_names_with_service:
                standalone_deps.append(ref)

    if standalone_deps:
        dependencies.append(CdxDependency(
            ref=standalone_bom_ref,
            depends_on=standalone_deps,
        ))

    # Service helm chart → docker image dependency
    # Use valuesPathPrefix from Build Config if available, else plain dependency
    for service_name, helm_ref in all_helm_refs.items():
        docker_ref = service_name_to_docker_ref.get(service_name)
        if not docker_ref:
            continue

        # Try to find valuesPathPrefix from Build Config
        helm_key = (service_name, MimeType.HELM_CHART)
        comp_config = config_index.get(helm_key)

        if comp_config:
            # Build artifactMappings from config
            mappings: dict[str, dict] = {}
            for dep in comp_config.depends_on:
                if dep.mime_type == MimeType.DOCKER_IMAGE and dep.values_path_prefix:
                    # Find the bom-ref for this docker dep by name
                    dep_bom_ref = docker_bom_refs.get(dep.name)
                    if dep_bom_ref:
                        mappings[dep_bom_ref] = {"valuesPathPrefix": dep.values_path_prefix}

            if mappings:
                # Add artifactMappings property to service helm chart component
                # (we mutate the nested component inside app_chart.components)
                _add_artifact_mappings_to_chart(
                    app_chart, helm_ref, mappings
                )

        dependencies.append(CdxDependency(
            ref=helm_ref,
            depends_on=[docker_ref],
        ))

    return dependencies


def _add_artifact_mappings_to_chart(
    app_chart: CdxComponent | None,
    helm_bom_ref: str,
    mappings: dict[str, dict],
) -> None:
    """Add artifactMappings property to a service chart inside app_chart.components."""
    if not app_chart or not app_chart.components:
        return
    for comp in app_chart.components:
        if comp.bom_ref == helm_bom_ref:
            props = list(comp.properties or [])
            props.append(CdxProperty(
                name="qubership:helm.values.artifactMappings",
                value=mappings,
            ))
            # CdxComponent is a Pydantic model — use model_copy for immutability
            # but since we're building, direct mutation via __dict__ is acceptable here
            comp.properties = props
            return


def _attach_zip_components(
    zip_path: Path,
    app_chart: CdxComponent | None,
    service_helm_charts: list[CdxComponent],
    warnings: list[str],
) -> None:
    """Extract values.schema.json and resource-profiles from ZIP and attach them.

    If app-chart exists — attaches to app-chart only.
    Otherwise — attaches to each service helm chart.
    """
    if not zip_path.exists():
        warnings.append(f"WARNING: ZIP file not found: {zip_path}")
        return

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()

            values_schema_comp = _extract_values_schema(zf, names, warnings)
            resource_profile_comp = _extract_resource_profiles(zf, names, warnings)

            targets = [app_chart] if app_chart else service_helm_charts
            for target in targets:
                if target is None:
                    continue
                nested = list(target.components or [])
                if values_schema_comp:
                    nested.append(values_schema_comp.model_copy(
                        update={"bom_ref": _make_bom_ref("values.schema.json")}
                    ))
                if resource_profile_comp:
                    nested.append(resource_profile_comp.model_copy(
                        update={"bom_ref": _make_bom_ref("resource-profile-baselines")}
                    ))
                target.components = nested

    except zipfile.BadZipFile as e:
        warnings.append(f"WARNING: cannot open ZIP file '{zip_path}': {e}")


def _extract_values_schema(
    zf: zipfile.ZipFile,
    names: list[str],
    warnings: list[str],
) -> CdxComponent | None:
    """Extract values.schema.json from ZIP and build a CdxComponent."""
    schema_file = next(
        (n for n in names if n.endswith("values.schema.json")), None
    )
    if not schema_file:
        return None

    content = base64.b64encode(zf.read(schema_file)).decode("utf-8")
    return CdxComponent(
        bom_ref=_make_bom_ref("values.schema.json"),
        type="data",
        mime_type=_MIME_VALUES_SCHEMA,
        name="values.schema.json",
        data=[CdxDataEntry(
            type="configuration",
            name="values.schema.json",
            contents=CdxDataContents(
                attachment=CdxAttachment(
                    content_type="application/json",
                    encoding="base64",
                    content=content,
                )
            ),
        )],
    )


def _extract_resource_profiles(
    zf: zipfile.ZipFile,
    names: list[str],
    warnings: list[str],
) -> CdxComponent | None:
    """Extract resource-profiles/*.yaml from ZIP and build a CdxComponent."""
    profile_files = [
        n for n in names
        if re.search(r"resource-profiles?/[^/]+\.ya?ml$", n)
    ]
    if not profile_files:
        return None

    data_entries: list[CdxDataEntry] = []
    for profile_file in sorted(profile_files):
        filename = Path(profile_file).name
        content = base64.b64encode(zf.read(profile_file)).decode("utf-8")
        data_entries.append(CdxDataEntry(
            type="configuration",
            name=filename,
            contents=CdxDataContents(
                attachment=CdxAttachment(
                    content_type="application/yaml",
                    encoding="base64",
                    content=content,
                )
            ),
        ))

    return CdxComponent(
        bom_ref=_make_bom_ref("resource-profile-baselines"),
        type="data",
        mime_type=_MIME_RESOURCE_PROFILE,
        name="resource-profile-baselines",
        data=data_entries,
    )


# ─────────────────────────────────────────────────────────────
# AMv2 → DD
# ─────────────────────────────────────────────────────────────

def convert_amv2_to_dd(
    bom: CycloneDxBom,
    regdef: RegistryDefinition,
) -> tuple[DeploymentDescriptor, list[str]]:
    """Convert an Application Manifest v2 to a Deployment Descriptor.

    Returns:
        (dd, warnings) — assembled DeploymentDescriptor and list of warnings.
    """
    warnings: list[str] = []

    # Step 1: Identify app-chart and service chart → docker image mappings
    app_chart = _find_app_chart(bom.components)
    service_chart_to_docker: dict[str, str] = {}  # service chart bom-ref → docker bom-ref

    if app_chart and app_chart.components:
        for service_chart in app_chart.components:
            if service_chart.mime_type != _MIME_HELM:
                continue
            docker_ref = _extract_docker_ref_from_mappings(service_chart)
            if docker_ref:
                service_chart_to_docker[service_chart.bom_ref] = docker_ref

    # Build set of docker bom-refs that are associated with service charts
    docker_refs_with_charts: set[str] = set(service_chart_to_docker.values())

    # Step 2: Extract services from docker images
    services: list[DdService] = []
    for comp in bom.components:
        if comp.mime_type != _MIME_DOCKER:
            continue

        if comp.bom_ref in docker_refs_with_charts:
            # Find associated service chart
            service_chart = _find_service_chart_for_docker(
                comp.bom_ref, service_chart_to_docker, app_chart
            )
            service = _docker_comp_to_dd_service(
                comp, regdef, service_chart, warnings
            )
        else:
            service = _docker_comp_to_dd_service(comp, regdef, None, warnings)

        services.append(service)

    # Step 3: Extract app-chart
    charts: list[DdChart] = []
    if app_chart:
        dd_chart = _helm_comp_to_dd_chart(app_chart, regdef, warnings)
        if dd_chart:
            charts.append(dd_chart)

    dd = DeploymentDescriptor(
        services=services,
        charts=charts,
    )
    return dd, warnings


def _find_app_chart(components: list[CdxComponent]) -> CdxComponent | None:
    """Find the app-chart: root-level helm chart with non-empty components."""
    for comp in components:
        if comp.mime_type == _MIME_HELM and comp.components:
            return comp
    return None


def _extract_docker_ref_from_mappings(service_chart: CdxComponent) -> str | None:
    """Extract docker image bom-ref from artifactMappings property."""
    for prop in (service_chart.properties or []):
        if prop.name == "qubership:helm.values.artifactMappings":
            mappings = prop.value
            if isinstance(mappings, dict) and mappings:
                return next(iter(mappings))  # first docker bom-ref
    return None


def _find_service_chart_for_docker(
    docker_bom_ref: str,
    service_chart_to_docker: dict[str, str],
    app_chart: CdxComponent | None,
) -> CdxComponent | None:
    """Find the service chart component that maps to the given docker bom-ref."""
    chart_bom_ref = next(
        (k for k, v in service_chart_to_docker.items() if v == docker_bom_ref), None
    )
    if not chart_bom_ref or not app_chart:
        return None
    return next(
        (c for c in (app_chart.components or []) if c.bom_ref == chart_bom_ref),
        None,
    )


def _docker_comp_to_dd_service(
    comp: CdxComponent,
    regdef: RegistryDefinition,
    service_chart: CdxComponent | None,
    warnings: list[str],
) -> DdService:
    """Convert a docker CdxComponent to a DD service entry."""
    full_image_name: str | None = None
    docker_registry: str | None = None

    if comp.purl:
        try:
            full_image_name, docker_registry = _purl_to_docker_artifact_ref(
                comp.purl, regdef
            )
        except ValueError as e:
            warnings.append(
                f"WARNING: cannot convert PURL to artifact ref for '{comp.name}': {e}"
            )

    docker_digest: str | None = None
    if comp.hashes:
        for h in comp.hashes:
            if h.alg == "SHA-256":
                docker_digest = h.content
                break

    if service_chart:
        return DdService(
            image_name=comp.name,
            docker_repository_name=comp.group,
            docker_tag=comp.version,
            full_image_name=full_image_name or "",
            docker_registry=docker_registry,
            docker_digest=docker_digest,
            image_type="service",
            service_name=service_chart.name,
            version=service_chart.version,
        )
    else:
        return DdService(
            image_name=comp.name,
            docker_repository_name=comp.group,
            docker_tag=comp.version,
            full_image_name=full_image_name or "",
            docker_registry=docker_registry,
            docker_digest=docker_digest,
            image_type="image",
        )


def _helm_comp_to_dd_chart(
    comp: CdxComponent,
    regdef: RegistryDefinition,
    warnings: list[str],
) -> DdChart | None:
    """Convert an app-chart CdxComponent to a DD chart entry."""
    full_chart_name: str | None = None
    helm_registry: str | None = None

    if comp.purl:
        try:
            full_chart_name, helm_registry = _purl_to_helm_artifact_ref(
                comp.purl, regdef
            )
        except ValueError as e:
            warnings.append(
                f"WARNING: cannot convert PURL to artifact ref for chart '{comp.name}': {e}"
            )

    if not full_chart_name:
        warnings.append(
            f"WARNING: app-chart '{comp.name}' has no PURL — full_chart_name will be empty"
        )
        return None

    return DdChart(
        helm_chart_name=comp.name,
        helm_chart_version=comp.version or "",
        full_chart_name=full_chart_name,
        helm_registry=helm_registry,
        type="app-chart",
    )


# ─────────────────────────────────────────────────────────────
# PURL ↔ Artifact Reference helpers
# ─────────────────────────────────────────────────────────────

def _purl_to_docker_artifact_ref(
    purl: str,
    regdef: RegistryDefinition,
) -> tuple[str, str]:
    """Convert a docker PURL to (full_image_name, docker_registry).

    pkg:docker/namespace/name@version?registry_name=X
    → "registry_uri/namespace/name:version", "registry_uri"
    """
    # Strip prefix
    if not purl.startswith("pkg:docker/"):
        raise ValueError(f"Not a docker PURL: '{purl}'")

    body = purl[len("pkg:docker/"):]
    qualifiers_str = ""
    if "?" in body:
        body, qualifiers_str = body.split("?", 1)

    # Parse namespace/name@version
    if "@" not in body:
        raise ValueError(f"PURL missing version: '{purl}'")
    path, version = body.rsplit("@", 1)

    parts = path.split("/")
    if len(parts) >= 2:
        namespace = "/".join(parts[:-1])
        name = parts[-1]
    else:
        namespace = ""
        name = parts[0]

    # Extract registry_name from qualifiers
    registry_name = _parse_qualifier(qualifiers_str, "registry_name")

    # Resolve registry URI from regdef
    registry_uri = _resolve_registry_uri_docker(registry_name, regdef)

    if namespace:
        full_image_name = f"{registry_uri}/{namespace}/{name}:{version}"
    else:
        full_image_name = f"{registry_uri}/{name}:{version}"

    return full_image_name, registry_uri


def _purl_to_helm_artifact_ref(
    purl: str,
    regdef: RegistryDefinition,
) -> tuple[str, str]:
    """Convert a helm PURL to (full_chart_name, helm_registry).

    pkg:helm/name@version?registry_name=X
    → "https://registry/path/name-version.tgz", "https://registry/path"

    pkg:helm/namespace/name@version?registry_name=X
    → "https://registry/namespace/name-version.tgz", "https://registry/namespace"
    """
    if not purl.startswith("pkg:helm/"):
        raise ValueError(f"Not a helm PURL: '{purl}'")

    body = purl[len("pkg:helm/"):]
    qualifiers_str = ""
    if "?" in body:
        body, qualifiers_str = body.split("?", 1)

    if "@" not in body:
        raise ValueError(f"PURL missing version: '{purl}'")
    path, version = body.rsplit("@", 1)

    parts = path.split("/")
    if len(parts) >= 2:
        namespace = "/".join(parts[:-1])
        name = parts[-1]
    else:
        namespace = ""
        name = parts[0]

    registry_name = _parse_qualifier(qualifiers_str, "registry_name")
    registry_base = _resolve_registry_uri_helm(registry_name, regdef)

    # Strip trailing protocol prefix duplication
    repo_path = regdef.helm_app_config.helm_group_repo_name if (
        regdef.helm_app_config and regdef.helm_app_config.helm_group_repo_name
    ) else None

    if namespace:
        helm_registry = f"{registry_base}/{namespace}"
    elif repo_path:
        helm_registry = f"{registry_base}/{repo_path}"
    else:
        helm_registry = registry_base

    full_chart_name = f"{helm_registry}/{name}-{version}.tgz"
    return full_chart_name, helm_registry


def _parse_qualifier(qualifiers_str: str, key: str) -> str:
    """Extract a qualifier value from a PURL qualifiers string."""
    for part in qualifiers_str.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k == key:
                return v
    return ""


def _resolve_registry_uri_docker(
    registry_name: str,
    regdef: RegistryDefinition,
) -> str:
    """Resolve docker registry URI from registry_name via regdef.

    Returns the groupUri if registry_name matches regdef.name,
    otherwise returns registry_name as-is (fallback).
    """
    if regdef and registry_name == regdef.name and regdef.docker_config:
        uri = regdef.docker_config.group_uri
        if uri:
            # Strip protocol prefix from URI
            for prefix in ("https://", "http://", "docker://"):
                if uri.startswith(prefix):
                    return uri[len(prefix):]
            return uri
    return registry_name


def _resolve_registry_uri_helm(
    registry_name: str,
    regdef: RegistryDefinition,
) -> str:
    """Resolve helm registry base URL from registry_name via regdef.

    Returns the repositoryDomainName if registry_name matches regdef.name,
    otherwise returns registry_name as-is.
    """
    if regdef and registry_name == regdef.name and regdef.helm_app_config:
        domain = regdef.helm_app_config.repository_domain_name
        if domain:
            # Ensure https:// prefix is present
            if not domain.startswith(("https://", "http://", "oci://")):
                domain = f"https://{domain}"
            return domain.rstrip("/")
    return registry_name
