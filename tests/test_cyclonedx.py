"""Tests for the CycloneDX output JSON models.

Verifies that models produce correct field names on serialization (model_dump)
as required by the specification.
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
    """Tests for bom-ref generation."""

    def test_format(self):
        """bom-ref must follow the name:uuid format."""
        ref = _make_bom_ref("my-app")
        assert ref.startswith("my-app:")
        # Part after colon must be a UUID (36 chars with dashes)
        uuid_part = ref.split(":")[1]
        assert len(uuid_part) == 36

    def test_unique(self):
        """Each call produces a unique bom-ref."""
        ref1 = _make_bom_ref("app")
        ref2 = _make_bom_ref("app")
        assert ref1 != ref2


class TestCdxComponent:
    """Tests for the component model."""

    def test_docker_image_serialization(self):
        """Docker image serializes with correct JSON keys."""
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

        assert data["bom-ref"] == "jaeger:aaa-bbb"
        assert data["mime-type"] == "application/vnd.docker.image"
        assert data["type"] == "container"
        assert data["name"] == "jaeger"
        assert data["version"] == "build3"
        assert data["group"] == "core"
        assert data["purl"] == "pkg:docker/core/jaeger@build3?registry_name=sandbox"

    def test_minimal_component(self):
        """Component with minimal fields — only required by schema."""
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
        # Optional fields must not appear in JSON
        assert "version" not in data
        assert "group" not in data
        assert "purl" not in data

    def test_component_with_properties(self):
        """Component with properties."""
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
        """Component with hashes."""
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
        """Empty properties/components lists are included when explicitly set."""
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
    """Tests for the dependency model."""

    def test_serialization(self):
        """dependsOn serializes in camelCase."""
        dep = CdxDependency(
            ref="app:123",
            depends_on=["chart:456", "docker:789"],
        )
        data = dep.model_dump(by_alias=True)
        assert data["ref"] == "app:123"
        assert data["dependsOn"] == ["chart:456", "docker:789"]

    def test_empty_depends_on(self):
        """Component with no dependencies."""
        dep = CdxDependency(ref="leaf:123")
        data = dep.model_dump(by_alias=True)
        assert data["dependsOn"] == []


class TestCycloneDxBom:
    """Tests for the root BOM model."""

    def _make_minimal_bom(self) -> CycloneDxBom:
        """Create a minimal BOM for tests."""
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
        """Root BOM fields serialize correctly."""
        bom = self._make_minimal_bom()
        data = bom.model_dump(by_alias=True)

        assert data["bomFormat"] == "CycloneDX"
        assert data["specVersion"] == "1.6"
        assert data["version"] == 1
        assert data["serialNumber"].startswith("urn:uuid:")
        assert data["$schema"] == "../schemas/application-manifest.schema.json"

    def test_metadata_structure(self):
        """metadata section has correct structure."""
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
        """tools section is an object with a components array."""
        bom = self._make_minimal_bom()
        data = bom.model_dump(by_alias=True)
        tools = data["metadata"]["tools"]

        assert "components" in tools
        assert tools["components"][0]["type"] == "application"
        assert tools["components"][0]["name"] == "am-build-cli"
        assert tools["components"][0]["version"] == "0.1.0"

    def test_empty_components_and_dependencies(self):
        """components and dependencies default to empty lists."""
        bom = self._make_minimal_bom()
        data = bom.model_dump(by_alias=True)
        assert data["components"] == []
        assert data["dependencies"] == []
