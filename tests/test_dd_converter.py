"""Tests for DD ↔ AMv2 conversion."""

import json
from pathlib import Path

import pytest

from app_manifest.models.dd import DeploymentDescriptor, DdService, DdChart
from app_manifest.models.regdef import RegistryDefinition, DockerConfig, HelmAppConfig
from app_manifest.models.cyclonedx import (
    CdxComponent, CdxHash, CdxProperty, CycloneDxBom,
    CdxMetadata, CdxMetadataComponent, CdxTool, CdxToolsWrapper,
)
from app_manifest.services.dd_converter import (
    convert_dd_to_amv2,
    convert_amv2_to_dd,
    _full_chart_name_to_helm_ref,
    _purl_to_docker_artifact_ref,
    _purl_to_helm_artifact_ref,
)
from app_manifest.services.config_loader import load_build_config
from app_manifest.services.regdef_loader import load_registry_definition

FIXTURES = Path(__file__).parent / "fixtures"
DD_FIXTURES = FIXTURES / "dd"


# ─── Fixtures ────────────────────────────────────────────────

@pytest.fixture
def artifactory_regdef() -> RegistryDefinition:
    return load_registry_definition(FIXTURES / "regdefs/artifactory_regdef.yml")


@pytest.fixture
def dd_simple() -> DeploymentDescriptor:
    """DD with one standalone image and one service (image + helm chart) + app-chart."""
    return DeploymentDescriptor(
        services=[
            DdService(
                image_name="my-standalone-image",
                docker_repository_name="cloud-core",
                docker_tag="build1",
                full_image_name="artifactorycn.netcracker.com:17004/cloud-core/my-standalone-image:build1",
                docker_registry="artifactorycn.netcracker.com:17004",
                docker_digest="abc123def456",
                image_type="image",
            ),
            DdService(
                image_name="my-service-image",
                docker_repository_name="cloud-core",
                docker_tag="build2",
                full_image_name="artifactorycn.netcracker.com:17004/cloud-core/my-service-image:build2",
                docker_registry="artifactorycn.netcracker.com:17004",
                docker_digest="def456abc123",
                image_type="service",
                service_name="my-service",
                version="1.0.0",
            ),
        ],
        charts=[
            DdChart(
                helm_chart_name="my-app",
                helm_chart_version="1.0.0",
                full_chart_name="https://artifactorycn.netcracker.com/nc.helm.charts/my-app-1.0.0.tgz",
                helm_registry="https://artifactorycn.netcracker.com/nc.helm.charts",
                type="app-chart",
            )
        ],
    )


@pytest.fixture
def config_simple():
    """Build config matching dd_simple."""
    return load_build_config(FIXTURES / "configs/cloud_integration_platform_config.yaml")


@pytest.fixture
def dd_from_file() -> DeploymentDescriptor:
    raw = json.loads((DD_FIXTURES / "cloud_integration_platform_dd.json").read_text())
    return DeploymentDescriptor.model_validate(raw)


# ─── _full_chart_name_to_helm_ref ───────────────────────────

class TestFullChartNameToHelmRef:

    def test_simple_version(self):
        result = _full_chart_name_to_helm_ref(
            "https://registry.example.com/charts/my-chart-1.0.0.tgz"
        )
        assert result == "https://registry.example.com/charts/my-chart:1.0.0"

    def test_complex_version(self):
        """Version with multiple segments (e.g. 0.0.0-release-2025.4-...)."""
        result = _full_chart_name_to_helm_ref(
            "https://artifactorycn.netcracker.com/nc.helm.charts/"
            "cloud-integration-platform-0.0.0-release-2025.4-20251120.144057-26.tgz"
        )
        assert result == (
            "https://artifactorycn.netcracker.com/nc.helm.charts/"
            "cloud-integration-platform:0.0.0-release-2025.4-20251120.144057-26"
        )

    def test_no_slash_raises(self):
        with pytest.raises(ValueError):
            _full_chart_name_to_helm_ref("my-chart-1.0.0.tgz")


# ─── PURL → Artifact Reference ──────────────────────────────

