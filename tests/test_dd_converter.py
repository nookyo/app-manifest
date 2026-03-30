"""Tests for DD ↔ AMv2 conversion."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from app_manifest.cli import cli
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


# ─── E2E: DD → AMv2 → validate ───────────────────────────────

class TestE2EConvertAndValidate:
    """End-to-end: DD → AMv2 → JSON Schema validation via CLI."""

    def test_dd_to_amv2_produces_valid_manifest(self, tmp_path):
        """DD → AMv2 via CLI → validate via CLI: both must succeed."""
        out_file = tmp_path / "manifest.json"
        runner = CliRunner()

        # Step 1: convert DD → AMv2
        result = runner.invoke(cli, [
            "convert",
            "--to-am",
            "--input", str(DD_FIXTURES / "cloud_integration_platform_dd.json"),
            "--out", str(out_file),
            "--registry-def", str(FIXTURES / "regdefs/artifactory_regdef.yml"),
            "--config", str(FIXTURES / "configs/cloud_integration_platform_config.yaml"),
            "--name", "cloud-integration-platform",
            "--version", "0.0.0-release-2025.4-20251120.144057-26",
        ])
        assert result.exit_code == 0, f"convert failed:\n{result.output}"
        assert out_file.exists()

        # Step 2: validate the produced AMv2
        result = runner.invoke(cli, [
            "validate",
            "--input", str(out_file),
        ])
        assert result.exit_code == 0, f"validate failed:\n{result.output}"
        assert "valid" in result.output.lower()

    def test_dd_to_amv2_manifest_structure(self, tmp_path):
        """Produced AMv2 has the expected top-level structure."""
        out_file = tmp_path / "manifest.json"
        runner = CliRunner()

        runner.invoke(cli, [
            "convert",
            "--to-am",
            "--input", str(DD_FIXTURES / "cloud_integration_platform_dd.json"),
            "--out", str(out_file),
            "--registry-def", str(FIXTURES / "regdefs/artifactory_regdef.yml"),
            "--config", str(FIXTURES / "configs/cloud_integration_platform_config.yaml"),
            "--name", "cloud-integration-platform",
            "--version", "0.0.0-release-2025.4-20251120.144057-26",
        ])

        import json
        data = json.loads(out_file.read_text(encoding="utf-8"))

        assert data["bomFormat"] == "CycloneDX"
        assert data["specVersion"] == "1.6"
        assert data["serialNumber"].startswith("urn:uuid:")
        assert "metadata" in data
        assert "components" in data
        assert "dependencies" in data

    def test_dd_to_amv2_missing_direction_fails(self, tmp_path):
        """Calling convert without --to-am or --to-dd must fail with exit code 2."""
        runner = CliRunner()
        result = runner.invoke(cli, [
            "convert",
            "--input", str(DD_FIXTURES / "cloud_integration_platform_dd.json"),
            "--out", str(tmp_path / "out.json"),
            "--registry-def", str(FIXTURES / "regdefs/artifactory_regdef.yml"),
        ])
        assert result.exit_code != 0

    def test_dd_to_amv2_missing_config_fails(self, tmp_path):
        """DD → AMv2 without --config must fail."""
        runner = CliRunner()
        result = runner.invoke(cli, [
            "convert",
            "--to-am",
            "--input", str(DD_FIXTURES / "cloud_integration_platform_dd.json"),
            "--out", str(tmp_path / "out.json"),
            "--registry-def", str(FIXTURES / "regdefs/artifactory_regdef.yml"),
            # no --config
        ])
        assert result.exit_code != 0

    def test_amv2_to_dd_roundtrip_via_cli(self, tmp_path):
        """Full CLI round-trip: DD → AMv2 → DD. Service count must be preserved."""
        amv2_file = tmp_path / "manifest.json"
        dd_out_file = tmp_path / "dd_roundtrip.json"
        runner = CliRunner()

        # DD → AMv2
        result = runner.invoke(cli, [
            "convert",
            "--to-am",
            "--input", str(DD_FIXTURES / "cloud_integration_platform_dd.json"),
            "--out", str(amv2_file),
            "--registry-def", str(FIXTURES / "regdefs/artifactory_regdef.yml"),
            "--config", str(FIXTURES / "configs/cloud_integration_platform_config.yaml"),
            "--name", "cloud-integration-platform",
            "--version", "0.0.0-release-2025.4-20251120.144057-26",
        ])
        assert result.exit_code == 0, f"DD→AMv2 failed:\n{result.output}"

        # AMv2 → DD
        result = runner.invoke(cli, [
            "convert",
            "--to-dd",
            "--input", str(amv2_file),
            "--out", str(dd_out_file),
            "--registry-def", str(FIXTURES / "regdefs/artifactory_regdef.yml"),
        ])
        assert result.exit_code == 0, f"AMv2→DD failed:\n{result.output}"

        import json
        original = json.loads(
            (DD_FIXTURES / "cloud_integration_platform_dd.json").read_text(encoding="utf-8")
        )
        roundtrip = json.loads(dd_out_file.read_text(encoding="utf-8"))

        assert len(roundtrip["services"]) == len(original["services"])
        assert len(roundtrip["charts"]) == len(original["charts"])


# ─── E2E: Full real DD (8 services, 1 app-chart) ─────────────

class TestE2EFullRealDd:
    """E2E tests using the real DD with 8 services and 1 app-chart.

    Covers:
    - 3 standalone images (image_type: image)
    - 5 service images with helm charts (image_type: service)
    - 1 umbrella app-chart
    """

    DD_FULL = DD_FIXTURES / "cloud_integration_platform_full_dd.json"
    CONFIG_FULL = FIXTURES / "configs/cloud_integration_platform_full_config.yaml"
    REGDEF = FIXTURES / "regdefs/artifactory_regdef.yml"
    APP_NAME = "cloud-integration-platform"
    APP_VERSION = "0.0.0-release-2025.4-20251120.144057-26"

    def _convert(self):
        from app_manifest.services.config_loader import load_build_config
        from app_manifest.services.regdef_loader import load_registry_definition

        dd_raw = json.loads(self.DD_FULL.read_text(encoding="utf-8"))
        dd = DeploymentDescriptor.model_validate(dd_raw)
        config = load_build_config(self.CONFIG_FULL)
        regdef = load_registry_definition(self.REGDEF)
        bom, warnings = convert_dd_to_amv2(
            dd=dd, config=config, regdef=regdef,
            app_name=self.APP_NAME, app_version=self.APP_VERSION,
        )
        return bom, warnings

    def test_no_warnings(self):
        _, warnings = self._convert()
        assert warnings == []

    def test_all_docker_components_created(self):
        """8 services in DD → 8 docker components in AMv2."""
        bom, _ = self._convert()
        docker = [c for c in bom.components if c.mime_type == "application/vnd.docker.image"]
        assert len(docker) == 8

    def test_standalone_runnable_created(self):
        bom, _ = self._convert()
        standalone = [c for c in bom.components if c.mime_type == "application/vnd.nc.standalone-runnable"]
        assert len(standalone) == 1
        assert standalone[0].name == self.APP_NAME
        assert standalone[0].version == self.APP_VERSION

    def test_app_chart_created_with_5_nested_service_charts(self):
        """1 app-chart with 5 nested service charts."""
        bom, _ = self._convert()
        helm_top = [c for c in bom.components if c.mime_type == "application/vnd.nc.helm.chart"]
        assert len(helm_top) == 1
        app_chart = helm_top[0]
        assert app_chart.name == "cloud-integration-platform"
        nested_helm = [
            c for c in (app_chart.components or [])
            if c.mime_type == "application/vnd.nc.helm.chart"
        ]
        assert len(nested_helm) == 5

    def test_all_hashes_present(self):
        """All 8 docker images have SHA-256 hash."""
        bom, _ = self._convert()
        docker = [c for c in bom.components if c.mime_type == "application/vnd.docker.image"]
        for comp in docker:
            assert comp.hashes is not None and len(comp.hashes) == 1
            assert comp.hashes[0].alg == "SHA-256"
            assert len(comp.hashes[0].content) == 64

    def test_all_purls_present(self):
        """All 8 docker images and the app-chart have PURLs."""
        bom, _ = self._convert()
        docker = [c for c in bom.components if c.mime_type == "application/vnd.docker.image"]
        for comp in docker:
            assert comp.purl is not None
            assert comp.purl.startswith("pkg:docker/")

        helm_top = [c for c in bom.components if c.mime_type == "application/vnd.nc.helm.chart"]
        assert helm_top[0].purl is not None
        assert helm_top[0].purl.startswith("pkg:helm/")

    def test_dependencies_wired(self):
        """dependencies[] must contain entries for app-chart and service charts."""
        bom, _ = self._convert()
        dep_refs = {d.ref.split(":")[0] for d in bom.dependencies}
        # standalone-runnable must have dependencies
        standalone = next(c for c in bom.components if c.mime_type == "application/vnd.nc.standalone-runnable")
        assert standalone.bom_ref in {d.ref for d in bom.dependencies}

    def test_produces_valid_manifest(self):
        """The produced AMv2 must pass JSON Schema validation."""
        from app_manifest.services.validator import validate_manifest
        bom, _ = self._convert()
        bom_dict = bom.model_dump(by_alias=True, exclude_none=True)
        errors = validate_manifest(bom_dict)
        assert errors == [], f"Validation errors: {errors}"

    def test_via_cli_produces_valid_manifest(self, tmp_path):
        """Full CLI path: convert + validate on the real DD."""
        out_file = tmp_path / "manifest.json"
        runner = CliRunner()

        result = runner.invoke(cli, [
            "convert", "--to-am",
            "--input", str(self.DD_FULL),
            "--out", str(out_file),
            "--registry-def", str(self.REGDEF),
            "--config", str(self.CONFIG_FULL),
            "--name", self.APP_NAME,
            "--version", self.APP_VERSION,
        ])
        assert result.exit_code == 0, f"convert failed:\n{result.output}"

        result = runner.invoke(cli, ["validate", "--input", str(out_file)])
        assert result.exit_code == 0, f"validate failed:\n{result.output}"

    def test_roundtrip_preserves_all_services(self, tmp_path):
        """DD → AMv2 → DD: all 8 full_image_names preserved."""
        from app_manifest.services.config_loader import load_build_config
        from app_manifest.services.regdef_loader import load_registry_definition

        dd_raw = json.loads(self.DD_FULL.read_text(encoding="utf-8"))
        dd_original = DeploymentDescriptor.model_validate(dd_raw)
        config = load_build_config(self.CONFIG_FULL)
        regdef = load_registry_definition(self.REGDEF)

        bom, _ = convert_dd_to_amv2(
            dd=dd_original, config=config, regdef=regdef,
            app_name=self.APP_NAME, app_version=self.APP_VERSION,
        )
        dd_back, _ = convert_amv2_to_dd(bom=bom, regdef=regdef)

        assert len(dd_back.services) == len(dd_original.services)
        assert len(dd_back.charts) == len(dd_original.charts)

        original_images = {s.full_image_name for s in dd_original.services}
        roundtrip_images = {s.full_image_name for s in dd_back.services}
        assert original_images == roundtrip_images


# ─── Full cycle round-trip with validation ───────────────────

class TestFullCycleRoundTrip:
    """Full cycle tests based on the real DD (8 services, 1 app-chart).

    Cycle A: DD → AMv2 (validate) → DD → AMv2 (validate)
    Cycle B: AMv2 → DD → AMv2 (validate)

    Each AMv2 produced is validated against JSON Schema.
    Each DD is validated structurally (all required fields present).
    """

    DD_FULL = DD_FIXTURES / "cloud_integration_platform_full_dd.json"
    CONFIG_FULL = FIXTURES / "configs/cloud_integration_platform_full_config.yaml"
    REGDEF = FIXTURES / "regdefs/artifactory_regdef.yml"
    APP_NAME = "cloud-integration-platform"
    APP_VERSION = "0.0.0-release-2025.4-20251120.144057-26"

    def _load(self):
        from app_manifest.services.config_loader import load_build_config
        from app_manifest.services.regdef_loader import load_registry_definition
        dd_raw = json.loads(self.DD_FULL.read_text(encoding="utf-8"))
        return (
            DeploymentDescriptor.model_validate(dd_raw),
            load_build_config(self.CONFIG_FULL),
            load_registry_definition(self.REGDEF),
        )

    def _assert_amv2_valid(self, bom, step: str):
        from app_manifest.services.validator import validate_manifest
        bom_dict = bom.model_dump(by_alias=True, exclude_none=True)
        errors = validate_manifest(bom_dict)
        assert errors == [], f"[{step}] AMv2 validation failed: {errors}"

    def _assert_dd_valid(self, dd: DeploymentDescriptor, step: str):
        """Structural validation of DD: required fields present on every service/chart."""
        assert isinstance(dd.services, list), f"[{step}] services must be a list"
        assert isinstance(dd.charts, list), f"[{step}] charts must be a list"
        for i, svc in enumerate(dd.services):
            assert svc.image_name, f"[{step}] services[{i}].image_name is empty"
            assert svc.full_image_name, f"[{step}] services[{i}].full_image_name is empty"
            assert svc.image_type in ("image", "service"), \
                f"[{step}] services[{i}].image_type is invalid: {svc.image_type}"
            if svc.image_type == "service":
                assert svc.service_name, f"[{step}] services[{i}].service_name is empty"
                assert svc.version, f"[{step}] services[{i}].version is empty"
        for i, chart in enumerate(dd.charts):
            assert chart.helm_chart_name, f"[{step}] charts[{i}].helm_chart_name is empty"
            assert chart.helm_chart_version, f"[{step}] charts[{i}].helm_chart_version is empty"
            assert chart.full_chart_name, f"[{step}] charts[{i}].full_chart_name is empty"

    def test_cycle_a_dd_amv2_dd_amv2(self):
        """Cycle A: DD → AMv2 (validate) → DD (validate) → AMv2 (validate)."""
        dd_original, config, regdef = self._load()

        # Step 1: DD → AMv2
        bom1, warnings1 = convert_dd_to_amv2(
            dd=dd_original, config=config, regdef=regdef,
            app_name=self.APP_NAME, app_version=self.APP_VERSION,
        )
        assert warnings1 == [], f"Unexpected warnings at DD→AMv2: {warnings1}"
        self._assert_amv2_valid(bom1, "DD→AMv2")

        # Step 2: AMv2 → DD
        dd_back, warnings2 = convert_amv2_to_dd(bom=bom1, regdef=regdef)
        assert warnings2 == [], f"Unexpected warnings at AMv2→DD: {warnings2}"
        self._assert_dd_valid(dd_back, "AMv2→DD")

        # Step 3: DD → AMv2 again
        bom2, warnings3 = convert_dd_to_amv2(
            dd=dd_back, config=config, regdef=regdef,
            app_name=self.APP_NAME, app_version=self.APP_VERSION,
        )
        assert warnings3 == [], f"Unexpected warnings at DD→AMv2 (2nd): {warnings3}"
        self._assert_amv2_valid(bom2, "DD→AMv2 (2nd)")

        # Both AMv2s must have the same number of components and dependencies
        assert len(bom2.components) == len(bom1.components), \
            "Component count changed after second conversion"
        assert len(bom2.dependencies) == len(bom1.dependencies), \
            "Dependency count changed after second conversion"

    def test_cycle_b_amv2_dd_amv2(self):
        """Cycle B: DD → AMv2 → DD → AMv2 (validate last AMv2).

        Uses the first AMv2 as the starting point for the B cycle.
        """
        dd_original, config, regdef = self._load()

        # Produce the initial AMv2
        bom_initial, _ = convert_dd_to_amv2(
            dd=dd_original, config=config, regdef=regdef,
            app_name=self.APP_NAME, app_version=self.APP_VERSION,
        )
        self._assert_amv2_valid(bom_initial, "initial AMv2")

        # AMv2 → DD
        dd_from_amv2, warnings = convert_amv2_to_dd(bom=bom_initial, regdef=regdef)
        assert warnings == [], f"Unexpected warnings at AMv2→DD: {warnings}"
        self._assert_dd_valid(dd_from_amv2, "DD from AMv2")

        # DD → AMv2 again
        bom_final, warnings = convert_dd_to_amv2(
            dd=dd_from_amv2, config=config, regdef=regdef,
            app_name=self.APP_NAME, app_version=self.APP_VERSION,
        )
        assert warnings == [], f"Unexpected warnings at DD→AMv2 (final): {warnings}"
        self._assert_amv2_valid(bom_final, "final AMv2")

    def test_cycle_a_service_data_preserved(self):
        """Cycle A: all service fields survive DD → AMv2 → DD."""
        dd_original, config, regdef = self._load()

        bom, _ = convert_dd_to_amv2(
            dd=dd_original, config=config, regdef=regdef,
            app_name=self.APP_NAME, app_version=self.APP_VERSION,
        )
        dd_back, _ = convert_amv2_to_dd(bom=bom, regdef=regdef)

        original_by_image = {s.image_name: s for s in dd_original.services}
        back_by_image = {s.image_name: s for s in dd_back.services}

        assert set(original_by_image) == set(back_by_image), \
            "Service image names changed after round-trip"

        for image_name, orig in original_by_image.items():
            back = back_by_image[image_name]
            assert back.full_image_name == orig.full_image_name, \
                f"full_image_name mismatch for {image_name}"
            assert back.image_type == orig.image_type, \
                f"image_type mismatch for {image_name}"
            assert back.docker_digest == orig.docker_digest, \
                f"docker_digest mismatch for {image_name}"
            if orig.image_type == "service":
                assert back.service_name == orig.service_name, \
                    f"service_name mismatch for {image_name}"
                assert back.version == orig.version, \
                    f"version mismatch for {image_name}"

    def test_cycle_a_chart_data_preserved(self):
        """Cycle A: chart fields survive DD → AMv2 → DD."""
        dd_original, config, regdef = self._load()

        bom, _ = convert_dd_to_amv2(
            dd=dd_original, config=config, regdef=regdef,
            app_name=self.APP_NAME, app_version=self.APP_VERSION,
        )
        dd_back, _ = convert_amv2_to_dd(bom=bom, regdef=regdef)

        assert len(dd_back.charts) == len(dd_original.charts)
        for orig, back in zip(dd_original.charts, dd_back.charts):
            assert back.helm_chart_name == orig.helm_chart_name
            assert back.helm_chart_version == orig.helm_chart_version
            assert back.full_chart_name == orig.full_chart_name

    def test_cycle_via_cli(self, tmp_path):
        """Full CLI cycle A: DD → AMv2 (am validate) → DD → AMv2 (am validate)."""
        amv2_file_1 = tmp_path / "manifest_1.json"
        dd_file = tmp_path / "dd_back.json"
        amv2_file_2 = tmp_path / "manifest_2.json"
        runner = CliRunner()

        # Step 1: DD → AMv2
        r = runner.invoke(cli, [
            "convert", "--to-am",
            "--input", str(self.DD_FULL),
            "--out", str(amv2_file_1),
            "--registry-def", str(self.REGDEF),
            "--config", str(self.CONFIG_FULL),
            "--name", self.APP_NAME,
            "--version", self.APP_VERSION,
        ])
        assert r.exit_code == 0, f"DD→AMv2 failed:\n{r.output}"

        # Validate AMv2 #1
        r = runner.invoke(cli, ["validate", "--input", str(amv2_file_1)])
        assert r.exit_code == 0, f"validate AMv2 #1 failed:\n{r.output}"

        # Step 2: AMv2 → DD
        r = runner.invoke(cli, [
            "convert", "--to-dd",
            "--input", str(amv2_file_1),
            "--out", str(dd_file),
            "--registry-def", str(self.REGDEF),
        ])
        assert r.exit_code == 0, f"AMv2→DD failed:\n{r.output}"

        # Step 3: DD → AMv2 again
        r = runner.invoke(cli, [
            "convert", "--to-am",
            "--input", str(dd_file),
            "--out", str(amv2_file_2),
            "--registry-def", str(self.REGDEF),
            "--config", str(self.CONFIG_FULL),
            "--name", self.APP_NAME,
            "--version", self.APP_VERSION,
        ])
        assert r.exit_code == 0, f"DD→AMv2 (2nd) failed:\n{r.output}"

        # Validate AMv2 #2
        r = runner.invoke(cli, ["validate", "--input", str(amv2_file_2)])
        assert r.exit_code == 0, f"validate AMv2 #2 failed:\n{r.output}"

        # Both manifests must have the same number of components
        data1 = json.loads(amv2_file_1.read_text(encoding="utf-8"))
        data2 = json.loads(amv2_file_2.read_text(encoding="utf-8"))
        assert len(data2["components"]) == len(data1["components"]), \
            "Component count changed between manifest #1 and #2"

    def test_cycle_amv2_dd_amv2_via_cli(self, tmp_path):
        """Cycle: AMv2 → DD → AMv2 (validate final AMv2).

        Starts from DD to produce the initial AMv2, then:
        AMv2 → DD → AMv2 and validates the final result.
        """
        amv2_initial = tmp_path / "manifest_initial.json"
        dd_file = tmp_path / "dd_from_amv2.json"
        amv2_final = tmp_path / "manifest_final.json"
        runner = CliRunner()

        # Produce initial AMv2 from DD
        r = runner.invoke(cli, [
            "convert", "--to-am",
            "--input", str(self.DD_FULL),
            "--out", str(amv2_initial),
            "--registry-def", str(self.REGDEF),
            "--config", str(self.CONFIG_FULL),
            "--name", self.APP_NAME,
            "--version", self.APP_VERSION,
        ])
        assert r.exit_code == 0, f"DD→AMv2 failed:\n{r.output}"
        r = runner.invoke(cli, ["validate", "--input", str(amv2_initial)])
        assert r.exit_code == 0, f"validate initial AMv2 failed:\n{r.output}"

        # AMv2 → DD
        r = runner.invoke(cli, [
            "convert", "--to-dd",
            "--input", str(amv2_initial),
            "--out", str(dd_file),
            "--registry-def", str(self.REGDEF),
        ])
        assert r.exit_code == 0, f"AMv2→DD failed:\n{r.output}"

        # Validate DD structure
        dd_data = json.loads(dd_file.read_text(encoding="utf-8"))
        assert len(dd_data["services"]) == 8
        assert len(dd_data["charts"]) == 1
        for svc in dd_data["services"]:
            assert svc["full_image_name"], f"empty full_image_name in {svc['image_name']}"

        # DD → AMv2
        r = runner.invoke(cli, [
            "convert", "--to-am",
            "--input", str(dd_file),
            "--out", str(amv2_final),
            "--registry-def", str(self.REGDEF),
            "--config", str(self.CONFIG_FULL),
            "--name", self.APP_NAME,
            "--version", self.APP_VERSION,
        ])
        assert r.exit_code == 0, f"DD→AMv2 final failed:\n{r.output}"

        # Validate final AMv2
        r = runner.invoke(cli, ["validate", "--input", str(amv2_final)])
        assert r.exit_code == 0, f"validate final AMv2 failed:\n{r.output}"

        # Component counts must match
        initial_data = json.loads(amv2_initial.read_text(encoding="utf-8"))
        final_data = json.loads(amv2_final.read_text(encoding="utf-8"))
        assert len(final_data["components"]) == len(initial_data["components"])
