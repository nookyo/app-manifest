"""End-to-end tests covering various component metadata formats and reference formats.

Covers:
- Docker reference formats: docker.io with org, bare name (library), ghcr.io, private registry
- Component metadata variations: with/without hash, with/without reference, with/without group
- fetch: helm + multiple docker images from reference in config
- Full pipeline: component → fetch → generate for monitoring-platform
"""

import json
import tarfile
import io
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml
from click.testing import CliRunner

from app_manifest.cli import cli
from app_manifest.models.config import BuildConfig, MimeType
from app_manifest.services.artifact_fetcher import (
    fetch_components_from_config,
    fetch_docker_component_from_reference,
)
from app_manifest.models.config import ComponentConfig

FIXTURES = Path(__file__).parent / "fixtures"


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────

def _make_tgz(dest_dir: Path, chart_name: str, version: str, app_version: str | None = None) -> None:
    """Create a minimal chart .tgz file in dest_dir."""
    chart_yaml_content = f"name: {chart_name}\nversion: {version}\n"
    if app_version:
        chart_yaml_content += f"appVersion: {app_version}\n"

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        chart_dir = f"{chart_name}/"
        info = tarfile.TarInfo(name=chart_dir)
        info.type = tarfile.DIRTYPE
        tar.addfile(info)

        data = chart_yaml_content.encode()
        info = tarfile.TarInfo(name=f"{chart_dir}Chart.yaml")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    tgz_bytes = buf.getvalue()
    tgz_path = dest_dir / f"{chart_name}-{version}.tgz"
    tgz_path.write_bytes(tgz_bytes)


def _helm_side_effect(chart_name: str, version: str, app_version: str | None = None):
    """Returns a side_effect for subprocess.run that simulates helm pull."""
    def fake_run(cmd, **kwargs):
        dest = Path(cmd[cmd.index("--destination") + 1])
        _make_tgz(dest, chart_name, version, app_version)
        return MagicMock(returncode=0, stderr="")
    return fake_run


# ─────────────────────────────────────────────────────────────────────────────
# 1. Docker reference formats
# ─────────────────────────────────────────────────────────────────────────────

class TestDockerReferenceFormats:
    """fetch creates correct mini-manifests for various reference formats."""

    def _make_comp(self, name: str, reference: str) -> ComponentConfig:
        return ComponentConfig(
            name=name,
            mime_type=MimeType.DOCKER_IMAGE,
            reference=reference,
        )

    @pytest.mark.parametrize("reference,exp_version,exp_group,exp_purl_fragment", [
        # explicit docker.io with org
        (
            "docker.io/prom/prometheus:v2.52.0",
            "v2.52.0",
            "prom",
            "pkg:docker/prom/prometheus@v2.52.0?registry_name=docker.io",
        ),
        # docker.io without explicit prefix (org/image)
        (
            "grafana/grafana:10.4.2",
            "10.4.2",
            "grafana",
            "pkg:docker/grafana/grafana@10.4.2?registry_name=docker.io",
        ),
        # bare image name → library namespace
        (
            "nginx:1.27.0",
            "1.27.0",
            "library",
            "pkg:docker/library/nginx@1.27.0?registry_name=docker.io",
        ),
        # GitHub Container Registry
        (
            "ghcr.io/oauth2-proxy/oauth2-proxy:v7.7.0",
            "v7.7.0",
            "oauth2-proxy",
            "pkg:docker/oauth2-proxy/oauth2-proxy@v7.7.0?registry_name=ghcr.io",
        ),
        # Private registry with namespace
        (
            "sandbox.example.com/monitoring/alertmanager:v0.27.0",
            "v0.27.0",
            "monitoring",
            "pkg:docker/monitoring/alertmanager@v0.27.0?registry_name=sandbox.example.com",
        ),
        # registry without namespace (host/image only)
        (
            "my-registry.corp.com/myapp:1.0.0",
            "1.0.0",
            None,  # no namespace → group is absent
            "pkg:docker/myapp@1.0.0?registry_name=my-registry.corp.com",
        ),
    ])
    def test_reference_parsed_correctly(self, reference, exp_version, exp_group, exp_purl_fragment):
        comp = self._make_comp("test-comp", reference)
        bom = fetch_docker_component_from_reference(comp)

        c = bom.components[0]
        assert c.version == exp_version, f"version mismatch for {reference!r}"
        assert c.group == exp_group, f"group mismatch for {reference!r}"
        assert c.purl == exp_purl_fragment, f"purl mismatch for {reference!r}"

    def test_name_taken_from_config_not_reference(self):
        """name in the mini-manifest is always taken from the config, not from the reference."""
        comp = self._make_comp("my-config-name", "registry.io/some-org/actual-image-name:2.0")
        bom = fetch_docker_component_from_reference(comp)
        assert bom.components[0].name == "my-config-name"

    def test_no_hashes_in_output(self):
        """Hash is not computed — hashes field is absent."""
        comp = self._make_comp("envoy", "docker.io/envoyproxy/envoy:v1.32.6")
        bom = fetch_docker_component_from_reference(comp)
        assert bom.components[0].hashes is None

    def test_mime_type_preserved(self):
        comp = self._make_comp("envoy", "docker.io/envoyproxy/envoy:v1.32.6")
        bom = fetch_docker_component_from_reference(comp)
        assert bom.components[0].mime_type == "application/vnd.docker.image"

    def test_type_is_container(self):
        comp = self._make_comp("envoy", "docker.io/envoyproxy/envoy:v1.32.6")
        bom = fetch_docker_component_from_reference(comp)
        assert bom.components[0].type == "container"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Component metadata variations (component command)
