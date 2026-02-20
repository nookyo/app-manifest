"""CycloneDX mini-manifest builder for a single component.

Converts CI metadata (ComponentMetadata) into a CycloneDX mini-manifest.
Output is a valid CycloneDX BOM with a single component.

Used by the component command.
"""

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
from app_manifest.models.metadata import ComponentMetadata
from app_manifest.models.regdef import RegistryDefinition
from app_manifest.services.purl import make_docker_purl, make_helm_purl

# mime-type patterns for determining the component type
_DOCKER_MIME_PATTERNS = ("docker.image",)
_HELM_MIME_PATTERNS = ("helm.chart",)


def build_component_manifest(
    meta: ComponentMetadata,
    regdef: RegistryDefinition | None = None,
) -> CycloneDxBom:
    """Create a CycloneDX mini-manifest from CI metadata.

    meta — component metadata from CI
    regdef — Registry Definition for PURL generation (optional)

    Returns a CycloneDxBom with a single component in components.
    """
    component = _build_component(meta, regdef)

    return CycloneDxBom(
        metadata=_build_mini_metadata(),
        components=[component],
        dependencies=[],
    )


def _build_mini_metadata() -> CdxMetadata:
    """Minimal metadata section for a mini-manifest."""
    from datetime import datetime, timezone

    return CdxMetadata(
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


def _build_component(
    meta: ComponentMetadata,
    regdef: RegistryDefinition | None,
) -> CdxComponent:
    """Create a CdxComponent from CI metadata."""
    if _is_docker(meta.mime_type):
        return _build_docker(meta, regdef)
    if _is_helm(meta.mime_type):
        return _build_helm(meta, regdef)

    # Unknown type — return a basic component
    return CdxComponent(
        bom_ref=_make_bom_ref(meta.name),
        type=meta.type,
        mime_type=meta.mime_type,
        name=meta.name,
        version=meta.version,
    )


def _build_docker(
    meta: ComponentMetadata,
    regdef: RegistryDefinition | None,
) -> CdxComponent:
    """Docker image → CdxComponent."""
    purl = None
    if meta.reference:
        purl = make_docker_purl(meta.reference, regdef)

    hashes = _convert_hashes(meta)

    return CdxComponent(
        bom_ref=_make_bom_ref(meta.name),
        type="container",
        mime_type=meta.mime_type,
        name=meta.name,
        group=meta.group or None,
        version=meta.version or None,
        purl=purl,
        hashes=hashes,
    )


def _build_helm(
    meta: ComponentMetadata,
    regdef: RegistryDefinition | None,
) -> CdxComponent:
    """Helm chart → CdxComponent."""
    purl = None
    if meta.reference:
        purl = make_helm_purl(meta.reference, regdef)

    # Version: appVersion → version
    version = meta.app_version or meta.version

    hashes = _convert_hashes(meta)
    nested = _convert_nested_components(meta)

    return CdxComponent(
        bom_ref=_make_bom_ref(meta.name),
        type="application",
        mime_type=meta.mime_type,
        name=meta.name,
        version=version,
        purl=purl,
        hashes=hashes,
        components=nested or None,
    )


def _convert_hashes(meta: ComponentMetadata) -> list[CdxHash] | None:
    """Convert hashes from metadata to CdxHash."""
    if not meta.hashes:
        return None
    return [CdxHash(alg=h.alg, content=h.content) for h in meta.hashes]


def _convert_nested_components(meta: ComponentMetadata) -> list[CdxComponent]:
    """Convert nested components (values.schema.json, resource-profiles)."""
    if not meta.components:
        return []

    result: list[CdxComponent] = []
    for nested in meta.components:
        data_entries: list[CdxDataEntry] = []
        for entry in nested.data:
            data_entries.append(CdxDataEntry(
                type=entry.type,
                name=entry.name,
                contents=CdxDataContents(
                    attachment=CdxAttachment(
                        content_type=entry.contents.attachment.content_type,
                        encoding=entry.contents.attachment.encoding,
                        content=entry.contents.attachment.content,
                    )
                ),
            ))

        result.append(CdxComponent(
            bom_ref=_make_bom_ref(nested.name),
            type=nested.type,
            mime_type=nested.mime_type,
            name=nested.name,
            data=data_entries if data_entries else None,
        ))

    return result


def _is_docker(mime_type: str) -> bool:
    """Return True if the mime-type belongs to Docker."""
    return any(p in mime_type for p in _DOCKER_MIME_PATTERNS)


def _is_helm(mime_type: str) -> bool:
    """Return True if the mime-type belongs to Helm."""
    return any(p in mime_type for p in _HELM_MIME_PATTERNS)
