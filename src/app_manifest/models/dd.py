"""Pydantic models for Deployment Descriptor (DD) JSON.

A Deployment Descriptor describes a set of services and charts
that make up a deployable application.

Only the fields relevant to DD↔AMv2 conversion are modelled here.
Other sections (infrastructures, configurations, etc.) are preserved
as-is during round-trip conversion.

Example:
    {
        "services": [
            {
                "image_name": "my-service",
                "docker_repository_name": "cloud-core",
                "docker_tag": "build2",
                "full_image_name": "registry.example.com/cloud-core/my-service:build2",
                "docker_registry": "registry.example.com",
                "docker_digest": "abc123...",
                "image_type": "service",
                "service_name": "my-service-chart",
                "version": "1.0.0"
            }
        ],
        "charts": [
            {
                "helm_chart_name": "my-app",
                "helm_chart_version": "1.0.0",
                "full_chart_name": "https://registry.example.com/charts/my-app-1.0.0.tgz",
                "helm_registry": "https://registry.example.com/charts",
                "type": "app-chart"
            }
        ]
    }
"""

from pydantic import BaseModel, Field


class DdService(BaseModel):
    """A service entry in the DD services array."""

    image_name: str
    docker_repository_name: str | None = None
    docker_tag: str | None = None
    full_image_name: str
    docker_registry: str | None = None
    docker_digest: str | None = None
    image_type: str  # "image" or "service"
    service_name: str | None = None
    version: str | None = None

    model_config = {"populate_by_name": True}


class DdChart(BaseModel):
    """A chart entry in the DD charts array."""

    helm_chart_name: str
    helm_chart_version: str
    full_chart_name: str
    helm_registry: str | None = None
    type: str | None = None  # "app-chart"

    model_config = {"populate_by_name": True}


class DeploymentDescriptor(BaseModel):
    """Root model for the Deployment Descriptor JSON.

    Only services and charts are used for conversion.
    All other sections are preserved verbatim.
    """

    services: list[DdService] = Field(default_factory=list)
    charts: list[DdChart] = Field(default_factory=list)

    # Preserved as-is during conversion — not used in transformation logic
    metadata: dict = Field(default_factory=dict)
    include: list = Field(default_factory=list)
    infrastructures: list = Field(default_factory=list)
    configurations: list = Field(default_factory=list)
    frontends: list = Field(default_factory=list)
    smartplug: list = Field(default_factory=list)
    jobs: list = Field(default_factory=list)
    libraries: list = Field(default_factory=list)
    complexes: list = Field(default_factory=list)
    additional_artifacts: dict = Field(default_factory=dict, alias="additionalArtifacts")
    descriptors: list = Field(default_factory=list)

    model_config = {"populate_by_name": True}