# ─────────────────────────────────────────────────────────────────────────────

class TestComponentMetadataVariations:
    """component builds a correct mini-manifest for various CI metadata formats."""

    def _run_component(self, tmp_path: Path, meta: dict) -> dict:
        meta_file = tmp_path / "meta.json"
        out_file = tmp_path / "mini.json"
        meta_file.write_text(json.dumps(meta), encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, [
            "component",
            "-i", str(meta_file),
            "-o", str(out_file),
        ])
        assert result.exit_code == 0, result.output
        return json.loads(out_file.read_text(encoding="utf-8"))

    def test_full_docker_metadata(self, tmp_path):
        """Full metadata: name, type, mime-type, group, version, hashes, reference."""
        data = self._run_component(tmp_path, {
            "name": "prometheus",
            "type": "container",
            "mime-type": "application/vnd.docker.image",
            "group": "prom",
            "version": "v2.52.0",
            "hashes": [{"alg": "SHA-256", "content": "a" * 64}],
            "reference": "docker.io/prom/prometheus:v2.52.0",
        })
        comp = data["components"][0]
        assert comp["name"] == "prometheus"
        assert comp["version"] == "v2.52.0"
        assert comp["group"] == "prom"
        assert comp["hashes"][0]["alg"] == "SHA-256"
        assert "purl" in comp

    def test_minimal_docker_metadata_no_hash_no_reference(self, tmp_path):
        """Minimal metadata: only name, type, mime-type — no hash or reference."""
        data = self._run_component(tmp_path, {
            "name": "sidecar",
            "type": "container",
            "mime-type": "application/vnd.docker.image",
        })
        comp = data["components"][0]
        assert comp["name"] == "sidecar"
        assert "hashes" not in comp
        assert "purl" not in comp

    def test_docker_with_reference_but_no_hash(self, tmp_path):
        """reference present, no hash — PURL is built, hashes absent."""
        data = self._run_component(tmp_path, {
            "name": "grafana",
            "type": "container",
            "mime-type": "application/vnd.docker.image",
            "reference": "grafana/grafana:10.4.2",
        })
        comp = data["components"][0]
        assert "purl" in comp
        assert "hashes" not in comp
        assert "grafana" in comp["purl"]

    def test_docker_without_group(self, tmp_path):
        """group not specified — group field absent from output."""
        data = self._run_component(tmp_path, {
            "name": "nginx",
            "type": "container",
            "mime-type": "application/vnd.docker.image",
            "version": "1.27.0",
        })
        comp = data["components"][0]
        assert "group" not in comp

    def test_multiple_hash_algorithms(self, tmp_path):
        """Multiple hash algorithms: SHA-256 and MD5."""
        data = self._run_component(tmp_path, {
            "name": "multi-hash-image",
            "type": "container",
            "mime-type": "application/vnd.docker.image",
            "version": "1.0.0",
            "hashes": [
                {"alg": "SHA-256", "content": "b" * 64},
                {"alg": "MD5", "content": "c" * 32},
            ],
        })
        comp = data["components"][0]
        algs = {h["alg"] for h in comp["hashes"]}
        assert "SHA-256" in algs
        assert "MD5" in algs

    def test_helm_metadata_with_app_version(self, tmp_path):
        """Helm from CI: appVersion differs from the chart version."""
        data = self._run_component(tmp_path, {
            "name": "my-chart",
            "type": "application",
            "mime-type": "application/vnd.nc.helm.chart",
            "version": "1.0.0",
            "appVersion": "3.5.1",
            "hashes": [{"alg": "SHA-256", "content": "d" * 64}],
            "reference": "oci://registry.example.com/charts/my-chart:1.0.0",
        })
        comp = data["components"][0]
        assert comp["name"] == "my-chart"
        assert comp["version"] == "3.5.1"   # appVersion is used as the component version
        assert "hashes" in comp

    def test_docker_ghcr_reference(self, tmp_path):
        """reference from ghcr.io — purl contains the correct registry_name."""
        data = self._run_component(tmp_path, {
            "name": "oauth2-proxy",
            "type": "container",
            "mime-type": "application/vnd.docker.image",
            "reference": "ghcr.io/oauth2-proxy/oauth2-proxy:v7.7.0",
        })
        comp = data["components"][0]
        assert "ghcr.io" in comp["purl"]
        assert "oauth2-proxy" in comp["purl"]

    def test_nested_components_from_ci_metadata(self, tmp_path):
        """Helm from CI can contain nested components (values.schema.json)."""
        import base64
        schema_b64 = base64.b64encode(b'{"type":"object"}').decode()
        data = self._run_component(tmp_path, {
            "name": "platform-chart",
            "type": "application",
            "mime-type": "application/vnd.nc.helm.chart",
            "version": "2.0.0",
            "hashes": [{"alg": "SHA-256", "content": "e" * 64}],
            "reference": "oci://registry.example.com/charts/platform-chart:2.0.0",
            "components": [
                {
                    "type": "data",
                    "mime-type": "application/vnd.nc.helm.values.schema",
                    "name": "values.schema.json",
                    "data": [{
                        "type": "configuration",
                        "name": "values.schema.json",
                        "contents": {
                            "attachment": {
                                "contentType": "application/json",
                                "encoding": "base64",
                                "content": schema_b64,
                            }
                        },
                    }],
                }
            ],
        })
        comp = data["components"][0]
        assert "components" in comp
        assert comp["components"][0]["name"] == "values.schema.json"


