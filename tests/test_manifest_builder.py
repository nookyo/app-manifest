"""Тесты для сборщика манифеста.

В новой архитектуре generate принимает мини-манифесты (CdxComponent),
а не сырые CI метаданные.
"""

from pathlib import Path

from app_manifest.services.component_builder import build_component_manifest
from app_manifest.services.config_loader import load_build_config
from app_manifest.services.manifest_builder import build_manifest
from app_manifest.services.metadata_loader import load_component_metadata
from app_manifest.services.regdef_loader import load_registry_definition

FIXTURES = Path(__file__).parent / "fixtures"


def _make_mini_manifests(metadata_files, regdef=None):
    """Создать dict мини-манифестов из metadata-файлов."""
    result = {}
    for path in metadata_files:
        meta = load_component_metadata(path)
        bom = build_component_manifest(meta, regdef)
        comp = bom.components[0]
        key = (comp.name, comp.mime_type)
        result[key] = comp
    return result


class TestBuildManifestMinimal:
    """Тесты на минимальном конфиге (jaeger-подобном)."""

    def _build(self):
        config = load_build_config(FIXTURES / "configs/minimal_config.yaml")
        regdef = load_registry_definition(FIXTURES / "regdefs/qubership_regdef.yml")
        mini = _make_mini_manifests([
            FIXTURES / "metadata/docker_metadata.json",
            FIXTURES / "metadata/helm_metadata.json",
            FIXTURES / "metadata/envoy_metadata.json",
        ], regdef)
        bom, _ = build_manifest(config, mini)
        return bom

    def test_bom_root_fields(self):
        """Корневые поля BOM."""
        bom = self._build()
        assert bom.bom_format == "CycloneDX"
        assert bom.spec_version == "1.6"
        assert bom.version == 1
        assert bom.serial_number.startswith("urn:uuid:")

    def test_metadata(self):
        """Metadata секция."""
        bom = self._build()
        assert bom.metadata.component.name == "qubership-jaeger"
        assert bom.metadata.component.version == "1.2.3"
        assert bom.metadata.component.type == "application"
        assert bom.metadata.component.mime_type == "application/vnd.nc.application"
        assert bom.metadata.tools.components[0].name == "am-build-cli"

    def test_components_count(self):
        """Количество компонентов из конфига."""
        bom = self._build()
        # minimal_config.yaml: standalone + helm + 2 docker = 4
        assert len(bom.components) == 4

    def test_standalone_component(self):
        """Standalone-runnable компонент."""
        bom = self._build()
        standalone = bom.components[0]
        assert standalone.type == "application"
        assert standalone.mime_type == "application/vnd.nc.standalone-runnable"
        assert standalone.name == "qubership-jaeger"
        assert standalone.properties == []
        assert standalone.components == []

    def test_docker_component(self):
        """Docker-образ из мини-манифеста."""
        bom = self._build()
        docker_comps = [c for c in bom.components if c.type == "container"]
        jaeger = next(c for c in docker_comps if c.name == "jaeger")

        assert jaeger.type == "container"
        assert jaeger.mime_type == "application/vnd.docker.image"
        assert jaeger.group == "core"
        assert jaeger.version == "build3"
        assert jaeger.purl is not None
        assert "pkg:docker/" in jaeger.purl
        assert jaeger.hashes is not None
        assert len(jaeger.hashes) == 1

    def test_docker_purl_from_mini_manifest(self):
        """PURL берётся из мини-манифеста как есть."""
        bom = self._build()
        docker_comps = [c for c in bom.components if c.type == "container"]
        jaeger = next(c for c in docker_comps if c.name == "jaeger")

        assert "jaeger" in jaeger.purl
        assert "build3" in jaeger.purl

    def test_envoy_from_mini_manifest(self):
        """Envoy из мини-манифеста."""
        bom = self._build()
        docker_comps = [c for c in bom.components if c.type == "container"]
        envoy = next(c for c in docker_comps if c.name == "envoy")

        assert envoy.version == "v1.32.6"
        assert envoy.purl is not None
        assert "envoy" in envoy.purl

    def test_dependencies_app_depends_on_all(self):
        """Приложение зависит от всех компонентов."""
        bom = self._build()
        app_dep = bom.dependencies[0]

        assert app_dep.ref == bom.metadata.component.bom_ref
        assert len(app_dep.depends_on) == 4

    def test_dependencies_standalone_depends_on_helm(self):
        """Standalone зависит от helm (из dependsOn в YAML)."""
        bom = self._build()
        standalone_ref = bom.components[0].bom_ref

        standalone_dep = next(
            (d for d in bom.dependencies if d.ref == standalone_ref), None
        )
        assert standalone_dep is not None
        assert len(standalone_dep.depends_on) == 1

    def test_dependencies_helm_depends_on_docker(self):
        """Helm зависит от Docker-образов (из dependsOn в YAML)."""
        bom = self._build()
        helm_ref = bom.components[1].bom_ref

        helm_dep = next(
            (d for d in bom.dependencies if d.ref == helm_ref), None
        )
        assert helm_dep is not None
        assert len(helm_dep.depends_on) == 2


