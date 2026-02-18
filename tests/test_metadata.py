"""Тесты для моделей JSON-метаданных и загрузчика."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app_manifest.models.metadata import ComponentMetadata, HashEntry
from app_manifest.services.metadata_loader import _expand_paths, load_all_metadata, load_component_metadata

FIXTURES = Path(__file__).parent / "fixtures"


class TestHashEntry:
    """Тесты для модели хеша."""

    def test_basic_hash(self):
        h = HashEntry(alg="SHA-256", content="abc123")
        assert h.alg == "SHA-256"
        assert h.content == "abc123"


class TestComponentMetadata:
    """Тесты для модели метаданных компонента."""

    def test_docker_metadata(self):
        """Docker-образ со всеми полями."""
        meta = ComponentMetadata(
            name="jaeger",
            type="container",
            **{"mime-type": "application/vnd.docker.image"},
            group="core",
            version="build3",
            hashes=[{"alg": "SHA-256", "content": "abc"}],
            reference="sandbox.example.com/core/jaeger:build3",
        )
        assert meta.name == "jaeger"
        assert meta.type == "container"
        assert meta.mime_type == "application/vnd.docker.image"
        assert meta.group == "core"
        assert len(meta.hashes) == 1

    def test_helm_metadata_minimal(self):
        """Helm-чарт без group и hashes."""
        meta = ComponentMetadata(
            name="my-chart",
            type="application",
            **{"mime-type": "application/vnd.nc.helm.chart"},
        )
        assert meta.group is None
        assert meta.version is None
        assert meta.hashes == []
        assert meta.reference is None

    def test_missing_name_raises_error(self):
        """Метаданные без имени — ошибка."""
        with pytest.raises(ValidationError):
            ComponentMetadata(
                type="container",
                **{"mime-type": "application/vnd.docker.image"},
            )


class TestMetadataLoader:
    """Тесты для загрузки JSON-файлов."""

    def test_load_docker_metadata(self):
        """Загрузка Docker-метаданных из файла."""
        meta = load_component_metadata(FIXTURES / "metadata/docker_metadata.json")
        assert meta.name == "jaeger"
        assert meta.type == "container"
        assert meta.group == "core"
        assert meta.version == "build3"
        assert len(meta.hashes) == 1
        assert meta.hashes[0].alg == "SHA-256"

    def test_load_helm_metadata(self):
        """Загрузка Helm-метаданных из файла."""
        meta = load_component_metadata(FIXTURES / "metadata/helm_metadata.json")
        assert meta.name == "qubership-jaeger"
        assert meta.type == "application"
        assert meta.reference == "oci://registry.qubership.org/charts/qubership-jaeger:1.2.3"

    def test_load_all_metadata(self):
        """Загрузка нескольких файлов — результат словарь по имени."""
        paths = [
            FIXTURES / "metadata/docker_metadata.json",
            FIXTURES / "metadata/helm_metadata.json",
        ]
        result = load_all_metadata(paths)

        assert len(result) == 2
        assert "jaeger" in result
        assert "qubership-jaeger" in result
        assert result["jaeger"].type == "container"
        assert result["qubership-jaeger"].type == "application"

    def test_load_nonexistent_file_raises_error(self):
        """Несуществующий файл — ошибка."""
        with pytest.raises(FileNotFoundError):
            load_component_metadata(Path("nonexistent.json"))

    def test_load_all_metadata_from_directory(self, tmp_path):
        """Директория вместо файла — загружаются все *.json внутри."""
        import shutil
        shutil.copy(FIXTURES / "metadata/docker_metadata.json", tmp_path / "docker_metadata.json")
        shutil.copy(FIXTURES / "metadata/helm_metadata.json", tmp_path / "helm_metadata.json")

        result = load_all_metadata([tmp_path])

        assert len(result) == 2
        assert "jaeger" in result
        assert "qubership-jaeger" in result

    def test_load_all_metadata_mixed(self, tmp_path):
        """Можно передавать и файлы и директории вместе."""
        import shutil
        shutil.copy(FIXTURES / "metadata/helm_metadata.json", tmp_path / "helm_metadata.json")

        result = load_all_metadata([
            FIXTURES / "metadata/docker_metadata.json",
            tmp_path,
        ])

        assert "jaeger" in result
        assert "qubership-jaeger" in result

    def test_load_all_metadata_empty_directory(self, tmp_path):
        """Пустая директория — пустой результат."""
        result = load_all_metadata([tmp_path])
        assert result == {}


class TestExpandPaths:
    """Тесты для раскрытия путей (файлы и директории)."""

    def test_file_stays_as_is(self):
        """Файл остаётся файлом."""
        paths = [FIXTURES / "metadata/docker_metadata.json"]
        result = _expand_paths(paths)
        assert result == [FIXTURES / "metadata/docker_metadata.json"]

    def test_directory_expands_to_json_files(self, tmp_path):
        """Директория раскрывается в *.json файлы."""
        import shutil
        shutil.copy(FIXTURES / "metadata/docker_metadata.json", tmp_path / "a.json")
        shutil.copy(FIXTURES / "metadata/helm_metadata.json", tmp_path / "b.json")

        result = _expand_paths([tmp_path])
        assert len(result) == 2
        assert all(p.suffix == ".json" for p in result)

    def test_directory_ignores_non_json(self, tmp_path):
        """Не-JSON файлы в директории игнорируются."""
        import shutil
        shutil.copy(FIXTURES / "metadata/docker_metadata.json", tmp_path / "meta.json")
        (tmp_path / "notes.txt").write_text("ignore me")

        result = _expand_paths([tmp_path])
        assert len(result) == 1
        assert result[0].name == "meta.json"

    def test_directory_files_sorted(self, tmp_path):
        """Файлы из директории возвращаются в алфавитном порядке."""
        import shutil
        shutil.copy(FIXTURES / "metadata/helm_metadata.json", tmp_path / "z.json")
        shutil.copy(FIXTURES / "metadata/docker_metadata.json", tmp_path / "a.json")

        result = _expand_paths([tmp_path])
        assert result[0].name == "a.json"
        assert result[1].name == "z.json"

    def test_mixed_files_and_directories(self, tmp_path):
        """Микс файлов и директорий."""
        import shutil
        shutil.copy(FIXTURES / "metadata/helm_metadata.json", tmp_path / "helm.json")

        result = _expand_paths([
            FIXTURES / "metadata/docker_metadata.json",
            tmp_path,
        ])
        assert len(result) == 2
