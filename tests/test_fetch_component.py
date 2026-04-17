"""Tests for the fetch command and the artifact_fetcher service.

Real helm pull is not invoked — subprocess is mocked
and a fake .tgz with Chart.yaml and values.schema.json is created.
"""

import base64
import json
import tarfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from app_manifest.cli import cli
from app_manifest.services.artifact_fetcher import (
    fetch_helm_component,
    fetch_components_from_config,
    fetch_docker_component_from_reference,
    _compute_sha256,
    _extract_nested_components,
    _read_chart_yaml,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _create_fake_chart_tgz(dest_dir: Path, chart_name="my-chart", version="1.0.0",
                            app_version="1.0.0", with_schema=True,
                            with_profiles=True) -> Path:
    """Create a fake Helm chart .tgz for tests."""
    import io
    import yaml

    tgz_path = dest_dir / f"{chart_name}-{version}.tgz"

    with tarfile.open(tgz_path, "w:gz") as tar:
        # Chart.yaml
        chart_yaml = {
            "apiVersion": "v2",
            "name": chart_name,
            "version": version,
            "appVersion": app_version,
        }
        chart_yaml_bytes = yaml.dump(chart_yaml).encode("utf-8")
        info = tarfile.TarInfo(name=f"{chart_name}/Chart.yaml")
        info.size = len(chart_yaml_bytes)
        tar.addfile(info, io.BytesIO(chart_yaml_bytes))

        # values.schema.json
        if with_schema:
            schema = json.dumps({"type": "object", "properties": {}}).encode("utf-8")
            info = tarfile.TarInfo(name=f"{chart_name}/values.schema.json")
            info.size = len(schema)
            tar.addfile(info, io.BytesIO(schema))

        # resource-profiles
        if with_profiles:
            for profile_name in ["small.yaml", "large.yaml"]:
                profile_data = f"{profile_name.split('.')[0]}: true".encode("utf-8")
                info = tarfile.TarInfo(name=f"{chart_name}/resource-profiles/{profile_name}")
                info.size = len(profile_data)
                tar.addfile(info, io.BytesIO(profile_data))

    return tgz_path


def _mock_helm_pull(reference, dest):
    """Mock for helm pull — creates a fake .tgz."""
    ref = reference.replace("oci://", "")
    parts = ref.split(":")
    version = parts[-1] if len(parts) > 1 else "1.0.0"
    name = parts[0].split("/")[-1]

    _create_fake_chart_tgz(dest, chart_name=name, version=version, app_version=version)


# ─── Utility tests ──────────────────────────────────────────


class TestComputeSha256:
    def test_hash_is_hex_string(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        h = _compute_sha256(f)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("hello")
        f2.write_text("world")
        assert _compute_sha256(f1) != _compute_sha256(f2)


class TestExtractNestedComponents:
    def test_values_schema(self, tmp_path):
        chart_dir = tmp_path / "my-chart"
        chart_dir.mkdir()
        schema = chart_dir / "values.schema.json"
        schema.write_text('{"type": "object"}')

        result = _extract_nested_components(chart_dir)
        assert len(result) == 1
        assert result[0].name == "values.schema.json"
        assert result[0].mime_type == "application/vnd.nc.helm.values.schema"

        assert result[0].data is not None
        decoded = base64.b64decode(result[0].data[0].contents.attachment.content)
        assert json.loads(decoded) == {"type": "object"}

    def test_resource_profiles(self, tmp_path):
        chart_dir = tmp_path / "my-chart"
        profiles_dir = chart_dir / "resource-profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "small.yaml").write_text("small: true")
        (profiles_dir / "large.yaml").write_text("large: true")

        result = _extract_nested_components(chart_dir)
        assert len(result) == 1
        assert result[0].name == "resource-profile-baselines"
        assert result[0].data is not None
        assert len(result[0].data) == 2

    def test_both_schema_and_profiles(self, tmp_path):
        chart_dir = tmp_path / "my-chart"
        chart_dir.mkdir()
        (chart_dir / "values.schema.json").write_text("{}")

        profiles_dir = chart_dir / "resource-profiles"
        profiles_dir.mkdir()
        (profiles_dir / "small.yaml").write_text("small: true")

        result = _extract_nested_components(chart_dir)
        assert len(result) == 2

    def test_no_nested_data(self, tmp_path):
        chart_dir = tmp_path / "empty-chart"
        chart_dir.mkdir()
        result = _extract_nested_components(chart_dir)
        assert result == []


class TestReadChartYaml:
    def test_reads_chart_yaml(self, tmp_path):
        import yaml as pyyaml
        chart_dir = tmp_path / "my-chart"
        chart_dir.mkdir()
        (chart_dir / "Chart.yaml").write_text(
            pyyaml.dump({"name": "test", "version": "1.0"})
        )
        data = _read_chart_yaml(chart_dir)
        assert data["name"] == "test"
        assert data["version"] == "1.0"


# ─── fetch_helm_component tests ────────────────────────────


class TestFetchHelmComponent:
    def _mock_subprocess(self, reference, tmp_path):
        """Mocked subprocess.run for helm pull."""
        def fake_run(cmd, **kwargs):
            dest = Path(cmd[cmd.index("--destination") + 1])
            _mock_helm_pull(reference, dest)
            return MagicMock(returncode=0, stderr="")

        return fake_run

    def test_basic_fetch(self, tmp_path):
        ref = "oci://registry.example.com/charts/my-chart:1.0.0"

        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = self._mock_subprocess(ref, tmp_path)
            bom = fetch_helm_component(ref)

        assert len(bom.components) == 1
        comp = bom.components[0]
        assert comp.name == "my-chart"
        assert comp.version == "1.0.0"
        assert comp.type == "application"
        assert comp.mime_type == "application/vnd.nc.helm.chart"
        assert comp.purl is not None
        assert "pkg:helm/" in comp.purl
        assert comp.hashes is not None

    def test_nested_components_extracted(self, tmp_path):
        ref = "oci://registry.example.com/charts/my-chart:1.0.0"

        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = self._mock_subprocess(ref, tmp_path)
            bom = fetch_helm_component(ref)

        comp = bom.components[0]
        assert comp.components is not None
        assert len(comp.components) == 2  # values.schema.json + resource-profiles

    def test_purl_with_regdef(self, tmp_path):
        from app_manifest.services.regdef_loader import load_registry_definition

        ref = "oci://registry.qubership.org/charts/my-chart:1.0.0"
        regdef = load_registry_definition(FIXTURES / "regdefs/qubership_regdef.yml")

        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = self._mock_subprocess(ref, tmp_path)
            bom = fetch_helm_component(ref, regdef)

        comp = bom.components[0]
        assert comp.purl is not None
        assert "registry_id=registry.qubership.org" in comp.purl

    def test_helm_not_installed(self):
        ref = "oci://registry.example.com/charts/my-chart:1.0.0"

        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            with pytest.raises(RuntimeError, match="helm CLI not found"):
                fetch_helm_component(ref)

    def test_helm_pull_fails(self):
        ref = "oci://registry.example.com/charts/nonexistent:1.0.0"

        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="chart not found")
            with pytest.raises(RuntimeError, match="helm pull failed"):
                fetch_helm_component(ref)


# ─── fetch_components_from_config tests ────────────────────


class TestFetchFromConfig:
    def _fake_run(self, cmd, **kwargs):
        dest = Path(cmd[cmd.index("--destination") + 1])
        ref = next(a for a in cmd if a.startswith("oci://"))
        _mock_helm_pull(ref, dest)
        return MagicMock(returncode=0, stderr="")

    def test_fetches_helm_and_docker_with_reference(self, tmp_path):
        """fetch_components_from_config processes helm charts and docker images with a reference."""
        from app_manifest.services.config_loader import load_build_config
        config = load_build_config(FIXTURES / "configs/minimal_config.yaml")

        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = self._fake_run
            results = fetch_components_from_config(config)

        # minimal_config.yaml: 1 helm + 2 docker (all with reference)
        assert len(results) == 3
        names = [name for name, _ in results]
        assert "qubership-jaeger" in names
        assert "jaeger" in names
        assert "envoy" in names

    def test_skips_components_without_reference(self, tmp_path):
        """Components without reference (standalone) are not included in results."""
        from app_manifest.services.config_loader import load_build_config
        config = load_build_config(FIXTURES / "configs/minimal_config.yaml")

        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = self._fake_run
            results = fetch_components_from_config(config)

        # standalone-runnable has no reference → skipped
        mime_types_of_standalones = [
            bom.components[0].mime_type
            for _, bom in results
            if bom.components[0].mime_type == "application/vnd.nc.standalone-runnable"
        ]
        assert mime_types_of_standalones == []


# ─── CLI tests ─────────────────────────────────────────────


class TestFetchCLI:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["fetch", "--help"])
        assert result.exit_code == 0
        assert "--config" in result.output
        assert "--out" in result.output
        assert "--registry-def" in result.output

    def test_shows_in_root_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "fetch" in result.output

    def test_end_to_end_with_mock(self, tmp_path):
        out_dir = tmp_path / "output"

        def fake_run(cmd, **kwargs):
            dest = Path(cmd[cmd.index("--destination") + 1])
            ref = next(a for a in cmd if a.startswith("oci://"))
            _mock_helm_pull(ref, dest)
            return MagicMock(returncode=0, stderr="")

        runner = CliRunner()
        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = fake_run
            result = runner.invoke(cli, [
                "fetch",
                "-c", str(FIXTURES / "configs/minimal_config.yaml"),
                "-o", str(out_dir),
            ])

        assert result.exit_code == 0, result.output
        # Helm chart
        helm_file = out_dir / "qubership-jaeger.json"
        assert helm_file.exists()
        with open(helm_file, encoding="utf-8") as f:
            data = json.load(f)
        assert data["bomFormat"] == "CycloneDX"
        comp = data["components"][0]
        assert comp["name"] == "qubership-jaeger"
        assert "purl" in comp

        # Docker images (from reference in config)
        assert (out_dir / "jaeger.json").exists()
        assert (out_dir / "envoy.json").exists()
        with open(out_dir / "envoy.json", encoding="utf-8") as f:
            envoy_data = json.load(f)
        assert envoy_data["components"][0]["name"] == "envoy"
        assert "hashes" not in envoy_data["components"][0]  # no hash without download

    def test_no_helm_references_in_config(self, tmp_path):
        """Config with no references — print message, exit 0."""
        import yaml
        config_path = tmp_path / "empty_config.yaml"
        config_path.write_text(yaml.dump({
            "applicationVersion": "1.0.0",
            "applicationName": "test-app",
            "components": [
                {"name": "my-app", "mimeType": "application/vnd.nc.standalone-runnable"},
            ],
        }))
        out_dir = tmp_path / "output"

        runner = CliRunner()
        result = runner.invoke(cli, [
            "fetch",
            "-c", str(config_path),
            "-o", str(out_dir),
        ])

        assert result.exit_code == 0
        assert "No components with reference" in result.output


