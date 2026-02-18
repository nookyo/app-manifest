"""Модели для Registry Definition v2.0.

Registry Definition описывает реестры артефактов (Docker, Helm, и др.)
и используется для определения registry_name при генерации PURL.

Пример файла (qubership.yml):
    version: "2.0"
    name: qubership
    dockerConfig:
      groupUri: ghcr.io
      groupName: netcracker
    helmAppConfig:
      repositoryDomainName: oci://registry.qubership.org
      helmGroupRepoName: helm-group

Пример файла (sandbox.yml):
    version: "2.0"
    name: sandbox
    dockerConfig:
      groupUri: 123456789.dkr.ecr.eu-west-1.amazonaws.com
      groupName: docker
    helmAppConfig:
      repositoryDomainName: https://nexus.mycompany.internal/repository/helm-charts
"""

from pydantic import BaseModel, Field


class DockerConfig(BaseModel):
    """Конфигурация Docker-реестра."""

    group_uri: str | None = Field(default=None, alias="groupUri")
    group_name: str | None = Field(default=None, alias="groupName")
    snapshot_uri: str | None = Field(default=None, alias="snapshotUri")
    staging_uri: str | None = Field(default=None, alias="stagingUri")
    release_uri: str | None = Field(default=None, alias="releaseUri")

    model_config = {"populate_by_name": True}


class HelmAppConfig(BaseModel):
    """Конфигурация Helm-реестра."""

    repository_domain_name: str | None = Field(
        default=None, alias="repositoryDomainName"
    )
    helm_group_repo_name: str | None = Field(
        default=None, alias="helmGroupRepoName"
    )

    model_config = {"populate_by_name": True}


class GitHubReleaseConfig(BaseModel):
    """Конфигурация GitHub Releases."""

    repository_domain_name: str | None = Field(
        default=None, alias="repositoryDomainName"
    )
    group_name: str | None = Field(default=None, alias="groupName")
    owner: str | None = None
    repository: str | None = None

    model_config = {"populate_by_name": True}


class RegistryDefinition(BaseModel):
    """Корневая модель Registry Definition v2.0.

    name — имя реестра (например "sandbox", "qubership")
    Это имя используется как registry_name в PURL.
    """

    version: str = "2.0"
    name: str
    docker_config: DockerConfig | None = Field(default=None, alias="dockerConfig")
    helm_app_config: HelmAppConfig | None = Field(
        default=None, alias="helmAppConfig"
    )
    github_release_config: GitHubReleaseConfig | None = Field(
        default=None, alias="githubReleaseConfig"
    )

    model_config = {"populate_by_name": True}
