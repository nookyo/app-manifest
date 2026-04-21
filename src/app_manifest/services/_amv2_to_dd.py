"""AMv2 → DD conversion logic.

Converts a CycloneDX Application Manifest v2 into a Deployment Descriptor (legacy JSON).

Requires:
  - CycloneDxBom (parsed AMv2 JSON)
  - RegistryDefinition (for PURL → full_image_name / full_chart_name)

Note: only services[] and charts[] are reconstructed — all other DD sections
(metadata, infrastructures, configurations, frontends, smartplug, jobs, etc.)
are always empty because they cannot be derived from AMv2.
"""

from urllib.parse import unquote

from app_manifest.models.config import MimeType
from app_manifest.models.cyclonedx import CdxComponent, CycloneDxBom
from app_manifest.models.dd import DdChart, DdService, DeploymentDescriptor
from app_manifest.models.regdef import RegistryDefinition
from app_manifest.services.purl import _hosts_match

_MIME_DOCKER = MimeType.DOCKER_IMAGE.value
_MIME_HELM = MimeType.HELM_CHART.value


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
        if prop.name in ("nc:helm.values.artifactMappings", "qubership:helm.values.artifactMappings"):
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

    # Read preserved image_type from property; fall back to structural inference
    saved_image_type: str | None = None
    for prop in (comp.properties or []):
        if prop.name == "nc:dd:image_type":
            saved_image_type = prop.value
            break

    if service_chart:
        image_type = saved_image_type if saved_image_type else "service"
        return DdService(
            image_name=comp.name,
            docker_repository_name=comp.group,
            docker_tag=comp.version,
            full_image_name=full_image_name or "",
            docker_registry=docker_registry,
            docker_digest=docker_digest,
            image_type=image_type,
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
            image_type=saved_image_type if saved_image_type else "image",
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
    registry_name = _parse_qualifier(qualifiers_str, "registry_id") or _parse_qualifier(qualifiers_str, "registry_name")

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

    registry_name = _parse_qualifier(qualifiers_str, "registry_id") or _parse_qualifier(qualifiers_str, "registry_name")
    registry_base = _resolve_registry_uri_helm(registry_name, regdef)

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
    """Resolve docker registry URI from registry_name qualifier.

    registry_name may be a logical name (e.g. "Shared%20Platform%20Registry")
    or a raw hostname. If it matches regdef.name, returns groupUri. Otherwise
    returns the decoded value as-is (raw hostname fallback).
    """
    decoded = unquote(registry_name)
    if regdef and regdef.name and decoded == regdef.name:
        if regdef.docker_config and regdef.docker_config.group_uri:
            return regdef.docker_config.group_uri
    return decoded


def _resolve_registry_uri_helm(
    registry_name: str,
    regdef: RegistryDefinition,
) -> str:
    """Resolve helm registry base URL from registry_name qualifier.

    registry_name may be a logical name or a raw hostname.
    If it matches regdef.name, returns repositoryDomainName with https://.
    Otherwise matches by host or falls back to adding https://.
    """
    decoded = unquote(registry_name)
    if regdef and regdef.name and decoded == regdef.name:
        if regdef.helm_app_config and regdef.helm_app_config.repository_domain_name:
            domain = regdef.helm_app_config.repository_domain_name
            if not domain.startswith(("https://", "http://", "oci://")):
                domain = f"https://{domain}"
            return domain.rstrip("/")

    if regdef and regdef.helm_app_config:
        domain = regdef.helm_app_config.repository_domain_name
        if domain and _hosts_match(decoded, domain):
            if not domain.startswith(("https://", "http://", "oci://")):
                domain = f"https://{domain}"
            return domain.rstrip("/")

    if decoded and not decoded.startswith(("https://", "http://")):
        return f"https://{decoded}"
    return decoded