# ─── Fix: components[] in helm-chart always present ─────────


class TestHelmComponentsFieldAlwaysPresent:
    """Fix: components[] in helm-chart is always present (even when empty).

    Without values.schema.json and resource-profiles the field must not be None,
    otherwise it will be omitted on serialization and the manifest will fail validation.
    """

    def test_components_is_list_when_no_nested_artifacts(self, tmp_path):
        """Chart without values.schema.json and resource-profiles → components=[]."""
        ref = "oci://registry.example.com/charts/bare-chart:2.0.0"

        def fake_run(cmd, **kwargs):
            dest = Path(cmd[cmd.index("--destination") + 1])
            _create_fake_chart_tgz(dest, chart_name="bare-chart", version="2.0.0",
                                   with_schema=False, with_profiles=False)
            return MagicMock(returncode=0, stderr="")

        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = fake_run
            bom = fetch_helm_component(ref)

        comp = bom.components[0]
        assert comp.components is not None, "components must not be None"
        assert comp.components == [], "components must be empty list, not None"

    def test_components_field_present_in_json_output(self, tmp_path):
        """Serialized JSON contains 'components': [] even for a bare chart."""
        ref = "oci://registry.example.com/charts/bare-chart:2.0.0"

        def fake_run(cmd, **kwargs):
            dest = Path(cmd[cmd.index("--destination") + 1])
            _create_fake_chart_tgz(dest, chart_name="bare-chart", version="2.0.0",
                                   with_schema=False, with_profiles=False)
            return MagicMock(returncode=0, stderr="")

        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = fake_run
            bom = fetch_helm_component(ref)

        data = bom.model_dump(by_alias=True, exclude_none=True)
        helm_comp = data["components"][0]
        assert "components" in helm_comp, "'components' field must be present in serialized JSON"