# ─────────────────────────────────────────────────────────────────────────────
# 3. fetch for monitoring-platform (helm + multiple docker from reference)
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchMonitoringPlatform:
    """fetch processes monitoring_config.yaml: 1 helm + 5 docker references."""

    def test_fetch_creates_all_mini_manifests(self, tmp_path):
        runner = CliRunner()
        out_dir = tmp_path / "minis"

        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = _helm_side_effect(
                "qubership-monitoring-platform", "3.5.1"
            )
            result = runner.invoke(cli, [
                "fetch",
                "-c", str(FIXTURES / "configs/monitoring_config.yaml"),
                "-o", str(out_dir),
            ])

        assert result.exit_code == 0, result.output

        # Helm chart
        helm_file = out_dir / "qubership-monitoring-platform.json"
        assert helm_file.exists(), "helm mini-manifest must be created"

        # Docker images from reference
        for name in ["prometheus", "grafana", "nginx", "oauth2-proxy", "alertmanager"]:
            f = out_dir / f"{name}.json"
            assert f.exists(), f"{name}.json must be created"

    def test_docker_mini_manifests_have_correct_purls(self, tmp_path):
        runner = CliRunner()
        out_dir = tmp_path / "minis"

        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = _helm_side_effect(
                "qubership-monitoring-platform", "3.5.1"
            )
            runner.invoke(cli, [
                "fetch",
                "-c", str(FIXTURES / "configs/monitoring_config.yaml"),
                "-o", str(out_dir),
            ])

        cases = {
            "prometheus": ("prom", "v2.52.0", "docker.io"),
            "grafana": ("grafana", "10.4.2", "docker.io"),
            "nginx": ("library", "1.27.0", "docker.io"),
            "oauth2-proxy": ("oauth2-proxy", "v7.7.0", "ghcr.io"),
            "alertmanager": ("monitoring", "v0.27.0", "sandbox.example.com"),
        }

        for comp_name, (exp_group, exp_version, exp_registry) in cases.items():
            f = out_dir / f"{comp_name}.json"
            data = json.loads(f.read_text(encoding="utf-8"))
            comp = data["components"][0]

            assert comp["name"] == comp_name, f"{comp_name}: wrong name"
            assert comp["version"] == exp_version, f"{comp_name}: wrong version"
            assert comp.get("group") == exp_group, f"{comp_name}: wrong group"
            assert exp_registry in comp["purl"], f"{comp_name}: wrong registry in purl"
            assert "hashes" not in comp, f"{comp_name}: hashes must be absent (no download)"

    def test_helm_mini_manifest_has_hash(self, tmp_path):
        runner = CliRunner()
        out_dir = tmp_path / "minis"

        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = _helm_side_effect(
                "qubership-monitoring-platform", "3.5.1"
            )
            runner.invoke(cli, [
                "fetch",
                "-c", str(FIXTURES / "configs/monitoring_config.yaml"),
                "-o", str(out_dir),
            ])

        data = json.loads((out_dir / "qubership-monitoring-platform.json").read_text())
        comp = data["components"][0]
        assert "hashes" in comp
        assert comp["hashes"][0]["alg"] == "SHA-256"

    def test_fetch_returns_six_results(self, tmp_path):
        """fetch returns 6 results: 1 helm + 5 docker."""
        config = BuildConfig.model_validate(
            yaml.safe_load((FIXTURES / "configs/monitoring_config.yaml").read_text(encoding="utf-8"))
        )

        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = _helm_side_effect(
                "qubership-monitoring-platform", "3.5.1"
            )
            results = fetch_components_from_config(config)

        assert len(results) == 6
        names = [name for name, _ in results]
        assert "qubership-monitoring-platform" in names
        assert "prometheus" in names
        assert "grafana" in names
        assert "nginx" in names
        assert "oauth2-proxy" in names
        assert "alertmanager" in names