class TestPurlToDockerArtifactRef:

    def test_basic(self, artifactory_regdef):
        full, registry = _purl_to_docker_artifact_ref(
            "pkg:docker/cloud-core/my-image@build2?registry_name=artifactory-netcracker",
            artifactory_regdef,
        )
        assert full == "artifactorycn.netcracker.com:17004/cloud-core/my-image:build2"
        assert registry == "artifactorycn.netcracker.com:17004"

    def test_unknown_registry_name_fallback(self, artifactory_regdef):
        """Unknown registry_name → use registry_name as-is in URI."""
        full, registry = _purl_to_docker_artifact_ref(
            "pkg:docker/ns/img@v1?registry_name=unknown-registry",
            artifactory_regdef,
        )
        assert full == "unknown-registry/ns/img:v1"

    def test_invalid_purl_raises(self, artifactory_regdef):
        with pytest.raises(ValueError):
            _purl_to_docker_artifact_ref("pkg:helm/chart@1.0.0", artifactory_regdef)

    def test_missing_version_raises(self, artifactory_regdef):
        with pytest.raises(ValueError):
            _purl_to_docker_artifact_ref(
                "pkg:docker/ns/img?registry_name=x", artifactory_regdef
            )


class TestPurlToHelmArtifactRef:

    def test_basic_with_repo_name(self, artifactory_regdef):
        full, registry = _purl_to_helm_artifact_ref(
            "pkg:helm/my-chart@1.0.0?registry_name=artifactory-netcracker",
            artifactory_regdef,
        )
        assert full == "https://artifactorycn.netcracker.com/nc.helm.charts/my-chart-1.0.0.tgz"
        assert "nc.helm.charts" in registry

    def test_complex_version(self, artifactory_regdef):
        full, _ = _purl_to_helm_artifact_ref(
            "pkg:helm/cloud-integration-platform@0.0.0-release-2025.4-20251120.144057-26"
            "?registry_name=artifactory-netcracker",
            artifactory_regdef,
        )
        assert full.endswith(
            "cloud-integration-platform-0.0.0-release-2025.4-20251120.144057-26.tgz"
        )

    def test_invalid_purl_raises(self, artifactory_regdef):
        with pytest.raises(ValueError):
            _purl_to_helm_artifact_ref("pkg:docker/img@1.0", artifactory_regdef)


# ─── DD → AMv2 ───────────────────────────────────────────────

