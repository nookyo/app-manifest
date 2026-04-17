"""Registry Definition file loader.

Reads a YAML file describing the registry and returns a RegistryDefinition.
"""

from pathlib import Path

import yaml

from app_manifest.models.regdef import RegistryDefinition


def load_registry_definition(path: Path) -> RegistryDefinition:
    """Read a Registry Definition file.

    Supports both Registry Definition v1.0 and v2.0.

    v1.0 differences from v2.0:
      - no `version` field (absence means v1.0)
      - `helmAppConfig` has no `repositoryDomainName` — use `mavenConfig.repositoryDomainName` as fallback
      - `dockerConfig.groupName` is a repo group name, not a Docker namespace — skip namespace matching
    """
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not raw:
        raise ValueError(f"Registry definition file {path} is empty or invalid")

    # v1.0 detection: no `version` field
    if not raw.get("version"):
        # Backfill helmAppConfig.repositoryDomainName from mavenConfig
        maven_domain = (raw.get("mavenConfig") or {}).get("repositoryDomainName")
        if maven_domain:
            helm_app = raw.setdefault("helmAppConfig", {})
            if not helm_app.get("repositoryDomainName"):
                helm_app["repositoryDomainName"] = maven_domain

        # In v1.0 groupName is a repo identifier, not a Docker namespace —
        # clear it so purl.py skips namespace matching and matches by host only
        docker_cfg = raw.get("dockerConfig") or {}
        if docker_cfg.get("groupName"):
            docker_cfg["groupName"] = None

    return RegistryDefinition.model_validate(raw)