# ─────────────────────────────────────────────────────────────────────────────
# 4. Full pipeline: component + fetch + generate (monitoring-platform)
# ─────────────────────────────────────────────────────────────────────────────

class TestFullPipelineMonitoring:
    """Full pipeline for monitoring-platform.

    Config: mixed_pipeline_config.yaml
    - prometheus, grafana: no reference in config → mini-manifest built by component (from CI)
    - nginx, oauth2-proxy, alertmanager: have reference → mini-manifest built by fetch
    - helm: has reference → fetch

    This avoids collisions: fetch skips prometheus/grafana (they have no reference),
    so mini-manifests with hashes from component are not overwritten.
    """

    # Metadata for CI-built images (reference in metadata for PURL, but not in config)
    _CI_IMAGES = [
        {
            "name": "prometheus",
            "type": "container",
            "mime-type": "application/vnd.docker.image",
            "group": "prom",
            "version": "v2.52.0",
            "hashes": [{"alg": "SHA-256", "content": "1" * 64}],
            "reference": "docker.io/prom/prometheus:v2.52.0",
        },
        {
            "name": "grafana",
            "type": "container",
            "mime-type": "application/vnd.docker.image",
            "group": "grafana",
            "version": "10.4.2",
            "hashes": [{"alg": "SHA-256", "content": "2" * 64}],
            "reference": "grafana/grafana:10.4.2",
        },
    ]

    _CONFIG = FIXTURES / "configs/mixed_pipeline_config.yaml"

    def _build_manifest(self, tmp_path: Path) -> dict:
        """Run the full pipeline and return the resulting manifest."""
        minis_dir = tmp_path / "minis"
        minis_dir.mkdir()
        runner = CliRunner()

        # Step 1: component for prometheus and grafana (from CI, with hash)
        for meta in self._CI_IMAGES:
            meta_file = tmp_path / f"{meta['name']}_meta.json"
            meta_file.write_text(json.dumps(meta), encoding="utf-8")
            result = runner.invoke(cli, [
                "component",
                "-i", str(meta_file),
                "-o", str(minis_dir / f"{meta['name']}.json"),
            ])
            assert result.exit_code == 0, f"component failed for {meta['name']}: {result.output}"

        # Step 2: fetch — helm + nginx, oauth2-proxy, alertmanager from reference
        # prometheus and grafana in mixed_pipeline_config.yaml have NO reference → fetch skips them
        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = _helm_side_effect(
                "qubership-monitoring-platform", "3.5.1"
            )
            result = runner.invoke(cli, [
                "fetch",
                "-c", str(self._CONFIG),
                "-o", str(minis_dir),
            ])
        assert result.exit_code == 0, f"fetch failed: {result.output}"

        # Step 3: generate
        out_manifest = tmp_path / "manifest.json"
        result = runner.invoke(cli, [
            "generate",
            "-c", str(self._CONFIG),
            "-o", str(out_manifest),
            str(minis_dir),
        ])
        assert result.exit_code == 0, f"generate failed: {result.output}"

        return json.loads(out_manifest.read_text(encoding="utf-8"))

    def test_manifest_structure(self, tmp_path):
        data = self._build_manifest(tmp_path)

        assert data["bomFormat"] == "CycloneDX"
        assert data["specVersion"] == "1.6"
        assert data["metadata"]["component"]["name"] == "qubership-monitoring-platform"
        assert data["metadata"]["component"]["version"] == "3.5.1"

    def test_manifest_component_count(self, tmp_path):
        """Component count: standalone + helm + 5 docker = 7."""
        data = self._build_manifest(tmp_path)
        assert len(data["components"]) == 7

    def test_manifest_all_services_present(self, tmp_path):
        data = self._build_manifest(tmp_path)
        names = {c["name"] for c in data["components"]}

        expected = {
            "qubership-monitoring-platform",  # standalone + helm (same name)
            "prometheus",
            "grafana",
            "nginx",
            "oauth2-proxy",
            "alertmanager",
        }
        assert expected == names, f"unexpected components: {names ^ expected}"

    def test_prometheus_has_hash_from_ci(self, tmp_path):
        """prometheus came from CI via component — hash must be present."""
        data = self._build_manifest(tmp_path)
        comp = next(c for c in data["components"] if c["name"] == "prometheus")
        assert "hashes" in comp

    def test_grafana_has_hash_from_ci(self, tmp_path):
        """grafana came from CI via component — hash must be present."""
        data = self._build_manifest(tmp_path)
        comp = next(c for c in data["components"] if c["name"] == "grafana")
        assert "hashes" in comp

    def test_nginx_has_no_hash(self, tmp_path):
        """nginx came from reference via fetch — no hash."""
        data = self._build_manifest(tmp_path)
        comp = next(c for c in data["components"] if c["name"] == "nginx")
        assert "hashes" not in comp

    def test_oauth2_proxy_purl(self, tmp_path):
        """oauth2-proxy from ghcr.io — purl contains ghcr.io."""
        data = self._build_manifest(tmp_path)
        comp = next(c for c in data["components"] if c["name"] == "oauth2-proxy")
        assert "ghcr.io" in comp.get("purl", "")

    def test_dependencies_present(self, tmp_path):
        data = self._build_manifest(tmp_path)
        assert len(data["dependencies"]) > 0

    def test_helm_depends_on_all_docker_images(self, tmp_path):
        data = self._build_manifest(tmp_path)

        helm_comp = next(
            c for c in data["components"]
            if c.get("mime-type") == "application/vnd.nc.helm.chart"
        )
        helm_ref = helm_comp["bom-ref"]

        helm_deps = next(
            d for d in data["dependencies"]
            if d["ref"] == helm_ref
        )

        # Helm depends on all 5 docker images
        assert len(helm_deps["dependsOn"]) == 5


