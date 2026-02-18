"""Генератор Package URL (PURL).

Преобразует reference артефакта в стандартный PURL,
используя Registry Definition для определения registry_name.

Примеры:
  Docker:
    reference: "ghcr.io/netcracker/jaeger:1.0"
    regdef name: "qubership" (groupUri: "ghcr.io")
    → "pkg:docker/netcracker/jaeger@1.0?registry_name=qubership"

  Helm OCI:
    reference: "oci://registry.qubership.org/charts/my-chart:1.0"
    regdef name: "qubership" (repositoryDomainName: "oci://registry.qubership.org")
    → "pkg:helm/charts/my-chart@1.0?registry_name=qubership"

  Docker без regdef:
    reference: "docker.io/envoyproxy/envoy:v1.32.6"
    → "pkg:docker/envoyproxy/envoy@v1.32.6?registry_name=docker.io"
"""

from app_manifest.models.regdef import RegistryDefinition


def parse_docker_reference(reference: str) -> tuple[str, str, str]:
    """Разобрать Docker reference на компоненты.

    Возвращает (name, version, namespace/group).

    Примеры:
        "docker.io/envoyproxy/envoy:v1.32.6" → ("envoy", "v1.32.6", "envoyproxy")
        "ghcr.io/netcracker/jaeger:1.0"      → ("jaeger", "1.0", "netcracker")
        "sandbox.example.com/core/svc:2.0"  → ("svc", "2.0", "core")
        "ubuntu:22.04"                       → ("ubuntu", "22.04", "library")
    """
    _, namespace, name, version = _parse_docker_ref_parts(reference)
    return name, version, namespace


def make_docker_purl(
    reference: str,
    regdef: RegistryDefinition | None = None,
) -> str:
    """Создать PURL для Docker-образа из reference.

    reference — например "ghcr.io/netcracker/jaeger:1.0"
    regdef — Registry Definition для определения registry_name
    """
    registry, namespace, name, version = _parse_docker_ref_parts(reference)

    if not name:
        raise ValueError(f"Invalid Docker reference: cannot parse image name from '{reference}'")
    if not registry:
        raise ValueError(f"Invalid Docker reference: cannot determine registry from '{reference}'")

    # Определяем registry_name из Registry Definition
    registry_name = _resolve_registry_name(registry, "docker", regdef, namespace)

    if namespace:
        return f"pkg:docker/{namespace}/{name}@{version}?registry_name={registry_name}"
    else:
        return f"pkg:docker/{name}@{version}?registry_name={registry_name}"


def _parse_docker_ref_parts(reference: str) -> tuple[str, str, str, str]:
    """Разобрать Docker reference на (registry, namespace, name, version)."""
    ref = reference
    if ref.startswith("docker://"):
        ref = ref[len("docker://"):]

    # Формат: REGISTRY_HOST[:PORT]/NAMESPACE/IMAGE:TAG
    parts = ref.split("/")

    if len(parts) >= 3:
        registry = parts[0]
        name_tag = parts[-1]
        namespace = "/".join(parts[1:-1])
    elif len(parts) == 2:
        if "." in parts[0] or ":" in parts[0]:
            registry = parts[0]
            namespace = ""
            name_tag = parts[1]
        else:
            registry = "docker.io"
            namespace = parts[0]
            name_tag = parts[1]
    else:
        registry = "docker.io"
        namespace = "library"
        name_tag = parts[0]

    if ":" in name_tag:
        name, version = name_tag.rsplit(":", 1)
    elif "@" in name_tag:
        name, version = name_tag.rsplit("@", 1)
    else:
        name = name_tag
        version = "latest"

    return registry, namespace, name, version


