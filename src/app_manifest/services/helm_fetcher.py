"""Backwards compatibility: module renamed to artifact_fetcher."""

from app_manifest.services.artifact_fetcher import (  # noqa: F401
    fetch_components_from_config,
    fetch_docker_component_from_reference,
    fetch_helm_component,
    fetch_helm_components_from_config,
)