class TestHelmComponent:
    """Тесты для Helm-чарт компонента."""

    def _build(self):
        config = load_build_config(FIXTURES / "configs/minimal_config.yaml")
        regdef = load_registry_definition(FIXTURES / "regdefs/qubership_regdef.yml")
        mini = _make_mini_manifests([
            FIXTURES / "metadata/docker_metadata.json",
            FIXTURES / "metadata/helm_metadata.json",
            FIXTURES / "metadata/envoy_metadata.json",
        ], regdef)
        bom, _ = build_manifest(config, mini)
        return bom

    def _helm_comp(self):
        bom = self._build()
        return next(
            c for c in bom.components
            if c.mime_type == "application/vnd.nc.helm.chart"
        )

    def test_helm_basic_fields(self):
        """Helm: type, mime-type, name."""
        helm = self._helm_comp()
        assert helm.type == "application"
        assert helm.mime_type == "application/vnd.nc.helm.chart"
        assert helm.name == "qubership-jaeger"

    def test_helm_version(self):
        """Version из мини-манифеста."""
        helm = self._helm_comp()
        assert helm.version == "1.2.3"

    def test_helm_purl(self):
        """PURL из мини-манифеста."""
        helm = self._helm_comp()
        assert helm.purl is not None
        assert "pkg:helm/" in helm.purl
        assert "qubership-jaeger" in helm.purl

    def test_helm_is_library_property(self):
        """isLibrary property добавляется в generate."""
        helm = self._helm_comp()
        is_library = next(
            p for p in helm.properties if p.name == "isLibrary"
        )
        assert is_library.value is False

    def test_helm_artifact_mappings(self):
        """artifactMappings маппинг Docker→valuesPathPrefix."""
        helm = self._helm_comp()
        mappings_prop = next(
            (p for p in helm.properties
             if p.name == "qubership:helm.values.artifactMappings"),
            None,
        )
        assert mappings_prop is not None
        assert len(mappings_prop.value) == 2

        prefixes = {v["valuesPathPrefix"] for v in mappings_prop.value.values()}
        assert "images.jaeger" in prefixes
        assert "images.envoy" in prefixes

    def test_helm_artifact_mappings_keys_are_bom_refs(self):
        """Ключи artifactMappings — bom-ref Docker-компонентов."""
        bom = self._build()
        helm = next(
            c for c in bom.components
            if c.mime_type == "application/vnd.nc.helm.chart"
        )
        mappings_prop = next(
            p for p in helm.properties
            if p.name == "qubership:helm.values.artifactMappings"
        )

        docker_refs = {
            c.bom_ref for c in bom.components if c.type == "container"
        }
        for key in mappings_prop.value:
            assert key in docker_refs, f"Key {key} is not a Docker bom-ref"

    def test_helm_nested_values_schema(self):
        """Вложенный компонент values.schema.json."""
        helm = self._helm_comp()
        assert helm.components is not None
        assert len(helm.components) >= 1

        schema_comp = next(
            (c for c in helm.components if c.name == "values.schema.json"),
            None,
        )
        assert schema_comp is not None
        assert schema_comp.type == "data"
        assert schema_comp.mime_type == "application/vnd.nc.helm.values.schema"
        assert schema_comp.data is not None
        assert len(schema_comp.data) == 1
        assert schema_comp.data[0].name == "values.schema.json"
        assert schema_comp.data[0].contents.attachment.encoding == "base64"

    def test_helm_nested_resource_profiles(self):
        """Вложенный компонент resource-profile-baselines."""
        helm = self._helm_comp()
        profiles_comp = next(
            (c for c in helm.components if c.name == "resource-profile-baselines"),
            None,
        )
        assert profiles_comp is not None
        assert profiles_comp.type == "data"
        assert profiles_comp.mime_type == "application/vnd.nc.resource-profile-baseline"
        assert profiles_comp.data is not None
        assert len(profiles_comp.data) == 2

    def test_helm_hashes(self):
        """Хеши из мини-манифеста."""
        helm = self._helm_comp()
        assert helm.hashes is not None
        assert len(helm.hashes) == 1
        assert helm.hashes[0].alg == "SHA-256"

    def test_helm_without_mini_manifest(self):
        """Helm без мини-манифеста — пропускается."""
        config = load_build_config(FIXTURES / "configs/minimal_config.yaml")
        bom, _ = build_manifest(config, {})
        helm_comps = [
            c for c in bom.components
            if c.mime_type == "application/vnd.nc.helm.chart"
        ]
        assert len(helm_comps) == 0

    def test_missing_mini_manifest_produces_warning(self):
        """Если мини-манифест не найден — возвращается warning."""
        config = load_build_config(FIXTURES / "configs/minimal_config.yaml")
        _, warnings = build_manifest(config, {})
        # В конфиге есть helm-chart — он не найдет мини-манифест
        assert any("not found in mini-manifests" in w for w in warnings)
        assert any("qubership-jaeger" in w for w in warnings)

    def test_helm_serialization(self):
        """Helm-компонент корректно сериализуется в JSON."""
        bom = self._build()
        data = bom.model_dump(by_alias=True, exclude_none=True)
        helm_data = next(
            c for c in data["components"]
            if c["mime-type"] == "application/vnd.nc.helm.chart"
        )
        assert "bom-ref" in helm_data
        assert "properties" in helm_data
        assert "purl" in helm_data
        assert "components" in helm_data
        assert len(helm_data["components"]) == 2

    def test_bom_ref_regenerated(self):
        """bom-ref перегенерируется в generate (вариант Б)."""
        config = load_build_config(FIXTURES / "configs/minimal_config.yaml")
        regdef = load_registry_definition(FIXTURES / "regdefs/qubership_regdef.yml")
        mini = _make_mini_manifests([
            FIXTURES / "metadata/docker_metadata.json",
            FIXTURES / "metadata/helm_metadata.json",
            FIXTURES / "metadata/envoy_metadata.json",
        ], regdef)

        helm_key = ("qubership-jaeger", "application/vnd.nc.helm.chart")
        original_ref = mini[helm_key].bom_ref

        bom, _ = build_manifest(config, mini)
        helm = next(
            c for c in bom.components
            if c.mime_type == "application/vnd.nc.helm.chart"
        )

        assert helm.bom_ref != original_ref
        assert helm.bom_ref.startswith("qubership-jaeger:")


