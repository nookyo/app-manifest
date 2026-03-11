import io
import json
import tarfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import yaml
from click.testing import CliRunner

from app_manifest.cli import cli

FIXTURES = Path(__file__).parent / "fixtures"


def _create_fake_chart_tgz(dest_dir: Path, chart_name: str, version: str) -> Path:
    """Create a fake Helm chart .tgz for tests."""
    tgz_path = dest_dir / f"{chart_name}-{version}.tgz"
    with tarfile.open(tgz_path, "w:gz") as tar:
        chart_yaml_bytes = yaml.dump({
            "apiVersion": "v2",
            "name": chart_name,
            "version": version,
            "appVersion": version,
        }).encode("utf-8")
        info = tarfile.TarInfo(name=f"{chart_name}/Chart.yaml")
        info.size = len(chart_yaml_bytes)
        tar.addfile(info, io.BytesIO(chart_yaml_bytes))

        schema = json.dumps({"type": "object", "properties": {}}).encode("utf-8")
        info = tarfile.TarInfo(name=f"{chart_name}/values.schema.json")
        info.size = len(schema)
        tar.addfile(info, io.BytesIO(schema))
    return tgz_path


def _fake_helm_run(cmd, **kwargs):
    """Mock subprocess.run for helm pull."""
    dest = Path(cmd[cmd.index("--destination") + 1])
    ref = next(a for a in cmd if a.startswith("oci://"))
    parts = ref.replace("oci://", "").split(":")
    version = parts[-1] if len(parts) > 1 else "1.0.0"
    name = parts[0].split("/")[-1]
    _create_fake_chart_tgz(dest, chart_name=name, version=version)
    return MagicMock(returncode=0, stderr="")


def _create_mini_manifests(tmp_path, metadata_files, regdef=None):
    """Create mini-manifests via the component CLI command."""
    runner = CliRunner()
    output_files = []
    for meta_file in metadata_files:
        out = tmp_path / f"mini_{meta_file.stem}.json"
        args = [
            "component",
            "-i", str(meta_file),
            "-o", str(out),
        ]
        if regdef:
            args.extend(["-r", str(regdef)])
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        output_files.append(out)
    return output_files


def test_generate_help():
    """generate command shows help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["generate", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.output
    assert "--out" in result.output
    assert "--version" in result.output
    assert "--name" in result.output


def test_cli_help():
    """Root command shows help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "generate" in result.output


