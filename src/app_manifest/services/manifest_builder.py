"""Сборщик Application Manifest.

Берёт:
  - BuildConfig (из YAML)
  - CdxComponent (из CycloneDX мини-манифестов)

И собирает из них CycloneDxBom — готовый манифест.

Логика сборки:
  1. Создаём metadata секцию (имя, версия, timestamp, tools)
  2. Определяем sub-chart'ы (helm→helm dependsOn) — они не попадают на верхний уровень
  3. Для каждого компонента из конфига находим готовый CdxComponent из мини-манифеста
  4. Перегенерируем bom-ref (вариант Б — generate контролирует идентификаторы)
  5. Для app-chart (umbrella) — вкладываем sub-chart'ы внутрь components[]
  6. standalone-runnable создаём из конфига (у него нет мини-манифеста)
  7. Генерируем dependencies — связи между компонентами
"""

from datetime import datetime, timezone

from app_manifest.models.config import BuildConfig, ComponentConfig, MimeType
from app_manifest.models.cyclonedx import (
    CdxComponent,
    CdxDependency,
    CdxMetadata,
    CdxMetadataComponent,
    CdxProperty,
    CdxTool,
    CdxToolsWrapper,
    CycloneDxBom,
    _make_bom_ref,
)

# Какие MimeType считаются docker-образами
_DOCKER_TYPES = {MimeType.DOCKER_IMAGE}

# Какие MimeType считаются standalone-runnable
_STANDALONE_TYPES = {MimeType.STANDALONE_RUNNABLE, MimeType.Q_STANDALONE_RUNNABLE}

# Какие MimeType считаются helm-чартами
_HELM_TYPES = {MimeType.HELM_CHART, MimeType.Q_HELM_CHART}