class TestConvertDdToAmv2:

    def _convert(self, dd, regdef, config=None, app_name="my-app", app_version="1.0.0"):
        if config is None:
            config = load_build_config(
                FIXTURES / "configs/cloud_integration_platform_config.yaml"
            )
        bom, warnings = convert_dd_to_amv2(
            dd=dd,
            config=config,
            regdef=regdef,
            app_name=app_name,
            app_version=app_version,
        )
        return bom, warnings

    def test_bom_root_fields(self, dd_from_file, artifactory_regdef):
        bom, _ = self._convert(dd_from_file, artifactory_regdef)
        assert bom.bom_format == "CycloneDX"
        assert bom.spec_version == "1.6"
        assert bom.version == 1
        assert bom.serial_number.startswith("urn:uuid:")

    def test_metadata(self, dd_from_file, artifactory_regdef):
        bom, _ = self._convert(
            dd_from_file, artifactory_regdef,
            app_name="cloud-integration-platform",
            app_version="0.0.0-release-2025.4",
        )
        assert bom.metadata.component.name == "cloud-integration-platform"
        assert bom.metadata.component.version == "0.0.0-release-2025.4"
        assert bom.metadata.component.mime_type == "application/vnd.nc.application"

    def test_docker_components_created(self, dd_from_file, artifactory_regdef):
        bom, _ = self._convert(dd_from_file, artifactory_regdef)
        docker = [c for c in bom.components if c.mime_type == "application/vnd.docker.image"]
        # 1 image + 2 services = 3 docker components
        assert len(docker) == 3

    def test_docker_hash(self, dd_from_file, artifactory_regdef):
        bom, _ = self._convert(dd_from_file, artifactory_regdef)
        docker = [c for c in bom.components if c.mime_type == "application/vnd.docker.image"]
        for comp in docker:
            assert comp.hashes is not None
            assert len(comp.hashes) == 1
            assert comp.hashes[0].alg == "SHA-256"

    def test_docker_purl_generated(self, dd_from_file, artifactory_regdef):
        bom, _ = self._convert(dd_from_file, artifactory_regdef)
        docker = [c for c in bom.components if c.mime_type == "application/vnd.docker.image"]
        for comp in docker:
            assert comp.purl is not None
            assert comp.purl.startswith("pkg:docker/")
            assert "registry_name=" in comp.purl

    def test_standalone_component_created(self, dd_from_file, artifactory_regdef):
        bom, _ = self._convert(dd_from_file, artifactory_regdef)
        standalone = [
            c for c in bom.components
            if c.mime_type == "application/vnd.nc.standalone-runnable"
        ]
        assert len(standalone) == 1
        assert standalone[0].type == "application"

    def test_app_chart_created(self, dd_from_file, artifactory_regdef):
        bom, _ = self._convert(dd_from_file, artifactory_regdef)
        helm = [c for c in bom.components if c.mime_type == "application/vnd.nc.helm.chart"]
        assert len(helm) == 1
        assert helm[0].name == "cloud-integration-platform"
        assert helm[0].purl is not None
        assert helm[0].purl.startswith("pkg:helm/")

    def test_service_charts_nested_in_app_chart(self, dd_from_file, artifactory_regdef):
        bom, _ = self._convert(dd_from_file, artifactory_regdef)
        app_chart = next(
            c for c in bom.components if c.mime_type == "application/vnd.nc.helm.chart"
        )
        # 2 service charts should be nested
        nested_helm = [
            c for c in (app_chart.components or [])
            if c.mime_type == "application/vnd.nc.helm.chart"
        ]
        assert len(nested_helm) == 2

    def test_is_library_property_on_service_charts(self, dd_from_file, artifactory_regdef):
        bom, _ = self._convert(dd_from_file, artifactory_regdef)
        app_chart = next(
            c for c in bom.components if c.mime_type == "application/vnd.nc.helm.chart"
        )
        for nested in (app_chart.components or []):
            if nested.mime_type == "application/vnd.nc.helm.chart":
                prop_names = [p.name for p in (nested.properties or [])]
                assert "isLibrary" in prop_names

    def test_dependencies_present(self, dd_from_file, artifactory_regdef):
        bom, _ = self._convert(dd_from_file, artifactory_regdef)
        assert len(bom.dependencies) > 0

    def test_no_warnings_for_valid_input(self, dd_from_file, artifactory_regdef):
        _, warnings = self._convert(dd_from_file, artifactory_regdef)
        assert warnings == []

    def test_dd_without_charts(self, artifactory_regdef):
        """DD with no charts → service charts go to top level."""
        dd = DeploymentDescriptor(
            services=[
                DdService(
                    image_name="my-service-image",
                    docker_repository_name="cloud-core",
                    docker_tag="v1",
                    full_image_name="artifactorycn.netcracker.com:17004/cloud-core/my-service-image:v1",
                    image_type="service",
                    service_name="my-service",
                    version="1.0.0",
                )
            ],
            charts=[],
        )
        config = load_build_config(
            FIXTURES / "configs/cloud_integration_platform_config.yaml"
        )
        bom, _ = convert_dd_to_amv2(
            dd=dd, config=config, regdef=artifactory_regdef,
            app_name="my-app", app_version="1.0.0",
        )
        helm_top = [c for c in bom.components if c.mime_type == "application/vnd.nc.helm.chart"]
        # service chart at top level when no app-chart
        assert len(helm_top) == 1
        assert helm_top[0].name == "my-service"


# ─── AMv2 → DD ───────────────────────────────────────────────