# ─────────────────────────────────────────────────────────────────────────────
# 5. Jaeger: real config with 11 docker images, helm chart, and validation
# ─────────────────────────────────────────────────────────────────────────────

class TestJaegerFullPipeline:
    """Full pipeline for jaeger_full_config.yaml.

    Mini-manifest sources:
    - component: 4 CI images (no reference in config):
        jaeger-readiness-probe, jaeger-integration-tests,
        spark-dependencies-image, qubership-deployment-status-provisioner
    - fetch (mock helm): qubership-jaeger helm chart
    - fetch (reference): 7 docker images with reference in config:
        jaeger-cassandra-schema, jaeger, example-hotrod,
        jaeger-es-index-cleaner, jaeger-es-rollover, envoy, openjdk

    Final manifest contains 13 components:
        qubership-jaeger (standalone) + qubership-jaeger (helm) + 11 docker = 13

    Validation with --validate runs at the end.
    """

    _CONFIG = FIXTURES / "configs/jaeger_full_config.yaml"

    _CI_METADATA_FILES = [
        FIXTURES / "metadata/jaeger_readiness_probe_metadata.json",
        FIXTURES / "metadata/jaeger_integration_tests_metadata.json",
        FIXTURES / "metadata/spark_dependencies_metadata.json",
        FIXTURES / "metadata/qubership_dsp_metadata.json",
    ]

    def _build_manifest(self, tmp_path: Path) -> dict:
        minis_dir = tmp_path / "minis"
        minis_dir.mkdir()
        runner = CliRunner()

        # Step 1: component for 4 CI images
        for meta_file in self._CI_METADATA_FILES:
            name = json.loads(meta_file.read_text(encoding="utf-8"))["name"]
            result = runner.invoke(cli, [
                "component",
                "-i", str(meta_file),
                "-o", str(minis_dir / f"{name}.json"),
            ])
            assert result.exit_code == 0, f"component failed for {name}: {result.output}"

        # Step 2: fetch — helm (mock) + 7 docker from reference
        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = _helm_side_effect(
                "qubership-jaeger", "1.2.3", app_version="1.2.3"
            )
            result = runner.invoke(cli, [
                "fetch",
                "-c", str(self._CONFIG),
                "-o", str(minis_dir),
            ])
        assert result.exit_code == 0, f"fetch failed: {result.output}"

        # Step 3: generate --validate
        out_manifest = tmp_path / "manifest.json"
        result = runner.invoke(cli, [
            "generate",
            "--validate",
            "-c", str(self._CONFIG),
            "-o", str(out_manifest),
            str(minis_dir),
        ])
        assert result.exit_code == 0, f"generate failed:\n{result.output}"
        assert "Manifest is valid" in result.output

        # Save the reference manifest to fixtures/examples/ for documentation and manual inspection
        examples_dir = FIXTURES / "examples"
        examples_dir.mkdir(exist_ok=True)
        saved = examples_dir / "jaeger_manifest.json"
        saved.write_text(out_manifest.read_text(encoding="utf-8"), encoding="utf-8")

        return json.loads(out_manifest.read_text(encoding="utf-8"))

    def test_manifest_is_valid(self, tmp_path):
        """generate --validate succeeds without errors."""
        self._build_manifest(tmp_path)  # assert внутри _build_manifest

    def test_component_count(self, tmp_path):
        """qubership-jaeger(standalone) + qubership-jaeger(helm) + 11 docker = 13."""
        data = self._build_manifest(tmp_path)
        assert len(data["components"]) == 13

    def test_all_components_present(self, tmp_path):
        data = self._build_manifest(tmp_path)
        names = {c["name"] for c in data["components"]}

        expected = {
            "qubership-jaeger",
            "jaeger-cassandra-schema",
            "jaeger",
            "jaeger-readiness-probe",
            "example-hotrod",
            "jaeger-integration-tests",
            "jaeger-es-index-cleaner",
            "jaeger-es-rollover",
            "envoy",
            "openjdk",
            "spark-dependencies-image",
            "qubership-deployment-status-provisioner",
        }
        assert expected == names, f"diff: {names ^ expected}"

    def test_ci_images_have_hashes(self, tmp_path):
        """Images from CI (via component) have a hash."""
        data = self._build_manifest(tmp_path)
        ci_names = {
            "jaeger-readiness-probe",
            "jaeger-integration-tests",
            "spark-dependencies-image",
            "qubership-deployment-status-provisioner",
        }
        for comp in data["components"]:
            if comp["name"] in ci_names:
                assert "hashes" in comp, f"{comp['name']} must have hashes"

    def test_reference_images_have_no_hashes(self, tmp_path):
        """Images from reference (via fetch) — no hash."""
        data = self._build_manifest(tmp_path)
        ref_names = {
            "jaeger-cassandra-schema",
            "jaeger",
            "example-hotrod",
            "jaeger-es-index-cleaner",
            "jaeger-es-rollover",
            "envoy",
            "openjdk",
        }
        for comp in data["components"]:
            if comp["name"] in ref_names:
                assert "hashes" not in comp, f"{comp['name']} must not have hashes"

    def test_reference_images_have_purls(self, tmp_path):
        """All images with reference have a purl."""
        data = self._build_manifest(tmp_path)
        ref_names = {
            "jaeger-cassandra-schema",
            "jaeger",
            "example-hotrod",
            "jaeger-es-index-cleaner",
            "jaeger-es-rollover",
            "envoy",
            "openjdk",
        }
        for comp in data["components"]:
            if comp["name"] in ref_names:
                assert "purl" in comp, f"{comp['name']} must have purl"
                assert "jaegertracing" in comp["purl"] or comp["name"] in ("envoy", "openjdk"), \
                    f"unexpected purl for {comp['name']}: {comp['purl']}"

    def test_envoy_purl(self, tmp_path):
        data = self._build_manifest(tmp_path)
        comp = next(c for c in data["components"] if c["name"] == "envoy")
        assert comp["purl"] == "pkg:docker/envoyproxy/envoy@v1.32.6?registry_name=docker.io"

    def test_openjdk_purl(self, tmp_path):
        """openjdk — docker.io/library/openjdk:11 → group=library."""
        data = self._build_manifest(tmp_path)
        comp = next(c for c in data["components"] if c["name"] == "openjdk")
        assert comp["purl"] == "pkg:docker/library/openjdk@11?registry_name=docker.io"
        assert comp.get("group") == "library"

    def test_helm_depends_on_11_images(self, tmp_path):
        """qubership-jaeger helm depends on exactly 11 docker images."""
        data = self._build_manifest(tmp_path)
        helm_comp = next(
            c for c in data["components"]
            if c.get("mime-type") == "application/vnd.nc.helm.chart"
        )
        helm_deps = next(
            d for d in data["dependencies"]
            if d["ref"] == helm_comp["bom-ref"]
        )
        assert len(helm_deps["dependsOn"]) == 11

    def test_standalone_depends_on_helm(self, tmp_path):
        """qubership-jaeger standalone-runnable зависит от qubership-jaeger helm."""
        data = self._build_manifest(tmp_path)
        standalone = next(
            c for c in data["components"]
            if c.get("mime-type") == "application/vnd.nc.standalone-runnable"
        )
        helm_comp = next(
            c for c in data["components"]
            if c.get("mime-type") == "application/vnd.nc.helm.chart"
        )
        standalone_deps = next(
            d for d in data["dependencies"]
            if d["ref"] == standalone["bom-ref"]
        )
        assert helm_comp["bom-ref"] in standalone_deps["dependsOn"]

    def test_metadata_version(self, tmp_path):
        data = self._build_manifest(tmp_path)
        assert data["metadata"]["component"]["version"] == "1.2.3"
        assert data["metadata"]["component"]["name"] == "jaeger"

    def test_saved_manifest_passes_standalone_validate(self, tmp_path):
        """Saved manifest passes the validate command as a standalone step.

        Simulates a scenario: manifest generated in one CI step,
        validated in another.
        """
        self._build_manifest(tmp_path)  # saves to fixtures/examples/jaeger_manifest.json

        saved = FIXTURES / "examples" / "jaeger_manifest.json"
        assert saved.exists(), "saved manifest must exist after _build_manifest"

        runner = CliRunner()
        result = runner.invoke(cli, ["validate", "-i", str(saved)])
        assert result.exit_code == 0, f"validate failed: {result.output}"
        assert "Manifest is valid" in result.output
