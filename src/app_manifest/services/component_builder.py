"""Построитель мини-манифеста CycloneDX для одного компонента.

Конвертирует CI метаданные (ComponentMetadata) в CycloneDX мини-манифест.
На выходе — валидный CycloneDX BOM с одним компонентом.

Используется командой component.
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

# mime-type паттерны для определения типа компонента
_DOCKER_MIME_PATTERNS = ("docker.image",)
_HELM_MIME_PATTERNS = ("helm.chart",)


def build_component_manifest(
    meta: ComponentMetadata,
    regdef: RegistryDefinition | None = None,
) -> CycloneDxBom:
    """Создать CycloneDX мини-манифест из CI метаданных.

    meta — метаданные компонента из CI
    regdef — Registry Definition для генерации PURL (опционально)

    Возвращает CycloneDxBom с одним компонентом в components.
    """
    component = _build_component(meta, regdef)

    return CycloneDxBom(
        metadata=_build_mini_metadata(),
        components=[component],
        dependencies=[],
    )


def _build_mini_metadata() -> CdxMetadata:
    """Минимальная metadata секция для мини-манифеста."""
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
    """Создать CdxComponent из CI метаданных."""
    if _is_docker(meta.mime_type):
        return _build_docker(meta, regdef)
    if _is_helm(meta.mime_type):
        return _build_helm(meta, regdef)

    # Неизвестный тип — базовый компонент
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
    """Docker-образ → CdxComponent."""
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
    """Helm-чарт → CdxComponent."""
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
    """Преобразовать хеши из metadata в CdxHash."""
    if not meta.hashes:
        return None
    return [CdxHash(alg=h.alg, content=h.content) for h in meta.hashes]


def _convert_nested_components(meta: ComponentMetadata) -> list[CdxComponent]:
    """Преобразовать вложенные компоненты (values.schema.json, resource-profiles)."""
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
    """Проверить что mime-type относится к Docker."""
    return any(p in mime_type for p in _DOCKER_MIME_PATTERNS)


def _is_helm(mime_type: str) -> bool:
    """Проверить что mime-type относится к Helm."""
    return any(p in mime_type for p in _HELM_MIME_PATTERNS)
