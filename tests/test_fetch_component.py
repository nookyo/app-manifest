"""Тесты для команды fetch и сервиса artifact_fetcher.

Реальный helm pull не вызывается — мокируем subprocess
и создаём фейковый .tgz с Chart.yaml и values.schema.json.
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
    """Создать фейковый .tgz Helm-чарта для тестов."""
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
    """Мок для helm pull — создаёт фейковый .tgz."""
    ref = reference.replace("oci://", "")
    parts = ref.split(":")
    version = parts[-1] if len(parts) > 1 else "1.0.0"
    name = parts[0].split("/")[-1]

    _create_fake_chart_tgz(dest, chart_name=name, version=version, app_version=version)


# ─── Тесты утилит ──────────────────────────────────────────


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


# ─── Тесты fetch_helm_component с моком ────────────────────


class TestFetchHelmComponent:
    def _mock_subprocess(self, reference, tmp_path):
        """Замоканный subprocess.run для helm pull."""
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
        assert "registry_name=qubership" in comp.purl

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


# ─── Тесты fetch_components_from_config ────────────────────


class TestFetchFromConfig:
    def _fake_run(self, cmd, **kwargs):
        dest = Path(cmd[cmd.index("--destination") + 1])
        ref = next(a for a in cmd if a.startswith("oci://"))
        _mock_helm_pull(ref, dest)
        return MagicMock(returncode=0, stderr="")

    def test_fetches_helm_and_docker_with_reference(self, tmp_path):
        """fetch_components_from_config обрабатывает helm-чарты и docker-образы с reference."""
        from app_manifest.services.config_loader import load_build_config
        config = load_build_config(FIXTURES / "configs/minimal_config.yaml")

        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = self._fake_run
            results = fetch_components_from_config(config)

        # minimal_config.yaml: 1 helm + 2 docker (все с reference)
        assert len(results) == 3
        names = [name for name, _ in results]
        assert "qubership-jaeger" in names
        assert "jaeger" in names
        assert "envoy" in names

    def test_skips_components_without_reference(self, tmp_path):
        """Компоненты без reference (standalone) не попадают в результат."""
        from app_manifest.services.config_loader import load_build_config
        config = load_build_config(FIXTURES / "configs/minimal_config.yaml")

        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = self._fake_run
            results = fetch_components_from_config(config)

        names = [name for name, _ in results]
        assert "qubership-jaeger" in names  # helm + docker — оба в результатах
        # standalone-runnable не имеет reference → пропущен
        mime_types_of_standalones = [
            bom.components[0].mime_type
            for _, bom in results
            if bom.components[0].mime_type == "application/vnd.nc.standalone-runnable"
        ]
        assert mime_types_of_standalones == []


# ─── Тесты CLI ─────────────────────────────────────────────


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
        # Helm-чарт
        helm_file = out_dir / "qubership-jaeger.json"
        assert helm_file.exists()
        with open(helm_file, encoding="utf-8") as f:
            data = json.load(f)
        assert data["bomFormat"] == "CycloneDX"
        comp = data["components"][0]
        assert comp["name"] == "qubership-jaeger"
        assert "purl" in comp

        # Docker-образы (из reference в конфиге)
        assert (out_dir / "jaeger.json").exists()
        assert (out_dir / "envoy.json").exists()
        with open(out_dir / "envoy.json", encoding="utf-8") as f:
            envoy_data = json.load(f)
        assert envoy_data["components"][0]["name"] == "envoy"
        assert "hashes" not in envoy_data["components"][0]  # нет хеша без скачивания

    def test_no_helm_references_in_config(self, tmp_path):
        """Конфиг без helm reference — выводим сообщение, выходим 0."""
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


# ─── Тесты исправлений ──────────────────────────────────────


class TestHelmComponentsFieldAlwaysPresent:
    """Фикс: components[] в helm-chart всегда присутствует (даже пустой).

    Без values.schema.json и resource-profiles поле не должно быть None,
    иначе оно будет опущено при сериализации и манифест не пройдёт валидацию.
    """

    def test_components_is_list_when_no_nested_artifacts(self, tmp_path):
        """Чарт без values.schema.json и resource-profiles → components=[]."""
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
        """Сериализованный JSON содержит 'components': [] даже для пустого чарта."""
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
    """Фикс: при дублирующихся именах компонентов используется vendor-суффикс из mimeType."""

    def test_duplicate_name_uses_vendor_suffix(self, tmp_path):
        """Если два helm-компонента имеют одинаковый name — каждый получает
        уникальное имя файла с вендор-суффиксом из mimeType, и в stderr warning."""
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
                    "mimeType": "application/vnd.qubership.helm.chart",
                    "reference": "oci://registry.example.com/charts/other-chart:2.0.0",
                },
            ],
        }))
        out_dir = tmp_path / "output"

        def fake_run(cmd, **kwargs):
            dest = Path(cmd[cmd.index("--destination") + 1])
            ref = next(a for a in cmd if a.startswith("oci://"))
            _mock_helm_pull(ref, dest)
            return MagicMock(returncode=0, stderr="")

        runner = CliRunner(mix_stderr=False)
        with patch("app_manifest.services.artifact_fetcher.subprocess.run") as mock_run:
            mock_run.side_effect = fake_run
            result = runner.invoke(cli, [
                "fetch",
                "-c", str(config_path),
                "-o", str(out_dir),
            ])

        assert result.exit_code == 0, result.output
        # Оба файла созданы с уникальными именами
        assert (out_dir / "my-chart_nc.json").exists()
        assert (out_dir / "my-chart_qubership.json").exists()
        # Нет файла без суффикса
        assert not (out_dir / "my-chart.json").exists()
        # Warning в stderr
        assert "duplicate" in result.stderr
        assert "my-chart" in result.stderr


# ─── Тесты Docker из reference ──────────────────────────────


class TestFetchDockerFromReference:
    """Тесты для fetch_docker_component_from_reference."""

    def _make_docker_config(self, name: str, reference: str, mime_type: str = "application/vnd.docker.image"):
        from app_manifest.models.config import ComponentConfig, MimeType
        return ComponentConfig(
            name=name,
            mime_type=MimeType(mime_type),
            reference=reference,
        )

    def test_basic_docker_from_reference(self):
        comp = self._make_docker_config("envoy", "docker.io/envoyproxy/envoy:v1.32.6")
        bom = fetch_docker_component_from_reference(comp)

        assert len(bom.components) == 1
        c = bom.components[0]
        assert c.name == "envoy"           # имя из конфига, не из reference
        assert c.version == "v1.32.6"
        assert c.group == "envoyproxy"
        assert c.type == "container"
        assert c.mime_type == "application/vnd.docker.image"
        assert c.hashes is None            # хеш не известен без скачивания
        assert c.purl is not None
        assert "envoyproxy/envoy" in c.purl
        assert "v1.32.6" in c.purl

    def test_name_comes_from_config_not_reference(self):
        """name в компоненте берётся из конфига, а не из reference — чтобы generate мог сопоставить."""
        comp = self._make_docker_config("my-service", "registry.example.com/team/actual-image-name:1.0")
        bom = fetch_docker_component_from_reference(comp)

        assert bom.components[0].name == "my-service"  # из конфига

    def test_purl_with_registry_host(self):
        comp = self._make_docker_config("jaeger", "sandbox.example.com/core/jaeger:build3")
        bom = fetch_docker_component_from_reference(comp)

        c = bom.components[0]
        assert "registry_name=sandbox.example.com" in c.purl

    def test_purl_with_regdef(self):
        from app_manifest.services.regdef_loader import load_registry_definition
        regdef = load_registry_definition(FIXTURES / "regdefs/sandbox_regdef.yml")

        comp = self._make_docker_config("jaeger", "sandbox.example.com/core/jaeger:build3")
        bom = fetch_docker_component_from_reference(comp, regdef)

        c = bom.components[0]
        # registry_name должен быть именем из regdef, а не хостом
        assert "sandbox.example.com" not in c.purl or "registry_name=" in c.purl

    def test_valid_bom_structure(self):
        comp = self._make_docker_config("envoy", "docker.io/envoyproxy/envoy:v1.32.6")
        bom = fetch_docker_component_from_reference(comp)

        assert bom.metadata is not None
        assert bom.metadata.timestamp is not None
        assert bom.metadata.tools is not None
        assert bom.dependencies == []

    def test_serialization_no_hashes_field(self):
        """Сериализованный JSON не содержит поле hashes (нет хеша)."""
        comp = self._make_docker_config("envoy", "docker.io/envoyproxy/envoy:v1.32.6")
        bom = fetch_docker_component_from_reference(comp)

        data = bom.model_dump(by_alias=True, exclude_none=True)
        c = data["components"][0]
        assert "hashes" not in c
        assert "purl" in c
        assert "version" in c
