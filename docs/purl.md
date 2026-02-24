# PURL — Package URL

Every component in a mini-manifest and in the final Application Manifest has a `purl` field.
This document explains what a PURL is, how `am` constructs one, and how a Registry Definition
changes the result.

---

## What is a PURL?

A **Package URL** (PURL) is a standardized string that uniquely identifies a software artifact
regardless of where it is stored. The format is defined by the
[PURL specification](https://github.com/package-url/purl-spec).

General structure:

```
pkg:<type>/<namespace>/<name>@<version>?<qualifiers>
```

| Part              | Meaning                                                        |
| ----------------- | -------------------------------------------------------------- |
| `pkg:`            | Fixed prefix — marks this as a Package URL                     |
| `<type>`          | Artifact type: `docker` or `helm`                              |
| `<namespace>`     | Registry path before the image/chart name (org, project, etc.) |
| `<name>`          | Image or chart name                                            |
| `@<version>`      | Tag, digest, or chart version                                  |
| `?registry_name=` | Custom qualifier: logical name of the registry (see below)     |

`am` generates PURLs automatically from the `reference` field in the config or CI metadata.
You never write PURLs by hand.

---

## Docker PURL

### Input

A Docker reference is a string like:

```
ghcr.io/netcracker/jaeger:1.0
docker.io/envoyproxy/envoy:v1.32.6
sandbox.example.com/core/svc:2.0
ubuntu:22.04
```

### How `am` parses the reference

```
REGISTRY_HOST[:PORT] / NAMESPACE / IMAGE : TAG
```

| Part          | Example (`ghcr.io/netcracker/jaeger:1.0`) |
| ------------- | ----------------------------------------- |
| Registry host | `ghcr.io`                                 |
| Namespace     | `netcracker`                              |
| Image name    | `jaeger`                                  |
| Tag / version | `1.0`                                     |

Special cases:

- `ubuntu:22.04` — no registry, no namespace → registry defaults to `docker.io`, namespace to `library`
- `library/ubuntu:22.04` — no registry with dot/colon → registry defaults to `docker.io`
- `sandbox.example.com/svc:2.0` — registry present, no namespace → namespace is empty

### PURL format

```
pkg:docker/<namespace>/<name>@<version>?registry_name=<registry>
```

If namespace is empty:

```
pkg:docker/<name>@<version>?registry_name=<registry>
```

### Examples

| Reference                                | Generated PURL                                                    |
| ---------------------------------------- | ----------------------------------------------------------------- |
| `ghcr.io/netcracker/jaeger:1.0`          | `pkg:docker/netcracker/jaeger@1.0?registry_name=ghcr.io`          |
| `docker.io/envoyproxy/envoy:v1.32.6`     | `pkg:docker/envoyproxy/envoy@v1.32.6?registry_name=docker.io`     |
| `docker.io/library/openjdk:11`           | `pkg:docker/library/openjdk@11?registry_name=docker.io`           |
| `sandbox.example.com/core/jaeger:build3` | `pkg:docker/core/jaeger@build3?registry_name=sandbox.example.com` |
| `ubuntu:22.04`                           | `pkg:docker/library/ubuntu@22.04?registry_name=docker.io`         |

---

## Helm PURL

### Input

A Helm reference is an OCI URL:

```
oci://registry.qubership.org/charts/my-chart:1.2.3
```

### How `am` parses the reference

The `oci://` prefix is stripped, then the string is parsed the same way as Docker:

```
REGISTRY_HOST / NAMESPACE / CHART_NAME : VERSION
```

| Part          | Example (`oci://registry.qubership.org/charts/my-chart:1.2.3`) |
| ------------- | -------------------------------------------------------------- |
| Registry host | `registry.qubership.org`                                       |
| Namespace     | `charts`                                                       |
| Chart name    | `my-chart`                                                     |
| Version       | `1.2.3`                                                        |

> A version is **required** for Helm references. References without a tag are rejected.

### PURL format

```
pkg:helm/<namespace>/<name>@<version>?registry_name=<registry>
```

### Examples

| Reference                                                 | Generated PURL                                                             |
| --------------------------------------------------------- | -------------------------------------------------------------------------- |
| `oci://registry.qubership.org/charts/my-chart:1.2.3`      | `pkg:helm/charts/my-chart@1.2.3?registry_name=registry.qubership.org`      |
| `oci://sandbox.example.com/charts/qubership-jaeger:1.2.3` | `pkg:helm/charts/qubership-jaeger@1.2.3?registry_name=sandbox.example.com` |

---

## registry_name and Registry Definition

By default `registry_name` is set to the raw hostname extracted from the reference:

```
pkg:docker/netcracker/jaeger@1.0?registry_name=ghcr.io
```

This is sufficient for simple cases. But in enterprise environments a single logical registry
may be exposed under different hostnames for different build stages
(snapshot, staging, release). A **Registry Definition** lets you replace the raw hostname
with a single logical name.

### How matching works

When a Registry Definition is provided, `am` checks the hostname from the reference against
the configured URIs:

**For Docker:**

1. The registry host is compared against `dockerConfig.groupUri`, `snapshotUri`,
   `stagingUri`, and `releaseUri`.
2. If the host matches **and** the image namespace starts with `dockerConfig.groupName`
   (when set) — `registry_name` is replaced with the regdef `name`.

**For Helm:**

1. The registry host is compared against `helmAppConfig.repositoryDomainName`.
2. If it matches — `registry_name` is replaced with the regdef `name`.

If nothing matches, the raw hostname is used as a fallback — no error is raised.

### Example

Registry Definition:

```yaml
name: "qubership"
dockerConfig:
  groupUri: "ghcr.io"
  groupName: "netcracker"
helmAppConfig:
  repositoryDomainName: "oci://registry.qubership.org"
```

| Reference                                          | Without regdef                                                      | With regdef above                                           |
| -------------------------------------------------- | ------------------------------------------------------------------- | ----------------------------------------------------------- |
| `ghcr.io/netcracker/jaeger:1.0`                    | `pkg:docker/netcracker/jaeger@1.0?registry_name=ghcr.io`            | `pkg:docker/netcracker/jaeger@1.0?registry_name=qubership`  |
| `ghcr.io/other-org/tool:2.0`                       | `pkg:docker/other-org/tool@2.0?registry_name=ghcr.io`               | `pkg:docker/other-org/tool@2.0?registry_name=ghcr.io` ¹     |
| `oci://registry.qubership.org/charts/my-chart:1.0` | `pkg:helm/charts/my-chart@1.0?registry_name=registry.qubership.org` | `pkg:helm/charts/my-chart@1.0?registry_name=qubership`      |
| `docker.io/library/ubuntu:22.04`                   | `pkg:docker/library/ubuntu@22.04?registry_name=docker.io`           | `pkg:docker/library/ubuntu@22.04?registry_name=docker.io` ² |

¹ Host (`ghcr.io`) matched, but namespace `other-org` does not start with `netcracker` — fallback to raw hostname.
² Host (`docker.io`) does not match any URI in the registry definition — fallback to raw hostname.

---

## Where PURLs appear

| Stage             | Command        | Where                                 |
| ----------------- | -------------- | ------------------------------------- |
| CI metadata input | `am component` | Generated from `reference` field in CI metadata JSON |
| Helm fetch        | `am fetch`     | Generated from `reference` in config  |
| Final manifest    | `am generate`  | Copied unchanged from mini-manifests  |

`am generate` does **not** re-generate PURLs — it takes them from the mini-manifests as-is.
This means the regdef must be passed consistently to both `am component` and `am fetch`.