class TestGenerateEndToEnd:
    """End-to-end tests: component → generate → JSON file."""

    def test_full_pipeline(self, tmp_path):
        """Full pipeline: CI metadata → mini-manifests → final manifest."""
        regdef = FIXTURES / "regdefs/qubership_regdef.yml"
        mini_files = _create_mini_manifests(tmp_path, [
            FIXTURES / "metadata/docker_metadata.json",
            FIXTURES / "metadata/helm_metadata.json",
            FIXTURES / "metadata/envoy_metadata.json",
        ], regdef)

        out_file = tmp_path / "manifest.json"
        runner = CliRunner()
        result = runner.invoke(cli, [
            "generate",
            "-c", str(FIXTURES / "configs/minimal_config.yaml"),
            "-o", str(out_file),
            *[str(f) for f in mini_files],
        ])
        assert result.exit_code == 0, result.output
        assert out_file.exists()

        with open(out_file, encoding="utf-8") as f:
            data = json.load(f)

        assert data["bomFormat"] == "CycloneDX"
        assert data["specVersion"] == "1.6"
        assert data["version"] == 1
        assert data["serialNumber"].startswith("urn:uuid:")
        assert data["metadata"]["component"]["name"] == "qubership-jaeger"
        assert data["metadata"]["component"]["version"] == "1.2.3"
        assert len(data["components"]) == 4
        assert len(data["dependencies"]) > 0

    def test_generate_without_components(self, tmp_path):
        """Generate without mini-manifests — only standalone from config."""
        out_file = tmp_path / "manifest.json"
        runner = CliRunner()
        result = runner.invoke(cli, [
            "generate",
            "-c", str(FIXTURES / "configs/minimal_config.yaml"),
            "-o", str(out_file),
        ])
        assert result.exit_code == 0, result.output
        assert out_file.exists()

        with open(out_file, encoding="utf-8") as f:
            data = json.load(f)

        assert data["metadata"]["component"]["name"] == "qubership-jaeger"
        # Without mini-manifests: only standalone (docker/helm are skipped)
        assert len(data["components"]) == 1

    def test_generate_warns_on_missing_mini_manifest(self, tmp_path):
        """Missing mini-manifest → warning in stderr, exit_code=0."""
        out_file = tmp_path / "manifest.json"
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(cli, [
            "generate",
            "-c", str(FIXTURES / "configs/minimal_config.yaml"),
            "-o", str(out_file),
            # no mini-manifests passed → helm-chart not found
        ])
        assert result.exit_code == 0, result.output
        assert "not found in mini-manifests" in result.stderr
        assert "qubership-jaeger" in result.stderr

    def test_generate_with_version_override(self, tmp_path):
        """Version override via --version."""
        out_file = tmp_path / "manifest.json"
        runner = CliRunner()
        result = runner.invoke(cli, [
            "generate",
            "-c", str(FIXTURES / "configs/minimal_config.yaml"),
            "-o", str(out_file),
            "-v", "9.9.9",
        ])
        assert result.exit_code == 0, result.output

        with open(out_file, encoding="utf-8") as f:
            data = json.load(f)

        assert data["metadata"]["component"]["version"] == "9.9.9"

    def test_generate_with_name_override(self, tmp_path):
        """Name override via --name."""
        out_file = tmp_path / "manifest.json"
        runner = CliRunner()
        result = runner.invoke(cli, [
            "generate",
            "-c", str(FIXTURES / "configs/minimal_config.yaml"),
            "-o", str(out_file),
            "-n", "custom-app",
        ])
        assert result.exit_code == 0, result.output

        with open(out_file, encoding="utf-8") as f:
            data = json.load(f)

        assert data["metadata"]["component"]["name"] == "custom-app"

    def test_output_has_correct_json_keys(self, tmp_path):
        """JSON keys are correct (bom-ref, not bom_ref)."""
        mini_files = _create_mini_manifests(tmp_path, [
            FIXTURES / "metadata/docker_metadata.json",
        ])

        out_file = tmp_path / "manifest.json"
        runner = CliRunner()
        result = runner.invoke(cli, [
            "generate",
            "-c", str(FIXTURES / "configs/minimal_config.yaml"),
            "-o", str(out_file),
            *[str(f) for f in mini_files],
        ])
        assert result.exit_code == 0, result.output

        with open(out_file, encoding="utf-8") as f:
            data = json.load(f)

        assert "$schema" in data
        assert "bomFormat" in data
        assert "specVersion" in data
        assert "serialNumber" in data
        assert "bom-ref" in data["metadata"]["component"]

        comp = data["components"][0]
        assert "bom-ref" in comp
        assert "mime-type" in comp

        dep = data["dependencies"][0]
        assert "dependsOn" in dep

    def test_output_creates_parent_dirs(self, tmp_path):
        """CLI creates parent directories for the output file."""
        out_file = tmp_path / "sub" / "dir" / "manifest.json"
        runner = CliRunner()
        result = runner.invoke(cli, [
            "generate",
            "-c", str(FIXTURES / "configs/minimal_config.yaml"),
            "-o", str(out_file),
        ])
        assert result.exit_code == 0, result.output
        assert out_file.exists()

    def test_generate_with_component_directory(self, tmp_path):
        """Directory of mini-manifests passed as argument."""
        comp_dir = tmp_path / "components"
        comp_dir.mkdir()
        regdef = FIXTURES / "regdefs/qubership_regdef.yml"
        _create_mini_manifests(comp_dir, [
            FIXTURES / "metadata/docker_metadata.json",
            FIXTURES / "metadata/helm_metadata.json",
            FIXTURES / "metadata/envoy_metadata.json",
        ], regdef)

        out_file = tmp_path / "manifest.json"
        runner = CliRunner()
        result = runner.invoke(cli, [
            "generate",
            "-c", str(FIXTURES / "configs/minimal_config.yaml"),
            "-o", str(out_file),
            str(comp_dir),
        ])
        assert result.exit_code == 0, result.output

        with open(out_file, encoding="utf-8") as f:
            data = json.load(f)
        assert len(data["components"]) == 4

    def test_generate_with_mixed_files_and_directory(self, tmp_path):
        """Mixed input: individual file + directory."""
        comp_dir = tmp_path / "components"
        comp_dir.mkdir()
        _create_mini_manifests(comp_dir, [
            FIXTURES / "metadata/helm_metadata.json",
            FIXTURES / "metadata/envoy_metadata.json",
        ])

        docker_mini = _create_mini_manifests(tmp_path, [
            FIXTURES / "metadata/docker_metadata.json",
        ])

        out_file = tmp_path / "manifest.json"
        runner = CliRunner()
        result = runner.invoke(cli, [
            "generate",
            "-c", str(FIXTURES / "configs/minimal_config.yaml"),
            "-o", str(out_file),
            *[str(f) for f in docker_mini],
            str(comp_dir),
        ])
        assert result.exit_code == 0, result.output

        with open(out_file, encoding="utf-8") as f:
            data = json.load(f)
        assert len(data["components"]) == 4