def make_helm_purl(
    reference: str,
    regdef: RegistryDefinition | None = None,
) -> str:
    """Создать PURL для Helm-чарта из reference.

    reference — например "oci://registry.example.com/charts/my-chart:1.0"
    """
    ref = reference

    # Убираем протокол
    if ref.startswith("oci://"):
        ref = ref[len("oci://"):]
    elif ref.startswith("https://"):
        ref = ref[len("https://"):]
    elif ref.startswith("http://"):
        ref = ref[len("http://"):]

    # Разделяем на registry/namespace/name:version
    parts = ref.split("/")

    if len(parts) >= 3:
        registry = parts[0]
        name_tag = parts[-1]
        namespace = "/".join(parts[1:-1])
    elif len(parts) == 2:
        registry = parts[0]
        name_tag = parts[1]
        namespace = ""
    else:
        registry = ""
        name_tag = parts[0]
        namespace = ""

    # Разделяем имя и версию
    if ":" in name_tag:
        name, version = name_tag.rsplit(":", 1)
    else:
        name = name_tag
        version = ""

    if not name:
        raise ValueError(f"Invalid Helm reference: cannot parse chart name from '{reference}'")
    if not version:
        raise ValueError(f"Invalid Helm reference: version is required in '{reference}'")
    if not registry:
        raise ValueError(f"Invalid Helm reference: cannot determine registry from '{reference}'")

    # Определяем registry_name
    registry_name = _resolve_registry_name(registry, "helm", regdef)

    if namespace:
        return f"pkg:helm/{namespace}/{name}@{version}?registry_name={registry_name}"
    else:
        return f"pkg:helm/{name}@{version}?registry_name={registry_name}"


def _resolve_registry_name(
    registry_host: str,
    artifact_type: str,
    regdef: RegistryDefinition | None,
    namespace: str = "",
) -> str:
    """Найти registry_name по хосту реестра.

    Для Docker: сопоставляет registry_host с groupUri И namespace с groupName.
    Для Helm: сопоставляет registry_host с repositoryDomainName.
    Если совпадение найдено — возвращает name из regdef.
    Если нет — возвращает сам registry_host как fallback.
    """
    if not regdef:
        return registry_host

    if artifact_type == "docker" and regdef.docker_config:
        uris = [
            regdef.docker_config.group_uri,
            regdef.docker_config.snapshot_uri,
            regdef.docker_config.staging_uri,
            regdef.docker_config.release_uri,
        ]
        group_name = regdef.docker_config.group_name
        for uri in uris:
            if uri and _hosts_match(registry_host, uri):
                # Хост совпал — теперь проверяем groupName против namespace
                if not group_name or _namespace_matches(namespace, group_name):
                    return regdef.name

    if artifact_type == "helm" and regdef.helm_app_config:
        domain = regdef.helm_app_config.repository_domain_name
        if domain and _hosts_match(registry_host, domain):
            return regdef.name

    # Fallback: возвращаем сам хост
    return registry_host


def _namespace_matches(namespace: str, group_name: str) -> bool:
    """Проверить что namespace совпадает с groupName.

    Точное совпадение или namespace начинается с groupName + "/".
    Это позволяет вложенные пути: netcracker/team/sub совпадает с netcracker.

    _namespace_matches("netcracker", "netcracker") → True
    _namespace_matches("netcracker/team/sub", "netcracker") → True
    _namespace_matches("other-org", "netcracker") → False
    _namespace_matches("netcracker-fork", "netcracker") → False
    """
    return namespace == group_name or namespace.startswith(group_name + "/")


def _hosts_match(host: str, uri: str) -> bool:
    """Проверить что хост совпадает с URI (игнорируя протокол).

    _hosts_match("ghcr.io", "ghcr.io") → True
    _hosts_match("ghcr.io", "https://ghcr.io") → True
    _hosts_match("registry.example.com", "oci://registry.example.com") → True
    """
    clean_uri = uri
    for prefix in ("oci://", "https://", "http://", "docker://"):
        if clean_uri.startswith(prefix):
            clean_uri = clean_uri[len(prefix):]
            break

    clean_uri = clean_uri.rstrip("/")
    host = host.rstrip("/")

    return host == clean_uri
