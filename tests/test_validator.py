"""Tests for the --validate flag and the validator service."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from app_manifest.cli import cli
from app_manifest.services.validator import validate_manifest

FIXTURES = Path(__file__).parent / "fixtures"


# ─── Tests for the validate_manifest service ─────────────────────


class TestValidateManifest:
    def _minimal_valid(self) -> dict:
        """Minimal valid manifest."""
        return {
            "$schema": "../schemas/application-manifest.schema.json",
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "serialNumber": "urn:uuid:c7eb7c5f-b8da-4c05-9c48-678a11c00a35",
            "version": 1,
            "metadata": {
                "timestamp": "2025-01-21T12:00:00Z",
                "component": {
                    "bom-ref": "app:abc",
                    "type": "application",
                    "mime-type": "application/vnd.nc.application",
                    "name": "my-app",
                    "version": "1.0.0",
                },
                "tools": {
                    "components": [
                        {"type": "application", "name": "am-build-cli", "version": "0.1.0"}
                    ]
                },
            },
            "components": [],
            "dependencies": [],
        }

    def test_valid_empty_manifest(self):
        errors = validate_manifest(self._minimal_valid())
        assert errors == []

    def test_missing_required_field(self):
        manifest = self._minimal_valid()
        del manifest["bomFormat"]
        errors = validate_manifest(manifest)
        assert any("bomFormat" in e for e in errors)

    def test_wrong_bom_format(self):
        manifest = self._minimal_valid()
        manifest["bomFormat"] = "SPDX"
        errors = validate_manifest(manifest)
        assert len(errors) > 0

    def test_invalid_serial_number(self):
        manifest = self._minimal_valid()
        manifest["serialNumber"] = "not-a-uuid"
        errors = validate_manifest(manifest)
        assert len(errors) > 0

    def test_invalid_hash_content(self):
        manifest = self._minimal_valid()
        manifest["components"] = [{
            "bom-ref": "img:1",
            "type": "container",
            "mime-type": "application/vnd.docker.image",
            "group": "core",
            "name": "my-image",
            "version": "1.0",
            "purl": "pkg:docker/core/my-image@1.0",
            "hashes": [{"alg": "SHA-256", "content": "not-a-valid-hash"}],
        }]
        errors = validate_manifest(manifest)
        assert len(errors) > 0

    def test_valid_with_docker_image(self):
        manifest = self._minimal_valid()
        manifest["components"] = [{
            "bom-ref": "img:abc123",
            "type": "container",
            "mime-type": "application/vnd.docker.image",
            "group": "core",
            "name": "my-image",
            "version": "1.0.0",
            "purl": "pkg:docker/core/my-image@1.0.0?registry_name=sandbox",
        }]
        errors = validate_manifest(manifest)
        assert errors == []

    def test_valid_with_helm_chart(self):
        manifest = self._minimal_valid()
        manifest["components"] = [{
            "bom-ref": "chart:abc",
            "type": "application",
            "mime-type": "application/vnd.nc.helm.chart",
            "name": "my-chart",
            "version": "1.0.0",
            "properties": [{"name": "isLibrary", "value": False}],
            "components": [],
        }]
        errors = validate_manifest(manifest)
        assert errors == []

    def test_example_jaeger_manifest_is_valid(self):
        """The reference Jaeger example must pass validation."""
        example = FIXTURES / "examples/jaeger_manifest.json"
        if not example.exists():
            pytest.skip("example_jaeger_manifest.json not generated yet")
        data = json.loads(example.read_text(encoding="utf-8"))
        errors = validate_manifest(data)
        assert errors == [], f"Validation errors: {errors}"


# ─── Tests for the --validate CLI flag ────────────────────────────


def _fake_helm_run(cmd, **kwargs):
    """Mock subprocess.run for helm pull."""
    import io, tarfile, yaml
    dest = Path(cmd[cmd.index("--destination") + 1])
    ref = next(a for a in cmd if a.startswith("oci://"))
    parts = ref.replace("oci://", "").split(":")
    version = parts[-1] if len(parts) > 1 else "1.0.0"
    name = parts[0].split("/")[-1]

    tgz_path = dest / f"{name}-{version}.tgz"
    with tarfile.open(tgz_path, "w:gz") as tar:
        chart_yaml_bytes = yaml.dump({
            "apiVersion": "v2", "name": name,
            "version": version, "appVersion": version,
        }).encode("utf-8")
        info = tarfile.TarInfo(name=f"{name}/Chart.yaml")
        info.size = len(chart_yaml_bytes)
        tar.addfile(info, io.BytesIO(chart_yaml_bytes))

        schema = json.dumps({"type": "object"}).encode("utf-8")
        info = tarfile.TarInfo(name=f"{name}/values.schema.json")
        info.size = len(schema)
        tar.addfile(info, io.BytesIO(schema))

    return MagicMock(returncode=0, stderr="")


class TestValidateCLIFlag:
    def test_validate_flag_in_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["generate", "--help"])
        assert "--validate" in result.output

    def test_generate_without_validate_flag(self, tmp_path):
        """Without --validate, the file is created and no validation message is shown."""
        out_file = tmp_path / "manifest.json"
        runner = CliRunner()
        result = runner.invoke(cli, [
            "generate",
            "-c", str(FIXTURES / "configs/minimal_config.yaml"),
            "-o", str(out_file),
        ])
        assert result.exit_code == 0
        assert "Manifest is valid." not in result.output

    def test_generate_with_validate_passes(self, tmp_path):
        """With --validate, a valid manifest → 'Manifest is valid.'"""
        minis_dir = tmp_path / "minis"
        minis_dir.mkdir()
        runner = CliRunner()

        # Build mini-manifests for docker images
        for meta_file in [FIXTURES / "metadata/docker_metadata.json", FIXTURES / "metadata/envoy_metadata.json"]:
            out = minis_dir / f"mini_{meta_file.stem}.json"
            runner.invoke(cli, ["component", "-i", str(meta_file), "-o", str(out)])

        # Fetch helm chart
        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = _fake_helm_run
            runner.invoke(cli, [
                "fetch", "-c", str(FIXTURES / "configs/minimal_config.yaml"), "-o", str(minis_dir),
            ])

        out_file = tmp_path / "manifest.json"
        result = runner.invoke(cli, [
            "generate",
            "-c", str(FIXTURES / "configs/minimal_config.yaml"),
            "-o", str(out_file),
            "--validate",
            str(minis_dir),
        ])

        assert result.exit_code == 0, result.output
        assert "Manifest is valid." in result.output

    def test_generate_with_validate_fails_on_bad_manifest(self, tmp_path):
        """If the manifest is invalid after writing — exit code != 0."""
        out_file = tmp_path / "manifest.json"

        # Write a deliberately invalid JSON
        out_file.write_text('{"bomFormat": "WRONG"}', encoding="utf-8")

        # Patch _write_output so it does not overwrite our file
        with patch("app_manifest.cli.validate_manifest") as mock_validate:
            mock_validate.return_value = ["root: 'bomFormat' is not valid"]

            runner = CliRunner()
            result = runner.invoke(cli, [
                "generate",
                "-c", str(FIXTURES / "configs/minimal_config.yaml"),
                "-o", str(out_file),
                "--validate",
            ])

        assert result.exit_code != 0
        assert "Validation FAILED" in result.output or "does not conform" in result.output


# ─── Tests for the validate command ─────────────────────────────


class TestValidateCommand:
    def test_validate_command_in_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "validate" in result.output

    def test_validate_valid_manifest(self, tmp_path):
        """Valid manifest → exit code 0 and 'is valid' message."""
        manifest = {
            "$schema": "../schemas/application-manifest.schema.json",
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "serialNumber": "urn:uuid:c7eb7c5f-b8da-4c05-9c48-678a11c00a35",
            "version": 1,
            "metadata": {
                "timestamp": "2025-01-21T12:00:00Z",
                "component": {
                    "bom-ref": "app:abc",
                    "type": "application",
                    "mime-type": "application/vnd.nc.application",
                    "name": "my-app",
                    "version": "1.0.0",
                },
                "tools": {
                    "components": [
                        {"type": "application", "name": "am-build-cli", "version": "0.1.0"}
                    ]
                },
            },
            "components": [],
            "dependencies": [],
        }
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(manifest), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(cli, ["validate", "-i", str(manifest_file)])

        assert result.exit_code == 0
        assert "is valid" in result.output

    def test_validate_invalid_manifest(self, tmp_path):
        """Invalid manifest → exit code != 0 and 'FAILED' message."""
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text('{"bomFormat": "WRONG"}', encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(cli, ["validate", "-i", str(manifest_file)])

        assert result.exit_code != 0
        assert "FAILED" in result.output or "does not conform" in result.output

    def test_validate_invalid_json(self, tmp_path):
        """Invalid JSON → exit code != 0."""
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text("{not valid json", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(cli, ["validate", "-i", str(manifest_file)])

        assert result.exit_code != 0

    def test_validate_example_jaeger_manifest(self):
        """The reference Jaeger example must pass validation."""
        example = FIXTURES / "examples/jaeger_manifest.json"
        if not example.exists():
            pytest.skip("example_jaeger_manifest.json not generated yet")

        runner = CliRunner()
        result = runner.invoke(cli, ["validate", "-i", str(example)])

        assert result.exit_code == 0, result.output
        assert "is valid" in result.output
