# am CLI — Usage

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

```yaml
applicationName: "qubership-jaeger"
applicationVersion: "1.2.3"

components:
  - name: qubership-jaeger
    mimeType: application/vnd.nc.standalone-runnable
    dependsOn:
      - name: qubership-jaeger
        mimeType: application/vnd.nc.helm.chart

  - name: qubership-jaeger
    mimeType: application/vnd.nc.helm.chart
    reference: "oci://sandbox.example.com/charts/qubership-jaeger:1.2.3"
    dependsOn:
      - name: jaeger
        mimeType: application/vnd.docker.image
        valuesPathPrefix: images.jaeger
      - name: envoy
        mimeType: application/vnd.docker.image
        valuesPathPrefix: images.envoy

  - name: jaeger
    mimeType: application/vnd.docker.image

  - name: envoy
    mimeType: application/vnd.docker.image
```

**Fields:**

| Field                | Required | Description                                                                      |
| -------------------- | -------- | -------------------------------------------------------------------------------- |
| `applicationName`    | yes      | Name of the application (appears in `metadata.component`)                        |
| `applicationVersion` | yes      | Version of the application                                                       |
| `components[]`       | yes      | List of components                                                               |
| `name`               | yes      | Component name                                                                   |
| `mimeType`           | yes      | Component type — see [Component Types](#component-types)                         |
| `reference`          | no       | OCI URL; required by `fetch` for helm charts; used for PURL generation           |
| `dependsOn[]`        | no       | List of dependencies                                                             |
| `valuesPathPrefix`   | no       | Path in `values.yaml` for a docker image dependency (used in `artifactMappings`) |

**Component identity** is the pair `(name, mimeType)`. Two entries with the same name
but different `mimeType` are two distinct components — this is the basis for the
umbrella Helm pattern.

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
