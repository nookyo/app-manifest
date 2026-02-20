"""Output CycloneDX 1.6 JSON models.

These models describe the structure of the final Application Manifest.
Calling .model_dump(by_alias=True) serializes them to JSON
with the correct field names (bom-ref, $schema, bomFormat, etc.).

Example top-level JSON output:
{
    "$schema": "...",
    "bomFormat": "CycloneDX",
    "specVersion": "1.6",
    "serialNumber": "urn:uuid:...",
    "version": 1,
    "metadata": { ... },
    "components": [ ... ],
    "dependencies": [ ... ]
}
"""

import uuid
from typing import Any

from pydantic import BaseModel, Field


def _make_bom_ref(name: str) -> str:
    """Generate a bom-ref in the format name:uuid.

    Based on the spec, bom-ref always follows the format:
    "qubership-jaeger:61439aff-c00d-43f5-9bae-fe6db05db2d5"
    """
    return f"{name}:{uuid.uuid4()}"


# ─── Hashes ────────────────────────────────────────────────

class CdxHash(BaseModel):
    """Artifact hash. Example: {"alg": "SHA-256", "content": "a1b2c3..."}"""

    alg: str
    content: str


# ─── Properties ─────────────────────────────────────────────

class CdxProperty(BaseModel):
    """Component property. Example: {"name": "isLibrary", "value": false}

    value — can be a string, number, boolean, or object,
    hence the type is Any.
    """

    name: str
    value: Any


# ─── Nested data (values.schema.json, resource-profiles) ──────

class CdxAttachment(BaseModel):
    """Attachment with encoded content."""

    content_type: str = Field(
        validation_alias="contentType",
        serialization_alias="contentType",
    )
    encoding: str = "base64"
    content: str

    model_config = {"populate_by_name": True}


class CdxDataContents(BaseModel):
    """Wrapper for an attachment."""

    attachment: CdxAttachment


class CdxDataEntry(BaseModel):
    """Data entry (e.g. values.schema.json).

    Example:
    {
        "type": "configuration",
        "name": "values.schema.json",
        "contents": { "attachment": { ... } }
    }
    """

    type: str = "configuration"
    name: str
    contents: CdxDataContents


# ─── Component ────────────────────────────────────────────

class CdxComponent(BaseModel):
    """A single component in the manifest.

    Can be: standalone-runnable, docker image, helm chart,
    values.schema.json, resource-profile, etc.

    bom-ref — unique identifier in the format "name:uuid"
    type — "application", "container", or "data"
    mime-type — specific type (e.g. "application/vnd.docker.image")
    """

    bom_ref: str = Field(
        validation_alias="bom-ref",
        serialization_alias="bom-ref",
    )
    type: str
    mime_type: str = Field(
        validation_alias="mime-type",
        serialization_alias="mime-type",
    )
    name: str
    version: str | None = None
    group: str | None = None
    purl: str | None = None
    properties: list[CdxProperty] | None = None
    hashes: list[CdxHash] | None = None
    components: list["CdxComponent"] | None = None
    data: list[CdxDataEntry] | None = None

    model_config = {"populate_by_name": True}


# ─── Dependencies ─────────────────────────────────────────

class CdxDependency(BaseModel):
    """Dependency link between components.

    ref — bom-ref of the depending component
    dependsOn — list of bom-ref values this component depends on

    Example:
    {
        "ref": "qubership-jaeger:aaa-bbb",
        "dependsOn": ["docker-jaeger:ccc-ddd", "chart-jaeger:eee-fff"]
    }
    """

    ref: str
    depends_on: list[str] = Field(
        default_factory=list,
        validation_alias="dependsOn",
        serialization_alias="dependsOn",
    )

    model_config = {"populate_by_name": True}


# ─── Metadata ─────────────────────────────────────────────

class CdxTool(BaseModel):
    """Tool that generated the manifest."""

    type: str = "application"
    name: str
    version: str


class CdxToolsWrapper(BaseModel):
    """Wrapper for the list of tools.

    In JSON: "tools": {"components": [{"name": "am-build-cli", ...}]}
    """

    components: list[CdxTool]


class CdxMetadataComponent(BaseModel):
    """Component in the metadata section — describes the application itself."""

    bom_ref: str = Field(
        validation_alias="bom-ref",
        serialization_alias="bom-ref",
    )
    type: str = "application"
    mime_type: str = Field(
        default="application/vnd.nc.application",
        validation_alias="mime-type",
        serialization_alias="mime-type",
    )
    name: str
    version: str

    model_config = {"populate_by_name": True}


class CdxMetadata(BaseModel):
    """Manifest metadata section.

    timestamp — when generated (ISO 8601)
    component — the application description
    tools — what generated the manifest
    """

    timestamp: str
    component: CdxMetadataComponent
    tools: CdxToolsWrapper


# ─── Root BOM ─────────────────────────────────────────

class CycloneDxBom(BaseModel):
    """Root model for the Application Manifest (CycloneDX 1.6 BOM).

    This is what gets serialized to the final JSON file.
    """

    serial_number: str = Field(
        default_factory=lambda: f"urn:uuid:{uuid.uuid4()}",
        validation_alias="serialNumber",
        serialization_alias="serialNumber",
    )
    schema_url: str = Field(
        default="../schemas/application-manifest.schema.json",
        validation_alias="$schema",
        serialization_alias="$schema",
    )
    bom_format: str = Field(
        default="CycloneDX",
        validation_alias="bomFormat",
        serialization_alias="bomFormat",
    )
    spec_version: str = Field(
        default="1.6",
        validation_alias="specVersion",
        serialization_alias="specVersion",
    )
    version: int = 1
    metadata: CdxMetadata
    components: list[CdxComponent] = Field(default_factory=list)
    dependencies: list[CdxDependency] = Field(default_factory=list)

    model_config = {"populate_by_name": True}
