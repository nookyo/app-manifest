"""Pydantic models for component CI metadata JSON.

These files are generated in CI when building Docker images
and Helm charts. They are passed to the CLI as positional arguments.

Docker image example:
{
    "name": "jaeger",
    "type": "container",
    "mime-type": "application/vnd.docker.image",
    "group": "core",
    "version": "build3",
    "hashes": [{"alg": "SHA-256", "content": "abc123..."}],
    "reference": "sandbox.example.com/core/jaeger:build3"
}

Helm chart example:
{
    "name": "kafka",
    "type": "application",
    "mime-type": "application/vnd.qubership.helm.chart",
    "appVersion": "2.1.0-dev-SNAPSHOT",
    "hashes": [...],
    "reference": "oci://registry.qubership.org/.../kafka:2.1.0",
    "components": [
        {
            "type": "data",
            "mime-type": "application/vnd.nc.resource-profile-baseline",
            "name": "resource-profile-baselines",
            "data": [{"type": "configuration", "name": "small.yaml", "contents": {...}}]
        }
    ]
}
"""

from typing import Any

from pydantic import BaseModel, Field


class HashEntry(BaseModel):
    """Artifact hash (for integrity verification)."""

    alg: str
    content: str


class MetadataAttachment(BaseModel):
    """Attachment with base64-encoded content from CI metadata."""

    content_type: str = Field(alias="contentType")
    encoding: str = "base64"
    content: str

    model_config = {"populate_by_name": True}


class MetadataDataContents(BaseModel):
    """Wrapper for the attachment."""

    attachment: MetadataAttachment


class MetadataDataEntry(BaseModel):
    """A single data entry (e.g. small.yaml)."""

    type: str = "configuration"
    name: str
    contents: MetadataDataContents


class MetadataNestedComponent(BaseModel):
    """Nested component from Helm chart CI metadata.

    For example: values.schema.json or resource-profile-baselines.
    This data is already prepared by CI in the required format and
    is passed to the output manifest almost unchanged.
    """

    type: str
    mime_type: str = Field(alias="mime-type")
    name: str
    data: list[MetadataDataEntry] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class ComponentMetadata(BaseModel):
    """Metadata for a single component from CI.

    name — component name (must match the name in the YAML config)
    type — "container" for Docker, "application" for Helm
    mime_type — component type
    group — group/namespace (e.g. "core") — optional
    version — version/tag — optional
    app_version — application version (for Helm charts) — optional
    hashes — list of artifact hashes
    reference — full reference to the artifact
    components — nested components (values.schema.json, resource-profiles)
    """

    name: str
    type: str
    mime_type: str = Field(alias="mime-type")
    group: str | None = None
    version: str | None = None
    app_version: str | None = Field(default=None, alias="appVersion")
    hashes: list[HashEntry] = Field(default_factory=list)
    reference: str | None = None
    components: list[MetadataNestedComponent] = Field(default_factory=list)

    model_config = {"populate_by_name": True}
