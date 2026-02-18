"""Тесты для PURL генератора и Registry Definition."""

from pathlib import Path

from app_manifest.services.purl import make_docker_purl, make_helm_purl, _hosts_match, _namespace_matches
from app_manifest.services.regdef_loader import load_registry_definition

FIXTURES = Path(__file__).parent / "fixtures"


class TestHostsMatch:
    """Тесты для сопоставления хостов."""

    def test_exact_match(self):
        assert _hosts_match("ghcr.io", "ghcr.io") is True

    def test_with_protocol(self):
        assert _hosts_match("ghcr.io", "https://ghcr.io") is True

    def test_oci_protocol(self):
        assert _hosts_match("registry.qubership.org", "oci://registry.qubership.org") is True

    def test_no_match(self):
        assert _hosts_match("ghcr.io", "docker.io") is False

    def test_trailing_slash(self):
        assert _hosts_match("ghcr.io", "ghcr.io/") is True


class TestNamespaceMatches:
    """Тесты для сопоставления namespace с groupName."""

    def test_exact_match(self):
        assert _namespace_matches("netcracker", "netcracker") is True

    def test_nested_path(self):
        assert _namespace_matches("netcracker/team/sub", "netcracker") is True

    def test_no_match(self):
        assert _namespace_matches("other-org", "netcracker") is False

    def test_similar_prefix_no_match(self):
        """netcracker-fork не должен совпадать с netcracker."""
        assert _namespace_matches("netcracker-fork", "netcracker") is False


class TestRegistryDefinition:
    """Тесты для загрузки Registry Definition."""

    def test_load_qubership(self):
        regdef = load_registry_definition(FIXTURES / "regdefs/qubership_regdef.yml")
        assert regdef.name == "qubership"
        assert regdef.docker_config.group_uri == "ghcr.io"
        assert regdef.helm_app_config.repository_domain_name == "oci://registry.qubership.org"

    def test_load_sandbox(self):
        regdef = load_registry_definition(FIXTURES / "regdefs/sandbox_regdef.yml")
        assert regdef.name == "sandbox"
        assert regdef.docker_config.group_uri == "123456789.dkr.ecr.eu-west-1.amazonaws.com"


class TestDockerPurl:
    """Тесты для генерации Docker PURL."""

    def test_ghcr_with_regdef(self):
        """ghcr.io → registry_name=qubership."""
        regdef = load_registry_definition(FIXTURES / "regdefs/qubership_regdef.yml")
        purl = make_docker_purl("ghcr.io/netcracker/jaeger:1.0", regdef)
        assert purl == "pkg:docker/netcracker/jaeger@1.0?registry_name=qubership"

    def test_docker_hub(self):
        """docker.io с namespace — regdef не совпадает, fallback на хост."""
        regdef = load_registry_definition(FIXTURES / "regdefs/qubership_regdef.yml")
        purl = make_docker_purl("docker.io/envoyproxy/envoy:v1.32.6", regdef)
        assert purl == "pkg:docker/envoyproxy/envoy@v1.32.6?registry_name=docker.io"

    def test_docker_hub_short(self):
        """Короткий формат docker.io — два сегмента."""
        purl = make_docker_purl("docker.io/openjdk:11")
        assert purl == "pkg:docker/openjdk@11?registry_name=docker.io"

    def test_aws_ecr_with_regdef(self):
        """AWS ECR → registry_name=sandbox (namespace совпадает с groupName)."""
        regdef = load_registry_definition(FIXTURES / "regdefs/sandbox_regdef.yml")
        purl = make_docker_purl(
            "123456789.dkr.ecr.eu-west-1.amazonaws.com/docker/jaeger:build3",
            regdef,
        )
        assert purl == "pkg:docker/docker/jaeger@build3?registry_name=sandbox"

    def test_aws_ecr_namespace_mismatch(self):
        """AWS ECR с чужим namespace — fallback на хост."""
        regdef = load_registry_definition(FIXTURES / "regdefs/sandbox_regdef.yml")
        purl = make_docker_purl(
            "123456789.dkr.ecr.eu-west-1.amazonaws.com/other-org/jaeger:build3",
            regdef,
        )
        assert purl == "pkg:docker/other-org/jaeger@build3?registry_name=123456789.dkr.ecr.eu-west-1.amazonaws.com"

    def test_without_regdef(self):
        """Без regdef — registry_name = хост."""
        purl = make_docker_purl("ghcr.io/netcracker/jaeger:1.0")
        assert purl == "pkg:docker/netcracker/jaeger@1.0?registry_name=ghcr.io"

    def test_deep_namespace(self):
        """Глубокий namespace: registry/a/b/c/image:tag."""
        regdef = load_registry_definition(FIXTURES / "regdefs/qubership_regdef.yml")
        purl = make_docker_purl("ghcr.io/netcracker/team/sub/image:v2", regdef)
        assert purl == "pkg:docker/netcracker/team/sub/image@v2?registry_name=qubership"


class TestHelmPurl:
    """Тесты для генерации Helm PURL."""

    def test_oci_with_regdef(self):
        """OCI Helm → registry_name=qubership."""
        regdef = load_registry_definition(FIXTURES / "regdefs/qubership_regdef.yml")
        purl = make_helm_purl("oci://registry.qubership.org/charts/my-chart:1.0", regdef)
        assert purl == "pkg:helm/charts/my-chart@1.0?registry_name=qubership"

    def test_without_regdef(self):
        """Без regdef — registry_name = хост."""
        purl = make_helm_purl("oci://registry.example.com/repo/chart:2.0")
        assert purl == "pkg:helm/repo/chart@2.0?registry_name=registry.example.com"

    def test_https_helm(self):
        """HTTPS Helm reference."""
        regdef = load_registry_definition(FIXTURES / "regdefs/sandbox_regdef.yml")
        purl = make_helm_purl(
            "https://nexus.mycompany.internal/repository/helm-charts/my-chart:3.0",
            regdef,
        )
        assert "pkg:helm/" in purl
        assert "my-chart@3.0" in purl
