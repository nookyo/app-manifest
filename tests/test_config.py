"""Tests for YAML build config models and loader."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app_manifest.models.config import BuildConfig, ComponentConfig, DependencyConfig, MimeType
from app_manifest.services.config_loader import load_build_config

FIXTURES = Path(__file__).parent / "fixtures"


class TestMimeType:
    """Tests for the MimeType enum."""

    def test_valid_mime_types(self):
        """Core mime types must be present in the enum."""
        assert MimeType.STANDALONE_RUNNABLE == "application/vnd.nc.standalone-runnable"
        assert MimeType.DOCKER_IMAGE == "application/vnd.docker.image"
        assert MimeType.HELM_CHART == "application/vnd.nc.helm.chart"

    def test_invalid_mime_type_rejected(self):
        """Unknown mimeType must raise a validation error."""
        with pytest.raises((ValidationError, ValueError)):
            ComponentConfig.model_validate({
                "name": "test",
                "mimeType": "application/vnd.nc.unknown",
            })


class TestDependencyConfig:
    """Tests for the dependency model."""

    def test_basic_dependency(self):
        """Simple dependency without valuesPathPrefix."""
        dep = DependencyConfig(
            name="jaeger",
            mimeType=MimeType.DOCKER_IMAGE,
        )
        assert dep.name == "jaeger"
        assert dep.mime_type == MimeType.DOCKER_IMAGE
        assert dep.values_path_prefix is None

    def test_dependency_with_values_path(self):
        """Dependency with valuesPathPrefix."""
        dep = DependencyConfig(
            name="jaeger",
            mimeType=MimeType.DOCKER_IMAGE,
            valuesPathPrefix="images.jaeger",
        )
        assert dep.values_path_prefix == "images.jaeger"


class TestComponentConfig:
    """Tests for the component model."""

    def test_minimal_component(self):
        """Component with minimal fields — no reference or dependsOn."""
        comp = ComponentConfig(
            name="my-service",
            mimeType=MimeType.DOCKER_IMAGE,
        )
        assert comp.name == "my-service"
        assert comp.mime_type == MimeType.DOCKER_IMAGE
        assert comp.reference is None
        assert comp.depends_on == []

    def test_component_with_dependencies(self):
        """Component with dependencies."""
        comp = ComponentConfig(
            name="my-chart",
            mimeType=MimeType.HELM_CHART,
            reference="oci://registry/repo/chart:1.0",
            dependsOn=[
                DependencyConfig(name="img1", mimeType=MimeType.DOCKER_IMAGE),
            ],
        )
        assert len(comp.depends_on) == 1
        assert comp.depends_on[0].name == "img1"

    def test_missing_name_raises_error(self):
        """Component without name raises an error."""
        with pytest.raises(ValidationError):
            ComponentConfig(mimeType=MimeType.DOCKER_IMAGE)  # type: ignore[call-arg]


class TestBuildConfig:
    """Tests for the root build config model."""

    def test_minimal_config(self):
        """Minimal config with one component."""
        config = BuildConfig(
            applicationVersion="1.0.0",
            applicationName="test-app",
            components=[
                ComponentConfig(name="svc", mimeType=MimeType.DOCKER_IMAGE),
            ],
        )
        assert config.application_name == "test-app"
        assert config.application_version == "1.0.0"
        assert len(config.components) == 1

    def test_missing_version_raises_error(self):
        """Config without version raises an error."""
        with pytest.raises(ValidationError):
            BuildConfig(  # type: ignore[call-arg]
                applicationName="test",
                components=[],
            )

    def test_missing_name_raises_error(self):
        """Config without name raises an error."""
        with pytest.raises(ValidationError):
            BuildConfig(  # type: ignore[call-arg]
                applicationVersion="1.0",
                components=[],
            )


class TestConfigLoader:
    """Tests for YAML file loading."""

    def test_load_minimal_config(self):
        """Load test YAML file."""
        config = load_build_config(FIXTURES / "configs/minimal_config.yaml")

        assert config.application_name == "qubership-jaeger"
        assert config.application_version == "1.2.3"
        assert len(config.components) == 4

    def test_standalone_component(self):
        """Check standalone-runnable component from YAML."""
        config = load_build_config(FIXTURES / "configs/minimal_config.yaml")

        standalone = config.components[0]
        assert standalone.name == "qubership-jaeger"
        assert standalone.mime_type == MimeType.STANDALONE_RUNNABLE
        assert len(standalone.depends_on) == 1
        assert standalone.depends_on[0].mime_type == MimeType.HELM_CHART

    def test_helm_component_with_deps(self):
        """Check helm-chart component with dependencies."""
        config = load_build_config(FIXTURES / "configs/minimal_config.yaml")

        helm = config.components[1]
        assert helm.mime_type == MimeType.HELM_CHART
        assert helm.reference == "oci://sandbox.example.com/charts/qubership-jaeger:1.2.3"
        assert len(helm.depends_on) == 2
        assert helm.depends_on[0].values_path_prefix == "images.jaeger"

    def test_load_nonexistent_file_raises_error(self):
        """Non-existent file raises an error."""
        with pytest.raises(FileNotFoundError):
            load_build_config(Path("nonexistent.yaml"))