def _make_simple_bom(
    app_chart_name="my-app",
    app_chart_version="1.0.0",
    app_chart_purl="pkg:helm/my-app@1.0.0?registry_name=artifactory-netcracker",
    docker_name="my-service-image",
    docker_purl="pkg:docker/cloud-core/my-service-image@build2?registry_name=artifactory-netcracker",
    service_chart_name="my-service",
    service_chart_version="1.0.0",
) -> CycloneDxBom:
    """Build a minimal CycloneDxBom for testing AMv2→DD."""
    docker_bom_ref = f"docker-{docker_name}"
    service_chart_bom_ref = f"helm-{service_chart_name}"
    app_chart_bom_ref = f"helm-{app_chart_name}"

    docker_comp = CdxComponent(
        bom_ref=docker_bom_ref,
        type="container",
        mime_type="application/vnd.docker.image",
        name=docker_name,
        group="cloud-core",
        version="build2",
        purl=docker_purl,
        hashes=[CdxHash(alg="SHA-256", content="abc123")],
    )
    service_chart_comp = CdxComponent(
        bom_ref=service_chart_bom_ref,
        type="application",
        mime_type="application/vnd.nc.helm.chart",
        name=service_chart_name,
        version=service_chart_version,
        properties=[
            CdxProperty(name="isLibrary", value=False),
            CdxProperty(
                name="qubership:helm.values.artifactMappings",
                value={docker_bom_ref: {"valuesPathPrefix": "image"}},
            ),
        ],
        components=[],
    )
    app_chart_comp = CdxComponent(
        bom_ref=app_chart_bom_ref,
        type="application",
        mime_type="application/vnd.nc.helm.chart",
        name=app_chart_name,
        version=app_chart_version,
        purl=app_chart_purl,
        hashes=[],
        properties=[CdxProperty(name="isLibrary", value=False)],
        components=[service_chart_comp],
    )

    import uuid
    from datetime import datetime, timezone
    return CycloneDxBom(
        serial_number=f"urn:uuid:{uuid.uuid4()}",
        metadata=CdxMetadata(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            component=CdxMetadataComponent(
                bom_ref="app-ref",
                name=app_chart_name,
                version=app_chart_version,
            ),
            tools=CdxToolsWrapper(components=[CdxTool(name="am-build-cli", version="0.1.0")]),
        ),
        components=[docker_comp, app_chart_comp],
        dependencies=[],
    )


class TestConvertAmv2ToDd:

    def test_services_extracted(self, artifactory_regdef):
        bom = _make_simple_bom()
        dd, warnings = convert_amv2_to_dd(bom, artifactory_regdef)
        assert len(dd.services) == 1

    def test_service_image_type(self, artifactory_regdef):
        bom = _make_simple_bom()
        dd, _ = convert_amv2_to_dd(bom, artifactory_regdef)
        service = dd.services[0]
        assert service.image_type == "service"
        assert service.service_name == "my-service"
        assert service.version == "1.0.0"

    def test_full_image_name_reconstructed(self, artifactory_regdef):
        bom = _make_simple_bom()
        dd, warnings = convert_amv2_to_dd(bom, artifactory_regdef)
        assert dd.services[0].full_image_name == (
            "artifactorycn.netcracker.com:17004/cloud-core/my-service-image:build2"
        )

    def test_docker_digest_preserved(self, artifactory_regdef):
        bom = _make_simple_bom()
        dd, _ = convert_amv2_to_dd(bom, artifactory_regdef)
        assert dd.services[0].docker_digest == "abc123"

    def test_chart_extracted(self, artifactory_regdef):
        bom = _make_simple_bom()
        dd, _ = convert_amv2_to_dd(bom, artifactory_regdef)
        assert len(dd.charts) == 1
        assert dd.charts[0].helm_chart_name == "my-app"
        assert dd.charts[0].helm_chart_version == "1.0.0"

    def test_full_chart_name_reconstructed(self, artifactory_regdef):
        bom = _make_simple_bom()
        dd, warnings = convert_amv2_to_dd(bom, artifactory_regdef)
        assert dd.charts[0].full_chart_name.endswith("my-app-1.0.0.tgz")

    def test_dd_sections_present(self, artifactory_regdef):
        """All standard DD sections are present in output."""
        bom = _make_simple_bom()
        dd, _ = convert_amv2_to_dd(bom, artifactory_regdef)
        dd_dict = dd.model_dump(by_alias=True)
        for section in ["services", "charts", "metadata", "include", "infrastructures",
                        "configurations", "frontends", "smartplug", "jobs",
                        "libraries", "complexes", "additionalArtifacts", "descriptors"]:
            assert section in dd_dict

    def test_standalone_not_in_services(self, artifactory_regdef):
        """standalone-runnable components must NOT appear in DD services."""
        from app_manifest.models.cyclonedx import _make_bom_ref
        import uuid
        from datetime import datetime, timezone

        standalone = CdxComponent(
            bom_ref=_make_bom_ref("my-app"),
            type="application",
            mime_type="application/vnd.nc.standalone-runnable",
            name="my-app",
            version="1.0.0",
        )
        bom = _make_simple_bom()
        bom.components.insert(0, standalone)

        dd, _ = convert_amv2_to_dd(bom, artifactory_regdef)
        service_names = [s.image_name for s in dd.services]
        assert "my-app" not in service_names

    def test_no_warnings_for_valid_bom(self, artifactory_regdef):
        bom = _make_simple_bom()
        _, warnings = convert_amv2_to_dd(bom, artifactory_regdef)
        assert warnings == []

    def test_bom_without_app_chart(self, artifactory_regdef):
        """AMv2 with no app-chart → DD with empty charts[]."""
        import uuid
        from datetime import datetime, timezone

        docker_comp = CdxComponent(
            bom_ref="docker-img",
            type="container",
            mime_type="application/vnd.docker.image",
            name="standalone-img",
            group="cloud-core",
            version="v1",
            purl="pkg:docker/cloud-core/standalone-img@v1?registry_name=artifactory-netcracker",
        )
        bom = CycloneDxBom(
            serial_number=f"urn:uuid:{uuid.uuid4()}",
            metadata=CdxMetadata(
                timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                component=CdxMetadataComponent(
                    bom_ref="app-ref", name="my-app", version="1.0.0"
                ),
                tools=CdxToolsWrapper(
                    components=[CdxTool(name="am-build-cli", version="0.1.0")]
                ),
            ),
            components=[docker_comp],
            dependencies=[],
        )
        dd, _ = convert_amv2_to_dd(bom, artifactory_regdef)
        assert dd.charts == []
        assert len(dd.services) == 1
        assert dd.services[0].image_type == "image"


