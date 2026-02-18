"""Тесты для моделей выходного CycloneDX JSON.

Проверяем что модели при сериализации (model_dump) выдают JSON
с правильными именами полей — как требует спецификация.
"""

from app_manifest.models.cyclonedx import (
    CdxComponent,
    CdxDependency,
    CdxHash,
    CdxMetadata,
    CdxMetadataComponent,
    CdxProperty,
    CdxTool,
    CdxToolsWrapper,
    CycloneDxBom,
    _make_bom_ref,
)


class TestBomRef:
    """Тесты для генерации bom-ref."""

    def test_format(self):
        """bom-ref должен быть в формате name:uuid."""
        ref = _make_bom_ref("my-app")
        assert ref.startswith("my-app:")
        # После двоеточия — UUID (36 символов с дефисами)
        uuid_part = ref.split(":")[1]
        assert len(uuid_part) == 36

    def test_unique(self):
        """Каждый вызов генерирует уникальный bom-ref."""
        ref1 = _make_bom_ref("app")
        ref2 = _make_bom_ref("app")
        assert ref1 != ref2


class TestCdxComponent:
    """Тесты для модели компонента."""

    def test_docker_image_serialization(self):
        """Docker-образ сериализуется с правильными ключами JSON."""
        comp = CdxComponent(
            bom_ref="jaeger:aaa-bbb",
            type="container",
            mime_type="application/vnd.docker.image",
            name="jaeger",
            version="build3",
            group="core",
            purl="pkg:docker/core/jaeger@build3?registry_name=sandbox",
        )
        data = comp.model_dump(by_alias=True, exclude_none=True)

        # Проверяем что ключи в JSON правильные (с дефисами)
        assert data["bom-ref"] == "jaeger:aaa-bbb"
        assert data["mime-type"] == "application/vnd.docker.image"
        assert data["type"] == "container"
        assert data["name"] == "jaeger"
        assert data["version"] == "build3"
        assert data["group"] == "core"
        assert data["purl"] == "pkg:docker/core/jaeger@build3?registry_name=sandbox"

    def test_minimal_component(self):
        """Компонент с минимумом полей — только обязательные по схеме."""
        comp = CdxComponent(
            bom_ref="app:123",
            type="application",
            mime_type="application/vnd.nc.standalone-runnable",
            name="my-app",
        )
        data = comp.model_dump(by_alias=True, exclude_none=True)

        assert "bom-ref" in data
        assert "type" in data
        assert "mime-type" in data
        assert "name" in data
        # Опциональные поля не должны быть в JSON
        assert "version" not in data
        assert "group" not in data
        assert "purl" not in data

    def test_component_with_properties(self):
        """Компонент со свойствами."""
        comp = CdxComponent(
            bom_ref="chart:123",
            type="application",
            mime_type="application/vnd.nc.helm.chart",
            name="my-chart",
            properties=[
                CdxProperty(name="isLibrary", value=False),
            ],
        )
        data = comp.model_dump(by_alias=True, exclude_none=True)
        assert data["properties"][0]["name"] == "isLibrary"
        assert data["properties"][0]["value"] is False

    def test_component_with_hashes(self):
        """Компонент с хешами."""
        comp = CdxComponent(
            bom_ref="img:123",
            type="container",
            mime_type="application/vnd.docker.image",
            name="my-image",
            hashes=[CdxHash(alg="SHA-256", content="abc123")],
        )
        data = comp.model_dump(by_alias=True, exclude_none=True)
        assert data["hashes"][0] == {"alg": "SHA-256", "content": "abc123"}

    def test_empty_lists_included_when_set(self):
        """Пустые списки properties/components включаются если заданы явно."""
        comp = CdxComponent(
            bom_ref="app:123",
            type="application",
            mime_type="application/vnd.nc.standalone-runnable",
            name="app",
            properties=[],
            components=[],
        )
        data = comp.model_dump(by_alias=True, exclude_none=True)
        assert data["properties"] == []
        assert data["components"] == []


class TestCdxDependency:
    """Тесты для модели зависимости."""

    def test_serialization(self):
        """dependsOn сериализуется в camelCase."""
        dep = CdxDependency(
            ref="app:123",
            depends_on=["chart:456", "docker:789"],
        )
        data = dep.model_dump(by_alias=True)
        assert data["ref"] == "app:123"
        assert data["dependsOn"] == ["chart:456", "docker:789"]

    def test_empty_depends_on(self):
        """Компонент без зависимостей."""
        dep = CdxDependency(ref="leaf:123")
        data = dep.model_dump(by_alias=True)
        assert data["dependsOn"] == []


class TestCycloneDxBom:
    """Тесты для корневой модели BOM."""

    def _make_minimal_bom(self) -> CycloneDxBom:
        """Создать минимальный BOM для тестов."""
        return CycloneDxBom(
            metadata=CdxMetadata(
                timestamp="2025-01-21T12:00:00Z",
                component=CdxMetadataComponent(
                    bom_ref="my-app:aaa-bbb",
                    name="my-app",
                    version="1.0.0",
                ),
                tools=CdxToolsWrapper(
                    components=[
                        CdxTool(name="am-build-cli", version="0.1.0"),
                    ]
                ),
            ),
        )

    def test_root_fields(self):
        """Корневые поля BOM сериализуются правильно."""
        bom = self._make_minimal_bom()
        data = bom.model_dump(by_alias=True)

        assert data["bomFormat"] == "CycloneDX"
        assert data["specVersion"] == "1.6"
        assert data["version"] == 1
        assert data["serialNumber"].startswith("urn:uuid:")
        assert data["$schema"] == "../schemas/application-manifest.schema.json"

    def test_metadata_structure(self):
        """Секция metadata имеет правильную структуру."""
        bom = self._make_minimal_bom()
        data = bom.model_dump(by_alias=True)
        meta = data["metadata"]

        assert meta["timestamp"] == "2025-01-21T12:00:00Z"
        assert meta["component"]["type"] == "application"
        assert meta["component"]["mime-type"] == "application/vnd.nc.application"
        assert meta["component"]["bom-ref"] == "my-app:aaa-bbb"
        assert meta["component"]["name"] == "my-app"
        assert meta["component"]["version"] == "1.0.0"

    def test_tools_structure(self):
        """Секция tools — объект с массивом components."""
        bom = self._make_minimal_bom()
        data = bom.model_dump(by_alias=True)
        tools = data["metadata"]["tools"]

        assert "components" in tools
        assert tools["components"][0]["type"] == "application"
        assert tools["components"][0]["name"] == "am-build-cli"
        assert tools["components"][0]["version"] == "0.1.0"

    def test_empty_components_and_dependencies(self):
        """По умолчанию components и dependencies — пустые списки."""
        bom = self._make_minimal_bom()
        data = bom.model_dump(by_alias=True)
        assert data["components"] == []
        assert data["dependencies"] == []
