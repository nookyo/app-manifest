"""Application Manifest builder.

Takes:
  - BuildConfig (from YAML)
  - CdxComponent (from CycloneDX mini-manifests)

And assembles them into a CycloneDxBom — the final manifest.

Assembly logic:
  1. Build the metadata section (name, version, timestamp, tools)
  2. Identify sub-charts (helm→helm dependsOn) — they are not added at the top level
  3. For each component in the config, find the corresponding CdxComponent from the mini-manifests
  4. Regenerate bom-ref (generate controls all identifiers)
  5. For umbrella app-chart — embed sub-charts inside components[]
  6. Create standalone-runnable from config (it has no mini-manifest)
  7. Build dependencies — links between components
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

# MimeTypes treated as Docker images
_DOCKER_TYPES = {MimeType.DOCKER_IMAGE}

# MimeTypes treated as standalone-runnable
_STANDALONE_TYPES = {MimeType.STANDALONE_RUNNABLE, MimeType.Q_STANDALONE_RUNNABLE}

# MimeTypes treated as Helm charts
_HELM_TYPES = {MimeType.HELM_CHART, MimeType.Q_HELM_CHART}


def build_manifest(
    config: BuildConfig,
    mini_manifests: dict[tuple[str, str], CdxComponent],
    version_override: str | None = None,
    name_override: str | None = None,
) -> tuple[CycloneDxBom, list[str]]:
    """Assemble an Application Manifest from config and mini-manifests.

    Returns:
        (bom, warnings) — the assembled manifest and a list of warnings (empty if all is well).
    """
    app_name = name_override or config.application_name
    app_version = version_override or config.application_version

    # --- 1. Identify sub-charts ---
    sub_chart_keys = _find_sub_charts(config)

    # --- 2. Generate bom-ref for ALL components (including sub-charts) ---
    bom_refs: dict[tuple[str, MimeType], str] = {}
    for comp in config.components:
        bom_refs[(comp.name, comp.mime_type)] = _make_bom_ref(comp.name)

    app_bom_ref = _make_bom_ref(app_name)

    # Config index by (name, mime_type) for fast lookup
    config_index: dict[tuple[str, MimeType], ComponentConfig] = {
        (c.name, c.mime_type): c for c in config.components
    }

    # --- 3. Build top-level components (excluding sub-charts) ---
    components: list[CdxComponent] = []
    warnings: list[str] = []
    for comp_config in config.components:
        key = (comp_config.name, comp_config.mime_type)
        if key in sub_chart_keys:
            continue  # sub-chart — will be nested inside the parent

        cdx_comp, warning = _build_component(
            comp_config, mini_manifests, bom_refs, app_version,
            config_index, sub_chart_keys,
        )
        if warning:
            warnings.append(warning)
        if cdx_comp:
            components.append(cdx_comp)

    # --- 4. Build dependencies ---
    dependencies = _build_dependencies(
        config, bom_refs, app_bom_ref, sub_chart_keys,
    )

    # --- 5. Build metadata ---
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

    # --- 6. Assemble BOM ---
    bom = CycloneDxBom(
        metadata=meta,
        components=components,
        dependencies=dependencies,
    )
    return bom, warnings


def _find_sub_charts(config: BuildConfig) -> set[tuple[str, MimeType]]:
    """Find components that are sub-charts.

    If helm chart A has helm chart B in dependsOn → B is a sub-chart.
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
    """Create a CdxComponent for the final manifest.

    Returns:
        (component, warning) — the component (or None if not found) and a warning string.
    """
    bom_ref = bom_refs[(comp_config.name, comp_config.mime_type)]

    # standalone-runnable — built from config (no mini-manifest)
    if comp_config.mime_type in _STANDALONE_TYPES:
        return _build_standalone_component(comp_config, bom_ref, app_version), None

    # Look up the component from the mini-manifests by (name, mime_type)
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

    # Unknown type — just reassign the bom-ref
    return source.model_copy(update={"bom_ref": bom_ref}), None


def _find_mini_manifest(
    comp_config: ComponentConfig,
    mini_manifests: dict[tuple[str, str], CdxComponent],
) -> CdxComponent | None:
    """Find a CdxComponent in mini-manifests by (name, mime_type)."""
    key = (comp_config.name, comp_config.mime_type.value)
    return mini_manifests.get(key)


def _build_docker_component(
    source: CdxComponent,
    bom_ref: str,
) -> CdxComponent:
    """Docker image: copy from mini-manifest, regenerate bom-ref."""
    return source.model_copy(update={"bom_ref": bom_ref})


def _build_standalone_component(
    comp_config: ComponentConfig,
    bom_ref: str,
    app_version: str,
) -> CdxComponent:
    """Standalone-runnable: build from config."""
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
    """Helm chart: copy from mini-manifest and add properties.

    For umbrella app-chart — embed sub-charts inside components[].
    """
    # Properties: isLibrary + artifactMappings (docker deps of this chart only)
    properties: list[CdxProperty] = [
        CdxProperty(name="isLibrary", value=False),
    ]

    artifact_mappings = _build_artifact_mappings(comp_config, bom_refs)
    if artifact_mappings:
        properties.append(CdxProperty(
            name="qubership:helm.values.artifactMappings",
            value=artifact_mappings,
        ))

    # Regenerate bom-ref for nested components from the mini-manifest
    # (values.schema, resource-profiles)
    nested: list[CdxComponent] = []
    if source.components:
        for c in source.components:
            nested.append(c.model_copy(update={"bom_ref": _make_bom_ref(c.name)}))

    # If this is an umbrella app-chart — embed sub-charts as nested components
    for dep in comp_config.depends_on:
        dep_key = (dep.name, dep.mime_type)
        if dep_key in sub_chart_keys:
            sub_comp = _build_sub_chart(dep_key, bom_refs, config_index)
            if sub_comp:
                nested.append(sub_comp)

    # Version: from mini-manifest, fallback to app_version
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
    """Create a nested sub-chart component."""
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
    """Build artifactMappings from dependsOn (non-helm deps only)."""
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
    """Build the dependencies array."""
    dependencies: list[CdxDependency] = []

    # The application (metadata) depends on all top-level components
    top_level_refs = [
        bom_refs[(c.name, c.mime_type)]
        for c in config.components
        if (c.name, c.mime_type) not in sub_chart_keys
    ]
    dependencies.append(CdxDependency(
        ref=app_bom_ref,
        depends_on=top_level_refs,
    ))

    # Each top-level component — its dependsOn
    for comp in config.components:
        comp_key = (comp.name, comp.mime_type)
        if comp_key in sub_chart_keys:
            continue  # dependencies for sub-charts are handled below

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

    # Sub-charts — their dependsOn (docker images)
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
