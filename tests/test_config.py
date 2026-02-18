"""Тесты для моделей YAML-конфига и загрузчика."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app_manifest.models.config import BuildConfig, ComponentConfig, DependencyConfig, MimeType
from app_manifest.services.config_loader import load_build_config

# Путь к папке с тестовыми файлами
FIXTURES = Path(__file__).parent / "fixtures"


class TestMimeType:
    """Тесты для перечисления MimeType."""

    def test_valid_mime_types(self):
        """Все три основных типа должны быть в enum."""
        assert MimeType.STANDALONE_RUNNABLE == "application/vnd.nc.standalone-runnable"
        assert MimeType.DOCKER_IMAGE == "application/vnd.docker.image"
        assert MimeType.HELM_CHART == "application/vnd.nc.helm.chart"

    def test_invalid_mime_type_rejected(self):
        """Неизвестный mimeType должен вызвать ошибку."""
        with pytest.raises(ValidationError):
            ComponentConfig(
                name="test",
                mimeType="application/vnd.nc.unknown",
            )


class TestDependencyConfig:
    """Тесты для модели зависимости."""

    def test_basic_dependency(self):
        """Простая зависимость без valuesPathPrefix."""
        dep = DependencyConfig(
            name="jaeger",
            mimeType="application/vnd.docker.image",
        )
        assert dep.name == "jaeger"
        assert dep.mime_type == MimeType.DOCKER_IMAGE
        assert dep.values_path_prefix is None

    def test_dependency_with_values_path(self):
        """Зависимость с valuesPathPrefix."""
        dep = DependencyConfig(
            name="jaeger",
            mimeType="application/vnd.docker.image",
            valuesPathPrefix="images.jaeger",
        )
        assert dep.values_path_prefix == "images.jaeger"


class TestComponentConfig:
    """Тесты для модели компонента."""

    def test_minimal_component(self):
        """Компонент с минимумом полей — без reference и dependsOn."""
        comp = ComponentConfig(
            name="my-service",
            mimeType="application/vnd.docker.image",
        )
        assert comp.name == "my-service"
        assert comp.mime_type == MimeType.DOCKER_IMAGE
        assert comp.reference is None
        assert comp.depends_on == []

    def test_component_with_dependencies(self):
        """Компонент с зависимостями."""
        comp = ComponentConfig(
            name="my-chart",
            mimeType="application/vnd.nc.helm.chart",
            reference="oci://registry/repo/chart:1.0",
            dependsOn=[
                {"name": "img1", "mimeType": "application/vnd.docker.image"},
            ],
        )
        assert len(comp.depends_on) == 1
        assert comp.depends_on[0].name == "img1"

    def test_missing_name_raises_error(self):
        """Компонент без имени — ошибка."""
        with pytest.raises(ValidationError):
            ComponentConfig(mimeType="application/vnd.docker.image")


class TestBuildConfig:
    """Тесты для корневой модели конфига."""

    def test_minimal_config(self):
        """Минимальный конфиг с одним компонентом."""
        config = BuildConfig(
            applicationVersion="1.0.0",
            applicationName="test-app",
            components=[
                {"name": "svc", "mimeType": "application/vnd.docker.image"},
            ],
        )
        assert config.application_name == "test-app"
        assert config.application_version == "1.0.0"
        assert len(config.components) == 1

    def test_missing_version_raises_error(self):
        """Конфиг без версии — ошибка."""
        with pytest.raises(ValidationError):
            BuildConfig(
                applicationName="test",
                components=[],
            )

    def test_missing_name_raises_error(self):
        """Конфиг без имени — ошибка."""
        with pytest.raises(ValidationError):
            BuildConfig(
                applicationVersion="1.0",
                components=[],
            )


class TestConfigLoader:
    """Тесты для загрузки YAML-файла."""

    def test_load_minimal_config(self):
        """Загрузка тестового YAML-файла."""
        config = load_build_config(FIXTURES / "configs/minimal_config.yaml")

        assert config.application_name == "qubership-jaeger"
        assert config.application_version == "1.2.3"
        assert len(config.components) == 4

    def test_standalone_component(self):
        """Проверяем standalone-runnable компонент из YAML."""
        config = load_build_config(FIXTURES / "configs/minimal_config.yaml")

        standalone = config.components[0]
        assert standalone.name == "qubership-jaeger"
        assert standalone.mime_type == MimeType.STANDALONE_RUNNABLE
        assert len(standalone.depends_on) == 1
        assert standalone.depends_on[0].mime_type == MimeType.HELM_CHART

    def test_helm_component_with_deps(self):
        """Проверяем helm-chart компонент с зависимостями."""
        config = load_build_config(FIXTURES / "configs/minimal_config.yaml")

        helm = config.components[1]
        assert helm.mime_type == MimeType.HELM_CHART
        assert helm.reference == "oci://sandbox.example.com/charts/qubership-jaeger:1.2.3"
        assert len(helm.depends_on) == 2
        assert helm.depends_on[0].values_path_prefix == "images.jaeger"

    def test_load_nonexistent_file_raises_error(self):
        """Несуществующий файл — ошибка."""
        with pytest.raises(FileNotFoundError):
            load_build_config(Path("nonexistent.yaml"))