class TestFetchDuplicateNameWarning:
    """Fix: duplicate component names use a vendor suffix from mimeType."""

    def test_duplicate_name_uses_vendor_suffix(self, tmp_path):
        """Components with the same name but different mimeType each get a unique
        filename with a vendor suffix derived from mimeType, and a warning in stderr."""
        import yaml
        config_path = tmp_path / "dup_config.yaml"
        config_path.write_text(yaml.dump({
            "applicationVersion": "1.0.0",
            "applicationName": "test-app",
            "components": [
                {
                    "name": "my-chart",
                    "mimeType": "application/vnd.nc.helm.chart",
                    "reference": "oci://registry.example.com/charts/my-chart:1.0.0",
                },
                {
                    "name": "my-chart",
                    "mimeType": "application/vnd.docker.image",
                    "reference": "docker.io/myorg/my-chart:1.0.0",
                },
            ],
        }))
        out_dir = tmp_path / "output"

        def fake_run(cmd, **kwargs):
            dest = Path(cmd[cmd.index("--destination") + 1])
            ref = next(a for a in cmd if a.startswith("oci://"))
            _mock_helm_pull(ref, dest)
            return MagicMock(returncode=0, stderr="")

        runner = CliRunner()
        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = fake_run
            result = runner.invoke(cli, [
                "fetch",
                "-c", str(config_path),
                "-o", str(out_dir),
            ])

        assert result.exit_code == 0, result.output
        # Both files created with unique names based on full mime-type suffix
        assert (out_dir / "my-chart_vnd_nc_helm_chart.json").exists()
        assert (out_dir / "my-chart_vnd_docker_image.json").exists()
        # No file without suffix
        assert not (out_dir / "my-chart.json").exists()
        # Warning appears in output (Click 8.2+ mixes stderr into output)
        assert "duplicate" in result.output
        assert "my-chart" in result.output


