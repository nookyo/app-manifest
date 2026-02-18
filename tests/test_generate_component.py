"""Тесты для команды component и сервиса component_builder."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from app_manifest.cli import cli
from app_manifest.models.metadata import ComponentMetadata
from app_manifest.services.component_builder import build_component_manifest

FIXTURES = Path(__file__).parent / "fixtures"


# ─── Тесты сервиса component_builder ───────────────────────────


class TestBuildDockerComponent:
    """Мини-манифест для Docker-образа."""

    def test_docker_basic_fields(self):
        """Основные поля Docker-компонента."""
        meta = ComponentMetadata.model_validate({
            "name": "jaeger",
            "type": "container",
            "mime-type": "application/vnd.docker.image",
            "group": "core",
            "version": "build3",
            "hashes": [{"alg": "SHA-256", "content": "abc123"}],
            "reference": "sandbox.example.com/core/jaeger:build3",
        })
        bom = build_component_manifest(meta)
        data = bom.model_dump(by_alias=True, exclude_none=True)

        assert data["bomFormat"] == "CycloneDX"
        assert data["specVersion"] == "1.6"
        assert len(data["components"]) == 1
        assert data["dependencies"] == []

        comp = data["components"][0]
        assert comp["name"] == "jaeger"
        assert comp["type"] == "container"
        assert comp["mime-type"] == "application/vnd.docker.image"
        assert comp["group"] == "core"
        assert comp["version"] == "build3"
        assert "bom-ref" in comp
        assert comp["bom-ref"].startswith("jaeger:")

    def test_docker_purl_without_regdef(self):
        """PURL генерируется с хостом как registry_name."""
        meta = ComponentMetadata.model_validate({
            "name": "jaeger",
            "type": "container",
            "mime-type": "application/vnd.docker.image",
            "reference": "sandbox.example.com/core/jaeger:build3",
        })
        bom = build_component_manifest(meta)
        comp = bom.model_dump(by_alias=True, exclude_none=True)["components"][0]

        assert comp["purl"] == "pkg:docker/core/jaeger@build3?registry_name=sandbox.example.com"

    def test_docker_purl_with_regdef(self):
        """PURL с regdef — registry_name из regdef."""
        from app_manifest.services.regdef_loader import load_registry_definition

        meta = ComponentMetadata.model_validate({
            "name": "jaeger",
            "type": "container",
            "mime-type": "application/vnd.docker.image",
            "group": "core",
            "reference": "ghcr.io/netcracker/jaeger:build3",
        })
        regdef = load_registry_definition(FIXTURES / "regdefs/qubership_regdef.yml")
        bom = build_component_manifest(meta, regdef)
        comp = bom.model_dump(by_alias=True, exclude_none=True)["components"][0]

        assert comp["purl"] == "pkg:docker/netcracker/jaeger@build3?registry_name=qubership"

    def test_docker_hashes(self):
        """Хеши передаются в мини-манифест."""
        meta = ComponentMetadata.model_validate({
            "name": "jaeger",
            "type": "container",
            "mime-type": "application/vnd.docker.image",
            "hashes": [
                {"alg": "SHA-256", "content": "aaa"},
                {"alg": "SHA-512", "content": "bbb"},
            ],
        })
        bom = build_component_manifest(meta)
        comp = bom.model_dump(by_alias=True, exclude_none=True)["components"][0]

        assert len(comp["hashes"]) == 2
        assert comp["hashes"][0] == {"alg": "SHA-256", "content": "aaa"}

    def test_docker_without_reference_no_purl(self):
        """Без reference — нет PURL."""
        meta = ComponentMetadata.model_validate({
            "name": "jaeger",
            "type": "container",
            "mime-type": "application/vnd.docker.image",
        })
        bom = build_component_manifest(meta)
        comp = bom.model_dump(by_alias=True, exclude_none=True)["components"][0]

        assert "purl" not in comp


class TestBuildHelmComponent:
    """Мини-манифест для Helm-чарта."""

    def test_helm_basic_fields(self):
        """Основные поля Helm-компонента."""
        meta = ComponentMetadata.model_validate({
            "name": "qubership-jaeger",
            "type": "application",
            "mime-type": "application/vnd.nc.helm.chart",
            "version": "1.2.3",
            "appVersion": "1.2.3",
            "reference": "oci://registry.qubership.org/charts/qubership-jaeger:1.2.3",
        })
        bom = build_component_manifest(meta)
        comp = bom.model_dump(by_alias=True, exclude_none=True)["components"][0]

        assert comp["name"] == "qubership-jaeger"
        assert comp["type"] == "application"
        assert comp["mime-type"] == "application/vnd.nc.helm.chart"
        assert comp["version"] == "1.2.3"
        assert comp["bom-ref"].startswith("qubership-jaeger:")

    def test_helm_purl_with_regdef(self):
        """PURL для Helm с regdef."""
        from app_manifest.services.regdef_loader import load_registry_definition

        meta = ComponentMetadata.model_validate({
            "name": "qubership-jaeger",
            "type": "application",
            "mime-type": "application/vnd.nc.helm.chart",
            "reference": "oci://registry.qubership.org/charts/qubership-jaeger:1.2.3",
        })
        regdef = load_registry_definition(FIXTURES / "regdefs/qubership_regdef.yml")
        bom = build_component_manifest(meta, regdef)
        comp = bom.model_dump(by_alias=True, exclude_none=True)["components"][0]

        assert comp["purl"] == "pkg:helm/charts/qubership-jaeger@1.2.3?registry_name=qubership"

    def test_helm_version_from_app_version(self):
        """appVersion приоритетнее version."""
        meta = ComponentMetadata.model_validate({
            "name": "my-chart",
            "type": "application",
            "mime-type": "application/vnd.nc.helm.chart",
            "version": "0.1.0",
            "appVersion": "2.0.0",
        })
        bom = build_component_manifest(meta)
        comp = bom.model_dump(by_alias=True, exclude_none=True)["components"][0]

        assert comp["version"] == "2.0.0"

    def test_helm_nested_components(self):
        """Вложенные компоненты (values.schema.json, resource-profiles)."""
        with open(FIXTURES / "metadata/helm_metadata.json") as f:
            raw = json.load(f)
        meta = ComponentMetadata.model_validate(raw)
        bom = build_component_manifest(meta)
        comp = bom.model_dump(by_alias=True, exclude_none=True)["components"][0]

        assert "components" in comp
        assert len(comp["components"]) == 2

        # values.schema.json
        schema_comp = comp["components"][0]
        assert schema_comp["name"] == "values.schema.json"
        assert schema_comp["type"] == "data"
        assert schema_comp["mime-type"] == "application/vnd.nc.helm.values.schema"
        assert len(schema_comp["data"]) == 1

        # resource-profile-baselines
        profiles_comp = comp["components"][1]
        assert profiles_comp["name"] == "resource-profile-baselines"
        assert len(profiles_comp["data"]) == 2

    def test_helm_without_nested_components(self):
        """Helm без вложенных компонентов."""
        meta = ComponentMetadata.model_validate({
            "name": "simple-chart",
            "type": "application",
            "mime-type": "application/vnd.nc.helm.chart",
            "version": "1.0.0",
        })
        bom = build_component_manifest(meta)
        comp = bom.model_dump(by_alias=True, exclude_none=True)["components"][0]

        assert "components" not in comp


class TestMiniManifestStructure:
    """Общие проверки структуры мини-манифеста."""

    def test_has_metadata_section(self):
        """Мини-манифест содержит metadata с tool info."""
        meta = ComponentMetadata.model_validate({
            "name": "test",
            "type": "container",
            "mime-type": "application/vnd.docker.image",
        })
        bom = build_component_manifest(meta)
        data = bom.model_dump(by_alias=True, exclude_none=True)

        assert "metadata" in data
        assert data["metadata"]["component"]["name"] == "am-build-cli"
        assert data["metadata"]["tools"]["components"][0]["name"] == "am-build-cli"

    def test_serial_number_is_urn_uuid(self):
        """serialNumber в формате urn:uuid:..."""
        meta = ComponentMetadata.model_validate({
            "name": "test",
            "type": "container",
            "mime-type": "application/vnd.docker.image",
        })
        bom = build_component_manifest(meta)
        data = bom.model_dump(by_alias=True, exclude_none=True)

        assert data["serialNumber"].startswith("urn:uuid:")


# ─── Тесты CLI команды component ──────────────────────


class TestGenerateComponentCLI:
    """Сквозные тесты CLI команды component."""

    def test_help(self):
        """Справка отображается."""
        runner = CliRunner()
        result = runner.invoke(cli, ["component", "--help"])
        assert result.exit_code == 0
        assert "--input" in result.output
        assert "--out" in result.output
        assert "--registry-def" in result.output

    def test_docker_metadata(self, tmp_path):
        """Генерация мини-манифеста для Docker."""
        out_file = tmp_path / "component.json"
        runner = CliRunner()
        result = runner.invoke(cli, [
            "component",
            "-i", str(FIXTURES / "metadata/docker_metadata.json"),
            "-o", str(out_file),
        ])
        assert result.exit_code == 0, result.output
        assert out_file.exists()

        with open(out_file, encoding="utf-8") as f:
            data = json.load(f)

        assert data["bomFormat"] == "CycloneDX"
        assert len(data["components"]) == 1
        assert data["components"][0]["name"] == "jaeger"
        assert data["components"][0]["type"] == "container"
        assert "purl" in data["components"][0]

    def test_helm_metadata_with_regdef(self, tmp_path):
        """Генерация мини-манифеста для Helm с regdef."""
        out_file = tmp_path / "component.json"
        runner = CliRunner()
        result = runner.invoke(cli, [
            "component",
            "-i", str(FIXTURES / "metadata/helm_metadata.json"),
            "-r", str(FIXTURES / "regdefs/qubership_regdef.yml"),
            "-o", str(out_file),
        ])
        assert result.exit_code == 0, result.output

        with open(out_file, encoding="utf-8") as f:
            data = json.load(f)

        comp = data["components"][0]
        assert comp["name"] == "qubership-jaeger"
        assert "registry_name=qubership" in comp["purl"]
        assert len(comp["components"]) == 2

    def test_helm_metadata_without_regdef(self, tmp_path):
        """Генерация мини-манифеста для Helm без regdef — хост как registry_name."""
        out_file = tmp_path / "component.json"
        runner = CliRunner()
        result = runner.invoke(cli, [
            "component",
            "-i", str(FIXTURES / "metadata/helm_metadata.json"),
            "-o", str(out_file),
        ])
        assert result.exit_code == 0, result.output

        with open(out_file, encoding="utf-8") as f:
            data = json.load(f)

        comp = data["components"][0]
        assert "registry_name=registry.qubership.org" in comp["purl"]

    def test_creates_parent_dirs(self, tmp_path):
        """Создаёт родительские директории для output файла."""
        out_file = tmp_path / "sub" / "dir" / "component.json"
        runner = CliRunner()
        result = runner.invoke(cli, [
            "component",
            "-i", str(FIXTURES / "metadata/docker_metadata.json"),
            "-o", str(out_file),
        ])
        assert result.exit_code == 0, result.output
        assert out_file.exists()

    def test_shows_in_root_help(self):
        """Команда component видна в корневой справке."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "component" in result.output
