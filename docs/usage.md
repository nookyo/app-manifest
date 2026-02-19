# Build Config & Workflow

The **build config** (`build-config.yaml`) is the main input file you write and maintain.
It tells `am` what components make up your application, their types, where to find them,
and how they depend on each other.

This document covers: command aliases, the three-step workflow, build config format,
component types, registry definition, and exit codes.

## Command Aliases

Each command has a short alias:

| Command     | Alias |
| ----------- | ----- |
| `component` | `c`   |
| `fetch`     | `f`   |
| `generate`  | `gen` |
| `validate`  | `v`   |

---

## Workflow

The pipeline consists of three sequential steps:

```
CI/CD pipeline
│
├── 1. component   ─── CI metadata JSON ──────────────> mini-manifest JSON
├── 2. fetch       ─── Helm OCI registry ─────────────> mini-manifest JSON
│                       (runs helm pull, extracts Chart.yaml)
│
└── 3. generate    ─── mini-manifests + build config ─> Application Manifest JSON
```

**Step 1 — `component`**: for each Docker image (and Helm chart whose metadata
comes from CI), convert a CI-produced metadata JSON into a CycloneDX mini-manifest.

**Step 2 — `fetch`**: for Helm charts that must be downloaded at manifest-build time,
run `helm pull` automatically, extract chart metadata, and produce mini-manifests.

**Step 3 — `generate`**: combine all mini-manifests with the build config YAML
into the final Application Manifest.

---

## Build Config YAML

The build config describes the application composition.
It is required by both `fetch` and `generate`.

### Component identity

Each component is uniquely identified by the pair **`(name, mimeType)`**.
This means two entries can share the same `name` as long as their `mimeType` differs —
they are treated as completely separate components.

This is intentional and enables the **umbrella Helm pattern**: a `standalone-runnable`
(the deployment entry point) and a `helm.chart` (the actual Helm chart) often share the
same application name, but are two different artifacts in the manifest.

```yaml
# These are TWO different components, not a duplicate:
- name: qubership-jaeger
  mimeType: application/vnd.nc.standalone-runnable   # deployment entry point

- name: qubership-jaeger
  mimeType: application/vnd.nc.helm.chart            # the Helm chart itself
```

### Fields

| Field                | Required | Description                                                                      |
| -------------------- | -------- | -------------------------------------------------------------------------------- |
| `applicationName`    | yes      | Name of the application (appears in `metadata.component`)                        |
| `applicationVersion` | yes      | Version of the application                                                       |
| `components[]`       | yes      | List of components                                                               |
| `name`               | yes      | Component name. Combined with `mimeType` it forms the unique component identity. |
| `mimeType`           | yes      | Component type — see [Component Types](#component-types)                         |
| `reference`          | no       | OCI URL. Required by `fetch` for Helm charts; also used for PURL generation.     |
| `dependsOn[]`        | no       | List of components this component depends on                                     |
| `valuesPathPrefix`   | no       | Helm values path for a Docker image dependency, e.g. `images.jaeger`. Used by `generate` to produce `artifactMappings` in the final manifest. |

### Example

A full real-world config is available at
[`tests/fixtures/configs/jaeger_full_config.yaml`](../tests/fixtures/configs/jaeger_full_config.yaml).

Annotated minimal example:

```yaml
applicationName: "qubership-jaeger"
applicationVersion: "1.2.3"

components:
  # Deployment entry point — no mini-manifest needed, built from config directly
  - name: qubership-jaeger
    mimeType: application/vnd.nc.standalone-runnable
    dependsOn:
      - name: qubership-jaeger
        mimeType: application/vnd.nc.helm.chart

  # Helm chart — fetched from OCI registry by `am fetch`
  - name: qubership-jaeger
    mimeType: application/vnd.nc.helm.chart
    reference: "oci://sandbox.example.com/charts/qubership-jaeger:1.2.3"
    dependsOn:
      - name: jaeger
        mimeType: application/vnd.docker.image
        valuesPathPrefix: images.jaeger   # key in values.yaml for this image
      - name: envoy
        mimeType: application/vnd.docker.image
        valuesPathPrefix: images.envoy

  # Docker images without reference — mini-manifests provided by CI via `am component`
  - name: jaeger
    mimeType: application/vnd.docker.image

  # Docker images with reference — mini-manifests built by `am fetch` (no hash)
  - name: envoy
    mimeType: application/vnd.docker.image
    reference: "docker.io/envoyproxy/envoy:v1.32.6"
```

---

## Component Types

| `mimeType`                                     | Description                                 |
| ---------------------------------------------- | ------------------------------------------- |
| `application/vnd.nc.standalone-runnable`       | Application entry point                     |
| `application/vnd.nc.helm.chart`                | Helm chart                                  |
| `application/vnd.docker.image`                 | Docker image                                |
| `application/vnd.nc.helm.values.schema`        | `values.schema.json` (nested in helm chart) |
| `application/vnd.nc.resource-profile-baseline` | Resource profiles (nested in helm chart)    |
| `application/vnd.nc.smartplug`                 | SmartPlug                                   |
| `application/vnd.nc.cdn`                       | CDN artifact                                |
| `application/vnd.nc.crd`                       | Custom Resource Definition                  |
| `application/vnd.nc.job`                       | Job                                         |
| `application/vnd.qubership.*`                  | Same types with `qubership` vendor prefix   |

Both `nc` and `qubership` vendor prefixes are supported everywhere and treated as equivalent.

---

## Registry Definition YAML

Optional. Used by `component` and `fetch` for PURL generation.
Maps registry hostnames to a logical name.

```yaml
name: "qubership"
dockerConfig:
  groupUri: "ghcr.io"
  snapshotUri: "snapshot.registry.example.com"
  releaseUri: "release.registry.example.com"
helmAppConfig:
  repositoryDomainName: "oci://registry.qubership.org"
```

Without registry definition — PURL uses raw hostname from `reference`:
```
pkg:docker/core/jaeger@1.0?registry_name=sandbox.example.com
```

With registry definition (`name: qubership`):
```
pkg:docker/core/jaeger@1.0?registry_name=qubership
```

---

## Exit Codes

| Code | Situation                                                                     |
| ---- | ----------------------------------------------------------------------------- |
| 0    | Success (warnings printed to stderr do not affect exit code)                  |
| 1    | Any error: bad config, bad JSON, helm pull failure, schema validation failure |
