# Build Config Reference

The **build config** (`build-config.yaml`) is a YAML file you write and maintain.
It defines all components that make up your application — Docker images, Helm charts —
their types, registry locations, and how they depend on each other.

Two `am` commands read this file:

- `am fetch` — uses it to know which charts to download and which Docker references to parse
- `am generate` — uses it to assemble the final manifest and build the dependency graph

For the three-step pipeline overview see [README — How it works](../README.md#how-it-works).
For all command options and flags see the [Commands Reference](commands.md).

A full real-world example is available at
[jaeger_full_config.yaml](../tests/fixtures/configs/jaeger_full_config.yaml).

---

## Top-level fields

| Field                | Required | Description                                                                       |
| -------------------- | -------- | --------------------------------------------------------------------------------- |
| `applicationName`    | yes      | Name of the application — written to `metadata.component.name` in the manifest   |
| `applicationVersion` | yes      | Version of the application — used as the version for `standalone-runnable`        |
| `components[]`       | yes      | List of components (see below)                                                    |

---

## Component fields

| Field              | Required | Description                                                                                                                                 |
| ------------------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`             | yes      | Component name. Together with `mimeType` forms the unique identity of the component.                                                        |
| `mimeType`         | yes      | Component type — see [Component types](#component-types)                                                                                    |
| `reference`        | no       | OCI URL of the image or chart. Required by `fetch` for Helm charts. For Docker images, used to generate a mini-manifest without downloading. |
| `dependsOn[]`      | no       | Components this component depends on. Used by `generate` to build `dependencies[]` in the final manifest.                                   |
| `valuesPathPrefix` | no       | Path in `values.yaml` where this Docker image's tag is configured (e.g. `images.jaeger`). **Only valid inside `dependsOn` entries**, not at the component level. |

### Component identity

Each component is uniquely identified by the pair **`(name, mimeType)`**.
Two entries can share the same `name` as long as their `mimeType` differs —
they are treated as two completely separate components.

This is used in the **umbrella Helm pattern**: the deployment entry point (`standalone-runnable`)
and the Helm chart itself often share the application name but are different artifacts:

```yaml
# These are TWO different components, not a duplicate:
- name: qubership-jaeger
  mimeType: application/vnd.nc.standalone-runnable   # the entry point

- name: qubership-jaeger
  mimeType: application/vnd.nc.helm.chart            # the Helm chart
```

---

## Component types

| `mimeType`                                     | Description                                                                 |
| ---------------------------------------------- | --------------------------------------------------------------------------- |
| `application/vnd.nc.standalone-runnable`       | Application entry point — see [Standalone-runnable](#standalone-runnable)   |
| `application/vnd.nc.helm.chart`                | Helm chart                                                                  |
| `application/vnd.docker.image`                 | Docker image                                                                |
| `application/vnd.nc.helm.values.schema`        | `values.schema.json` — extracted from chart automatically, not set manually |
| `application/vnd.nc.resource-profile-baseline` | Resource profiles — extracted from chart automatically, not set manually    |
| `application/vnd.nc.smartplug`                 | SmartPlug artifact                                                          |
| `application/vnd.nc.cdn`                       | CDN artifact                                                                |
| `application/vnd.nc.crd`                       | Custom Resource Definition                                                  |
| `application/vnd.nc.job`                       | Job artifact                                                                |
| `application/vnd.qubership.*`                  | Same types with `qubership` vendor prefix — treated identically to `nc`     |

### Standalone-runnable

A `standalone-runnable` is the **top-level deployment entry point** for your application.
It does not have its own Docker image or Helm chart.
Its purpose is to group all the application's dependencies under one root component
and carry the application version in the final manifest.

- Its `version` in the final manifest is always `applicationVersion` from the build config.
- `am generate` builds it directly from the config — no mini-manifest is needed or looked up.
- Every application config should have exactly one `standalone-runnable`.

```yaml
- name: my-app
  mimeType: application/vnd.nc.standalone-runnable
  dependsOn:
    - name: my-app                         # the Helm chart with the same name
      mimeType: application/vnd.nc.helm.chart
```

---

## Annotated example

```yaml
applicationName: "qubership-jaeger"
applicationVersion: "1.2.3"

components:
  # Entry point — no image, no chart, just groups the dependencies
  - name: qubership-jaeger
    mimeType: application/vnd.nc.standalone-runnable
    dependsOn:
      - name: qubership-jaeger
        mimeType: application/vnd.nc.helm.chart

  # Helm chart — `am fetch` will run `helm pull` and produce a mini-manifest
  - name: qubership-jaeger
    mimeType: application/vnd.nc.helm.chart
    reference: "oci://sandbox.example.com/charts/qubership-jaeger:1.2.3"
    dependsOn:
      # Docker images used by this chart, with their values.yaml path
      - name: jaeger
        mimeType: application/vnd.docker.image
        valuesPathPrefix: images.jaeger    # matches the key in values.yaml
      - name: envoy
        mimeType: application/vnd.docker.image
        valuesPathPrefix: images.envoy

  # Docker image WITHOUT reference:
  # mini-manifest must be provided by CI via `am component`
  - name: jaeger
    mimeType: application/vnd.docker.image

  # Docker image WITH reference:
  # `am fetch` produces the mini-manifest from the URL (no hash, image not downloaded)
  - name: envoy
    mimeType: application/vnd.docker.image
    reference: "docker.io/envoyproxy/envoy:v1.32.6"
```

**Key rule**: if a Docker image has no `reference`, `am fetch` skips it silently.
A mini-manifest for that image must be produced by `am component` using CI metadata.

---

## Registry Definition YAML

Optional. Passed to `am component` or `am fetch` with `-r registry-definition.yaml`.
Controls how PURLs are generated in mini-manifests and the final manifest.

Without a registry definition, PURLs use the raw hostname from the `reference` field.
With a registry definition, PURLs use the logical `name` instead of the hostname.

```yaml
name: "qubership"                              # logical name written into PURLs
dockerConfig:
  groupUri: "ghcr.io"                          # main Docker registry hostname
  snapshotUri: "snapshot.registry.example.com" # hostname for snapshot/dev builds
  releaseUri: "release.registry.example.com"   # hostname for stable release builds
helmAppConfig:
  repositoryDomainName: "oci://registry.qubership.org"  # Helm OCI registry base URL
```

| Field                              | Description                                                          |
| ---------------------------------- | -------------------------------------------------------------------- |
| `name`                             | Logical registry name written into PURLs as `registry_name=...`      |
| `dockerConfig.groupUri`            | Hostname of the main Docker registry                                 |
| `dockerConfig.snapshotUri`         | Hostname used for snapshot or development image builds               |
| `dockerConfig.releaseUri`          | Hostname used for stable or release image builds                     |
| `helmAppConfig.repositoryDomainName` | Base OCI URL of the Helm chart registry                            |

**Effect on PURLs:**

Without registry definition:
```
pkg:docker/core/jaeger@1.0?registry_name=sandbox.example.com
```

With `name: qubership`:
```
pkg:docker/core/jaeger@1.0?registry_name=qubership
```