class TestBuildManifestOverrides:
    """Тесты для переопределения version и name."""

    def test_version_override(self):
        config = load_build_config(FIXTURES / "configs/minimal_config.yaml")
        bom, _ = build_manifest(config, {}, version_override="9.9.9")
        assert bom.metadata.component.version == "9.9.9"

    def test_name_override(self):
        config = load_build_config(FIXTURES / "configs/minimal_config.yaml")
        bom, _ = build_manifest(config, {}, name_override="custom-name")
        assert bom.metadata.component.name == "custom-name"

    def test_no_override_uses_config(self):
        config = load_build_config(FIXTURES / "configs/minimal_config.yaml")
        bom, _ = build_manifest(config, {})
        assert bom.metadata.component.name == "qubership-jaeger"
        assert bom.metadata.component.version == "1.2.3"


class TestUmbrellaHelm:
    """Тесты для umbrella (app-chart) паттерна — QIP."""

    def _build(self):
        config = load_build_config(FIXTURES / "configs/qip_config.yaml")
        regdef = load_registry_definition(FIXTURES / "regdefs/qubership_regdef.yml")
        mini = _make_mini_manifests([
            FIXTURES / "metadata/qip_helm_metadata.json",
            FIXTURES / "metadata/qip_engine_metadata.json",
            FIXTURES / "metadata/qip_runtime_catalog_metadata.json",
        ], regdef)
        bom, _ = build_manifest(config, mini)
        return bom

    def test_top_level_components_count(self):
        """Top-level: standalone + app-chart + 2 docker = 4 (sub-charts НЕ на верхнем уровне)."""
        bom = self._build()
        assert len(bom.components) == 4

    def test_sub_charts_not_at_top_level(self):
        """Sub-chart'ы (qip-engine, qip-runtime-catalog) НЕ на верхнем уровне."""
        bom = self._build()
        top_names = [(c.name, c.mime_type) for c in bom.components]
        assert ("qip-engine", "application/vnd.nc.helm.chart") not in top_names
        assert ("qip-runtime-catalog", "application/vnd.nc.helm.chart") not in top_names

    def test_sub_charts_nested_in_app_chart(self):
        """Sub-chart'ы вложены внутрь app-chart."""
        bom = self._build()
        app_chart = next(
            c for c in bom.components
            if c.mime_type == "application/vnd.nc.helm.chart"
        )
        nested_names = [c.name for c in app_chart.components]
        assert "qip-engine" in nested_names
        assert "qip-runtime-catalog" in nested_names

    def test_sub_chart_has_artifact_mapping(self):
        """Каждый sub-chart имеет свой artifactMapping."""
        bom = self._build()
        app_chart = next(
            c for c in bom.components
            if c.mime_type == "application/vnd.nc.helm.chart"
        )
        for sub in app_chart.components:
            if sub.mime_type != "application/vnd.nc.helm.chart":
                continue
            mappings_prop = next(
                (p for p in sub.properties
                 if p.name == "qubership:helm.values.artifactMappings"),
                None,
            )
            assert mappings_prop is not None, f"{sub.name} has no artifactMappings"
            assert len(mappings_prop.value) == 1

    def test_sub_chart_artifact_mapping_keys_are_docker_refs(self):
        """Ключи artifactMappings sub-chart'ов — bom-ref Docker-компонентов."""
        bom = self._build()
        docker_refs = {
            c.bom_ref for c in bom.components if c.type == "container"
        }
        app_chart = next(
            c for c in bom.components
            if c.mime_type == "application/vnd.nc.helm.chart"
        )
        for sub in app_chart.components:
            if sub.mime_type != "application/vnd.nc.helm.chart":
                continue
            mappings_prop = next(
                p for p in sub.properties
                if p.name == "qubership:helm.values.artifactMappings"
            )
            for key in mappings_prop.value:
                assert key in docker_refs

    def test_app_chart_no_artifact_mappings(self):
        """App-chart (umbrella) не имеет artifactMappings (его deps — sub-charts, не docker)."""
        bom = self._build()
        app_chart = next(
            c for c in bom.components
            if c.mime_type == "application/vnd.nc.helm.chart"
        )
        mappings_prop = next(
            (p for p in app_chart.properties
             if p.name == "qubership:helm.values.artifactMappings"),
            None,
        )
        assert mappings_prop is None

    def test_standalone_depends_on_app_chart(self):
        """Standalone зависит от app-chart."""
        bom = self._build()
        standalone = next(
            c for c in bom.components
            if c.mime_type == "application/vnd.nc.standalone-runnable"
        )
        app_chart = next(
            c for c in bom.components
            if c.mime_type == "application/vnd.nc.helm.chart"
        )
        standalone_dep = next(
            d for d in bom.dependencies if d.ref == standalone.bom_ref
        )
        assert app_chart.bom_ref in standalone_dep.depends_on

    def test_sub_chart_depends_on_docker(self):
        """Sub-chart → docker image в dependencies."""
        bom = self._build()
        app_chart = next(
            c for c in bom.components
            if c.mime_type == "application/vnd.nc.helm.chart"
        )
        docker_refs = {
            c.bom_ref for c in bom.components if c.type == "container"
        }
        for sub in app_chart.components:
            if sub.mime_type != "application/vnd.nc.helm.chart":
                continue
            sub_dep = next(
                (d for d in bom.dependencies if d.ref == sub.bom_ref),
                None,
            )
            assert sub_dep is not None, f"No dependency for sub-chart {sub.name}"
            for ref in sub_dep.depends_on:
                assert ref in docker_refs

    def test_metadata_depends_on_top_level_only(self):
        """Metadata (app) зависит только от top-level компонентов."""
        bom = self._build()
        app_dep = bom.dependencies[0]
        assert app_dep.ref == bom.metadata.component.bom_ref
        # standalone + app-chart + 2 docker = 4
        assert len(app_dep.depends_on) == 4

    def test_sub_chart_is_library_false(self):
        """Sub-chart'ы имеют isLibrary=false."""
        bom = self._build()
        app_chart = next(
            c for c in bom.components
            if c.mime_type == "application/vnd.nc.helm.chart"
        )
        for sub in app_chart.components:
            if sub.mime_type != "application/vnd.nc.helm.chart":
                continue
            is_lib = next(p for p in sub.properties if p.name == "isLibrary")
            assert is_lib.value is False

    def test_serialization(self):
        """Umbrella манифест корректно сериализуется."""
        bom = self._build()
        data = bom.model_dump(by_alias=True, exclude_none=True)
        app_chart = next(
            c for c in data["components"]
            if c["mime-type"] == "application/vnd.nc.helm.chart"
        )
        nested_charts = [
            c for c in app_chart["components"]
            if c.get("mime-type") == "application/vnd.nc.helm.chart"
        ]
        assert len(nested_charts) == 2
