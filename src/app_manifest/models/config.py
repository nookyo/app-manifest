"""Pydantic models for the YAML build config.

This file describes the structure of the YAML file that the user
passes via --config. Pydantic validates that
all required fields are present and types are correct.
"""

from enum import Enum

from pydantic import BaseModel, Field


class MimeType(str, Enum):
    """Allowed component types.

    str + Enum — each value is a string
    but restricted to the declared set. If YAML contains
    mimeType: "invalid-type" — Pydantic will raise an error.
    """

    # nc-variant (from the spec)
    STANDALONE_RUNNABLE = "application/vnd.nc.standalone-runnable"
    DOCKER_IMAGE = "application/vnd.docker.image"
    HELM_CHART = "application/vnd.nc.helm.chart"
    SMARTPLUG = "application/vnd.nc.smartplug"
    SAMPLEREPO = "application/vnd.nc.samplerepo"
    CDN = "application/vnd.nc.cdn"
    CRD = "application/vnd.nc.crd"
    JOB = "application/vnd.nc.job"

    # qubership-variant (from real-world usage)
    Q_STANDALONE_RUNNABLE = "application/vnd.qubership.standalone-runnable"
    Q_HELM_CHART = "application/vnd.qubership.helm.chart"
    Q_SMARTPLUG = "application/vnd.qubership.smartplug"
    Q_SAMPLEREPO = "application/vnd.qubership.samplerepo"
    Q_CDN = "application/vnd.qubership.cdn"
    Q_CRD = "application/vnd.qubership.crd"
    Q_JOB = "application/vnd.qubership.job"


class DependencyConfig(BaseModel):
    """A single component dependency.

    Example in YAML:
        dependsOn:
          - name: jaeger
            mimeType: application/vnd.docker.image
            valuesPathPrefix: images.jaeger
    """

    name: str
    mime_type: MimeType = Field(alias="mimeType")
    values_path_prefix: str | None = Field(default=None, alias="valuesPathPrefix")

    # populate_by_name=True — allows both the Python name (mime_type)
    # and the YAML alias (mimeType). Needed for convenience in tests.
    model_config = {"populate_by_name": True}


class ComponentConfig(BaseModel):
    """A single component entry in the config.

    Example in YAML:
        components:
          - name: qubership-jaeger
            mimeType: application/vnd.nc.helm.chart
            reference: oci://registry/repo/chart:1.0
            dependsOn:
              - name: jaeger
                mimeType: application/vnd.docker.image
    """

    name: str
    mime_type: MimeType = Field(alias="mimeType")
    reference: str | None = None
    depends_on: list[DependencyConfig] = Field(default_factory=list, alias="dependsOn")

    model_config = {"populate_by_name": True}


class BuildConfig(BaseModel):
    """Root model for the YAML build config.

    Example YAML file:
        applicationVersion: "1.2.3"
        applicationName: "my-app"
        components:
          - name: my-service
            mimeType: application/vnd.docker.image
    """

    application_version: str = Field(alias="applicationVersion")
    application_name: str = Field(alias="applicationName")
    components: list[ComponentConfig]

    model_config = {"populate_by_name": True}