# ─── Docker from reference tests ────────────────────────────


class TestFetchDockerFromReference:
    """Tests for fetch_docker_component_from_reference."""

    def _make_docker_config(self, name: str, reference: str, mime_type: str = "application/vnd.docker.image"):
        from app_manifest.models.config import ComponentConfig, MimeType
        return ComponentConfig(
            name=name,
            mimeType=MimeType(mime_type),
            reference=reference,
        )

    def test_basic_docker_from_reference(self):
        comp = self._make_docker_config("envoy", "docker.io/envoyproxy/envoy:v1.32.6")
        bom = fetch_docker_component_from_reference(comp)

        assert len(bom.components) == 1
        c = bom.components[0]
        assert c.name == "envoy"           # name from config, not from reference
        assert c.version == "v1.32.6"
        assert c.group == "envoyproxy"
        assert c.type == "container"
        assert c.mime_type == "application/vnd.docker.image"
        assert c.hashes is None            # hash unknown without download
        assert c.purl is not None
        assert "envoyproxy/envoy" in c.purl
        assert "v1.32.6" in c.purl

    def test_name_comes_from_config_not_reference(self):
        """name in the component is taken from config, not from reference — so generate can match it."""
        comp = self._make_docker_config("my-service", "registry.example.com/team/actual-image-name:1.0")
        bom = fetch_docker_component_from_reference(comp)

        assert bom.components[0].name == "my-service"  # from config

    def test_purl_with_registry_host(self):
        comp = self._make_docker_config("jaeger", "sandbox.example.com/core/jaeger:build3")
        bom = fetch_docker_component_from_reference(comp)

        c = bom.components[0]
        assert c.purl is not None
        assert "registry_id=sandbox.example.com" in c.purl

    def test_purl_with_regdef(self):
        from app_manifest.services.regdef_loader import load_registry_definition
        regdef = load_registry_definition(FIXTURES / "regdefs/sandbox_regdef.yml")

        comp = self._make_docker_config("jaeger", "sandbox.example.com/core/jaeger:build3")
        bom = fetch_docker_component_from_reference(comp, regdef)

        c = bom.components[0]
        # registry_name must be the name from regdef, not the host
        assert c.purl is not None
        assert "sandbox.example.com" not in c.purl or "registry_id=" in c.purl

    def test_valid_bom_structure(self):
        comp = self._make_docker_config("envoy", "docker.io/envoyproxy/envoy:v1.32.6")
        bom = fetch_docker_component_from_reference(comp)

        assert bom.metadata is not None
        assert bom.metadata.timestamp is not None
        assert bom.metadata.tools is not None
        assert bom.dependencies == []

    def test_serialization_no_hashes_field(self):
        """Serialized JSON does not contain the hashes field (no hash computed)."""
        comp = self._make_docker_config("envoy", "docker.io/envoyproxy/envoy:v1.32.6")
        bom = fetch_docker_component_from_reference(comp)

        data = bom.model_dump(by_alias=True, exclude_none=True)
        c = data["components"][0]
        assert "hashes" not in c
        assert "purl" in c
        assert "version" in c