def build_manifest(
    config: BuildConfig,
    mini_manifests: dict[tuple[str, str], CdxComponent],
    version_override: str | None = None,
    name_override: str | None = None,
) -> tuple[CycloneDxBom, list[str]]:
    """Собрать Application Manifest из конфига и мини-манифестов.

    Returns:
        (bom, warnings) — готовый манифест и список предупреждений (пустой если всё хорошо).
    """
    app_name = name_override or config.application_name
    app_version = version_override or config.application_version

    # --- 1. Определяем sub-chart'ы ---
    sub_chart_keys = _find_sub_charts(config)

    # --- 2. Создаём bom-ref для ВСЕХ компонентов (включая sub-charts) ---
    bom_refs: dict[tuple[str, MimeType], str] = {}
    for comp in config.components:
        bom_refs[(comp.name, comp.mime_type)] = _make_bom_ref(comp.name)

    app_bom_ref = _make_bom_ref(app_name)

    # Индекс конфигов по (name, mime_type) для быстрого поиска
    config_index: dict[tuple[str, MimeType], ComponentConfig] = {
        (c.name, c.mime_type): c for c in config.components
    }

    # --- 3. Создаём top-level компоненты (без sub-charts) ---
    components: list[CdxComponent] = []
    warnings: list[str] = []
    for comp_config in config.components:
        key = (comp_config.name, comp_config.mime_type)
        if key in sub_chart_keys:
            continue  # sub-chart — будет вложен в parent

        cdx_comp, warning = _build_component(
            comp_config, mini_manifests, bom_refs, app_version,
            config_index, sub_chart_keys,
        )
        if warning:
            warnings.append(warning)
        if cdx_comp:
            components.append(cdx_comp)

    # --- 4. Создаём dependencies ---
    dependencies = _build_dependencies(
        config, bom_refs, app_bom_ref, sub_chart_keys,
    )

    # --- 5. Создаём metadata ---
    meta = CdxMetadata(
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

    # --- 6. Собираем BOM ---
    bom = CycloneDxBom(
        metadata=meta,
        components=components,
        dependencies=dependencies,
    )
    return bom, warnings


def _find_sub_charts(config: BuildConfig) -> set[tuple[str, MimeType]]:
    """Найти компоненты которые являются sub-chart'ами.

    Если helm chart A имеет в dependsOn helm chart B → B sub-chart.
    """
    sub_charts: set[tuple[str, MimeType]] = set()
    for comp in config.components:
        if comp.mime_type in _HELM_TYPES:
            for dep in comp.depends_on:
                if dep.mime_type in _HELM_TYPES:
                    sub_charts.add((dep.name, dep.mime_type))
    return sub_charts


def _build_component(
    comp_config: ComponentConfig,
    mini_manifests: dict[tuple[str, str], CdxComponent],
    bom_refs: dict[tuple[str, MimeType], str],
    app_version: str,
    config_index: dict[tuple[str, MimeType], ComponentConfig],
    sub_chart_keys: set[tuple[str, MimeType]],
) -> tuple[CdxComponent | None, str | None]:
    """Создать CdxComponent для финального манифеста.

    Returns:
        (component, warning) — компонент (или None если не найден) и строка предупреждения.
    """
    bom_ref = bom_refs[(comp_config.name, comp_config.mime_type)]

    # standalone-runnable — создаём из конфига (нет мини-манифеста)
    if comp_config.mime_type in _STANDALONE_TYPES:
        return _build_standalone_component(comp_config, bom_ref, app_version), None

    # Ищем готовый компонент из мини-манифеста по (name, mime_type)
    source = _find_mini_manifest(comp_config, mini_manifests)

    if not source:
        warning = (
            f"WARNING: component '{comp_config.name}' ({comp_config.mime_type.value}) "
            f"not found in mini-manifests — skipped"
        )
        return None, warning

    if comp_config.mime_type in _HELM_TYPES:
        return _build_helm_component(
            comp_config, source, bom_ref, bom_refs, app_version,
            mini_manifests, config_index, sub_chart_keys,
        ), None

    if comp_config.mime_type in _DOCKER_TYPES:
        return _build_docker_component(source, bom_ref), None

    # Неизвестный тип — просто переназначаем bom-ref
    return source.model_copy(update={"bom_ref": bom_ref}), None


def _find_mini_manifest(
    comp_config: ComponentConfig,
    mini_manifests: dict[tuple[str, str], CdxComponent],
) -> CdxComponent | None:
    """Найти CdxComponent в мини-манифестах по (name, mime_type)."""
    key = (comp_config.name, comp_config.mime_type.value)
    return mini_manifests.get(key)


def _build_docker_component(
    source: CdxComponent,
    bom_ref: str,
) -> CdxComponent:
    """Docker-образ: берём из мини-манифеста, перегенерируем bom-ref."""
    return source.model_copy(update={"bom_ref": bom_ref})


def _build_standalone_component(
    comp_config: ComponentConfig,
    bom_ref: str,
    app_version: str,
) -> CdxComponent:
    """Standalone-runnable: создаём из конфига."""
    return CdxComponent(
        bom_ref=bom_ref,
        type="application",
        mime_type=comp_config.mime_type.value,
        name=comp_config.name,
        version=app_version,
        properties=[],
        components=[],
    )


def _build_helm_component(
    comp_config: ComponentConfig,
    source: CdxComponent,
    bom_ref: str,
    bom_refs: dict[tuple[str, MimeType], str],
    app_version: str,
    mini_manifests: dict[tuple[str, str], CdxComponent],
    config_index: dict[tuple[str, MimeType], ComponentConfig],
    sub_chart_keys: set[tuple[str, MimeType]],
) -> CdxComponent:
    """Helm-чарт: берём из мини-манифеста + добавляем properties.

    Для app-chart (umbrella) — вкладываем sub-chart'ы внутрь components[].
    """
    # Properties: isLibrary + artifactMappings (только docker deps этого chart'а)
    properties: list[CdxProperty] = [
        CdxProperty(name="isLibrary", value=False),
    ]

    artifact_mappings = _build_artifact_mappings(comp_config, bom_refs)
    if artifact_mappings:
        properties.append(CdxProperty(
            name="qubership:helm.values.artifactMappings",
            value=artifact_mappings,
        ))

    # Перегенерируем bom-ref для вложенных компонентов из мини-манифеста
    # (values.schema, resource-profiles)
    nested: list[CdxComponent] = []
    if source.components:
        for c in source.components:
            nested.append(c.model_copy(update={"bom_ref": _make_bom_ref(c.name)}))

    # Если это app-chart (umbrella) — добавляем sub-chart'ы как вложенные
    for dep in comp_config.depends_on:
        dep_key = (dep.name, dep.mime_type)
        if dep_key in sub_chart_keys:
            sub_comp = _build_sub_chart(dep_key, bom_refs, config_index)
            if sub_comp:
                nested.append(sub_comp)

    # Version: из мини-манифеста, fallback на app_version
    version = source.version or app_version

    return source.model_copy(update={
        "bom_ref": bom_ref,
        "version": version,
        "properties": properties,
        "components": nested or [],
    })


def _build_sub_chart(
    key: tuple[str, MimeType],
    bom_refs: dict[tuple[str, MimeType], str],
    config_index: dict[tuple[str, MimeType], ComponentConfig],
) -> CdxComponent | None:
    """Создать вложенный sub-chart компонент."""
    name, mime_type = key
    bom_ref = bom_refs[key]
    comp_config = config_index.get(key)
    if not comp_config:
        return None

    # Properties: isLibrary + artifactMappings
    properties: list[CdxProperty] = [
        CdxProperty(name="isLibrary", value=False),
    ]

    artifact_mappings = _build_artifact_mappings(comp_config, bom_refs)
    if artifact_mappings:
        properties.append(CdxProperty(
            name="qubership:helm.values.artifactMappings",
            value=artifact_mappings,
        ))

    return CdxComponent(
        bom_ref=bom_ref,
        type="application",
        mime_type=mime_type.value,
        name=name,
        properties=properties,
        components=[],
    )


def _build_artifact_mappings(
    comp_config: ComponentConfig,
    bom_refs: dict[tuple[str, MimeType], str],
) -> dict[str, dict[str, str]]:
    """Построить artifactMappings из dependsOn (только не-helm deps)."""
    mappings: dict[str, dict[str, str]] = {}

    for dep in comp_config.depends_on:
        if dep.mime_type not in _HELM_TYPES and dep.values_path_prefix is not None:
            dep_key = (dep.name, dep.mime_type)
            if dep_key in bom_refs:
                dep_bom_ref = bom_refs[dep_key]
                mappings[dep_bom_ref] = {
                    "valuesPathPrefix": dep.values_path_prefix,
                }

    return mappings


def _build_dependencies(
    config: BuildConfig,
    bom_refs: dict[tuple[str, MimeType], str],
    app_bom_ref: str,
    sub_chart_keys: set[tuple[str, MimeType]],
) -> list[CdxDependency]:
    """Построить массив dependencies."""
    dependencies: list[CdxDependency] = []

    # Приложение (metadata) зависит от всех top-level компонентов
    top_level_refs = [
        bom_refs[(c.name, c.mime_type)]
        for c in config.components
        if (c.name, c.mime_type) not in sub_chart_keys
    ]
    dependencies.append(CdxDependency(
        ref=app_bom_ref,
        depends_on=top_level_refs,
    ))

    # Каждый top-level компонент — его dependsOn
    for comp in config.components:
        comp_key = (comp.name, comp.mime_type)
        if comp_key in sub_chart_keys:
            continue  # dependencies для sub-charts — ниже

        comp_ref = bom_refs[comp_key]
        dep_refs = []
        for dep in comp.depends_on:
            dep_key = (dep.name, dep.mime_type)
            if dep_key in bom_refs:
                dep_refs.append(bom_refs[dep_key])

        if dep_refs:
            dependencies.append(CdxDependency(
                ref=comp_ref,
                depends_on=dep_refs,
            ))

    # Sub-chart'ы — их dependsOn (docker images)
    for sub_key in sub_chart_keys:
        comp_config = next(
            (c for c in config.components if (c.name, c.mime_type) == sub_key),
            None,
        )
        if not comp_config:
            continue

        sub_ref = bom_refs[sub_key]
        dep_refs = []
        for dep in comp_config.depends_on:
            dep_key = (dep.name, dep.mime_type)
            if dep_key in bom_refs:
                dep_refs.append(bom_refs[dep_key])

        if dep_refs:
            dependencies.append(CdxDependency(
                ref=sub_ref,
                depends_on=dep_refs,
            ))

    return dependencies