# ─── Round-trip ──────────────────────────────────────────────

class TestRoundTrip:
    """DD → AMv2 → DD should preserve key fields."""

    def test_dd_to_amv2_to_dd_service_count(self, dd_from_file, artifactory_regdef):
        config = load_build_config(
            FIXTURES / "configs/cloud_integration_platform_config.yaml"
        )
        bom, _ = convert_dd_to_amv2(
            dd=dd_from_file,
            config=config,
            regdef=artifactory_regdef,
            app_name="cloud-integration-platform",
            app_version="0.0.0-release-2025.4-20251120.144057-26",
        )
        dd_back, _ = convert_amv2_to_dd(bom=bom, regdef=artifactory_regdef)
        assert len(dd_back.services) == len(dd_from_file.services)

    def test_dd_to_amv2_to_dd_chart_count(self, dd_from_file, artifactory_regdef):
        config = load_build_config(
            FIXTURES / "configs/cloud_integration_platform_config.yaml"
        )
        bom, _ = convert_dd_to_amv2(
            dd=dd_from_file,
            config=config,
            regdef=artifactory_regdef,
            app_name="cloud-integration-platform",
            app_version="0.0.0-release-2025.4-20251120.144057-26",
        )
        dd_back, _ = convert_amv2_to_dd(bom=bom, regdef=artifactory_regdef)
        assert len(dd_back.charts) == len(dd_from_file.charts)

    def test_dd_to_amv2_to_dd_full_image_names(self, dd_from_file, artifactory_regdef):
        config = load_build_config(
            FIXTURES / "configs/cloud_integration_platform_config.yaml"
        )
        bom, _ = convert_dd_to_amv2(
            dd=dd_from_file,
            config=config,
            regdef=artifactory_regdef,
            app_name="cloud-integration-platform",
            app_version="0.0.0-release-2025.4-20251120.144057-26",
        )
        dd_back, _ = convert_amv2_to_dd(bom=bom, regdef=artifactory_regdef)

        original_images = {s.full_image_name for s in dd_from_file.services}
        roundtrip_images = {s.full_image_name for s in dd_back.services}
        assert original_images == roundtrip_images