class TestFullPipelineEndToEnd:
    """Full pipeline: component → fetch → generate."""

    def test_full_three_step_pipeline(self, tmp_path):
        """
        Step 1: component — create mini-manifests for docker images.
        Step 2: fetch — pull helm chart (subprocess mocked), write to same dir.
        Step 3: generate — read whole dir and produce the final manifest.
        """
        minis_dir = tmp_path / "minis"
        minis_dir.mkdir()
        runner = CliRunner()

        # Step 1: mini-manifests for docker images
        for meta_file in [
            FIXTURES / "metadata/docker_metadata.json",
            FIXTURES / "metadata/envoy_metadata.json",
        ]:
            out = minis_dir / f"mini_{meta_file.stem}.json"
            result = runner.invoke(cli, [
                "component",
                "-i", str(meta_file),
                "-o", str(out),
            ])
            assert result.exit_code == 0, f"component failed: {result.output}"

        # Step 2: fetch helm chart from config
        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = _fake_helm_run
            result = runner.invoke(cli, [
                "fetch",
                "-c", str(FIXTURES / "configs/minimal_config.yaml"),
                "-o", str(minis_dir),
            ])
        assert result.exit_code == 0, f"fetch failed: {result.output}"
        assert (minis_dir / "qubership-jaeger.json").exists()

        # Step 3: final manifest
        out_manifest = tmp_path / "manifest.json"
        result = runner.invoke(cli, [
            "generate",
            "-c", str(FIXTURES / "configs/minimal_config.yaml"),
            "-o", str(out_manifest),
            str(minis_dir),
        ])
        assert result.exit_code == 0, f"generate failed: {result.output}"
        assert out_manifest.exists()

        with open(out_manifest, encoding="utf-8") as f:
            data = json.load(f)

        print("\n" + json.dumps(data, indent=2, ensure_ascii=False))

        # Save as reference example
        example_path = FIXTURES / "examples/jaeger_manifest.json"
        example_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Basic structure
        assert data["bomFormat"] == "CycloneDX"
        assert data["specVersion"] == "1.6"
        assert data["metadata"]["component"]["name"] == "qubership-jaeger"
        assert data["metadata"]["component"]["version"] == "1.2.3"

        # Components: standalone + helm + jaeger + envoy = 4
        assert len(data["components"]) == 4
        names = {c["name"] for c in data["components"]}
        assert "qubership-jaeger" in names
        assert "jaeger" in names
        assert "envoy" in names

        # Dependencies present
        assert len(data["dependencies"]) > 0

        # JSON keys are correct
        helm_comp = next(c for c in data["components"] if c["name"] == "qubership-jaeger"
                         and c.get("mime-type") == "application/vnd.nc.helm.chart")
        assert "bom-ref" in helm_comp
        assert "purl" in helm_comp
