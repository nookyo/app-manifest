# CLI Commands Reference

Full reference for all four `am` commands.

Each command has a short alias: `c` = `component`, `f` = `fetch`, `gen` = `generate`, `v` = `validate`.

---

## `component` (`c`) — CI metadata to mini-manifest

Converts a CI-produced metadata JSON file into a CycloneDX mini-manifest
for a single Docker image or Helm chart.

Use this command when the component's hash, version, and registry address
are already known from CI (i.e., the image has already been built and pushed).

```
am component [OPTIONS]

Options:
  -i, --input PATH        CI metadata JSON file                    [required]
  -o, --out PATH          Output mini-manifest JSON file           [required]
  -r, --registry-def PATH Registry Definition YAML                 [optional]
```

### Input format

Your CI pipeline must produce one JSON file per built component.
`am component` reads this file and converts it into a mini-manifest.

> **Contract**: the fields `name` and `mime-type` must exactly match
> the corresponding `name` and `mimeType` in `build-config.yaml`,
> otherwise `am generate` will not be able to find the component.

#### Docker image (typical case)

Produced after `docker build && docker push`:

```json
{
  "name": "jaeger",
  "type": "container",
  "mime-type": "application/vnd.docker.image",
  "group": "core",
  "version": "build3",
  "hashes": [
    {
      "alg": "SHA-256",
      "content": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    }
  ],
  "reference": "sandbox.example.com/core/jaeger:build3"
}
```

#### Helm chart built in CI

Produced after `helm package && helm push`.
Includes nested components extracted from the chart archive
(`values.schema.json`, resource profiles):

```json
{
  "name": "qubership-jaeger",
  "type": "application",
  "mime-type": "application/vnd.nc.helm.chart",
  "version": "1.2.3",
  "appVersion": "1.2.3",
  "hashes": [
    { "alg": "SHA-256", "content": "e3b0c44298fc1c149afbf4c8996fb924..." }
  ],
  "reference": "oci://registry.qubership.org/charts/qubership-jaeger:1.2.3",
  "components": [
    {
      "type": "data",
      "mime-type": "application/vnd.nc.helm.values.schema",
      "name": "values.schema.json",
      "data": [
        {
          "type": "configuration",
          "name": "values.schema.json",
          "contents": {
            "attachment": {
              "contentType": "application/json",
              "encoding": "base64",
              "content": "<base64-encoded values.schema.json>"
            }
          }
        }
      ]
    },
    {
      "type": "data",
      "mime-type": "application/vnd.nc.resource-profile-baseline",
      "name": "resource-profile-baselines",
      "data": [
        {
          "type": "configuration",
          "name": "small.yaml",
          "contents": {
            "attachment": {
              "contentType": "application/yaml",
              "encoding": "base64",
              "content": "<base64-encoded small.yaml>"
            }
          }
        }
      ]
    }
  ]
}
```

Real fixture examples: [`tests/fixtures/metadata/`](../tests/fixtures/metadata/).

#### Field reference

| Field        | Required | Description                                                                    |
| ------------ | -------- | ------------------------------------------------------------------------------ |
| `name`       | yes      | Component name — must match the name in `build-config.yaml`                    |
| `type`       | yes      | CycloneDX component type: `container` for Docker, `application` for Helm/other |
| `mime-type`  | yes      | Component mime-type — must match the `mimeType` in `build-config.yaml`         |
| `group`      | no       | Registry namespace or organisation (e.g. `core`, `envoyproxy`)                 |
| `version`    | no       | Image tag or chart version                                                     |
| `hashes`     | no       | List of `{ "alg": "SHA-256", "content": "<hex>" }` objects                     |
| `reference`  | no       | Full address in the registry, used for PURL generation                         |
| `appVersion` | no       | Helm only: the application version (may differ from chart version)             |
| `components` | no       | Helm only: pre-built nested components (values.schema.json, resource-profiles) |

Supported hash algorithms: `MD5`, `SHA-1`, `SHA-256`, `SHA-512`.

### Output

A mini-manifest JSON file (CycloneDX BOM with one component in `components[]`).
See [mini-manifests.md](mini-manifests.md) for the format and naming rules.

### Examples

```bash
# Docker image
am component \
  -i ci-output/jaeger-meta.json \
  -o minis/jaeger.json \
  -r registry-definition.yaml

# Helm chart from CI (no helm pull needed)
am component \
  -i ci-output/chart-meta.json \
  -o minis/my-chart.json
```

---

## `fetch` (`f`) — artifacts with reference to mini-manifests

> **Important**: the `name` in the build config must match the `name` field inside `Chart.yaml`
> of the downloaded chart, otherwise `generate` will not find the component.
> See [Chart name vs config name](#important-chart-name-vs-config-name) below.

Processes components from the build config that have a `reference` field,
and creates mini-manifests for them:

- **Helm charts**: downloaded from OCI registries via `helm pull`.
  Chart metadata, embedded files, and SHA-256 hash are extracted.
- **Docker images**: no download. A minimal mini-manifest is built directly
  from the `reference` field (version and namespace parsed from the URL).
  The `hashes` field is absent because the image content is not fetched.

Use this command when:
- Helm charts are fetched from a registry at manifest-build time (not built in CI).
- Docker images are third-party and only their registry reference is known.

> **Note:** the `helm` CLI must be installed and accessible in `PATH` only for
> Helm chart processing. Docker image mini-manifests do not require any external tools.

```
am fetch [OPTIONS]

Options:
  -c, --config PATH       Build config YAML                        [required]
  -o, --out PATH          Output directory for mini-manifest files  [required]
  -r, --registry-def PATH Registry Definition YAML                 [optional]
```

### Which components are processed

`fetch` processes all components in the config that satisfy **both** conditions:

1. `mimeType` is one of:
   - a helm chart type: `application/vnd.nc.helm.chart` or `application/vnd.qubership.helm.chart`
   - a Docker image: `application/vnd.docker.image`
2. `reference` field is present and non-empty

Components without `reference` are skipped silently.
`standalone-runnable` components are always skipped (they have no reference).

**Processing by mime-type:**

| `mimeType`                           | How processed                          | `hashes` in output |
| ------------------------------------ | -------------------------------------- | ------------------ |
| `application/vnd.nc.helm.chart`      | `helm pull` + archive inspection       | Present (SHA-256)  |
| `application/vnd.qubership.helm.chart` | `helm pull` + archive inspection     | Present (SHA-256)  |
| `application/vnd.docker.image`       | Reference parsed, no download          | Absent             |

### What is extracted from the chart

For each downloaded chart:

| Data                 | Source                     | Stored as                                         |
| -------------------- | -------------------------- | ------------------------------------------------- |
| Chart name           | `Chart.yaml` — `name`        | `component.name`                                  |
| Application version  | `Chart.yaml` — `appVersion`  | `component.version`                               |
| Chart version        | `Chart.yaml` — `version`     | `component.version` (fallback if no `appVersion`) |
| SHA-256 hash         | `.tgz` archive file        | `component.hashes[0]`                             |
| PURL                 | `reference` + regdef       | `component.purl`                                  |
| `values.schema.json` | Chart root directory       | Nested component (base64-encoded)                 |
| Resource profiles    | `resource-profiles/*.yaml` | Nested component (base64-encoded)                 |

### Output file naming

See [mini-manifests.md — Naming in `fetch`](mini-manifests.md#naming-in-fetch)
for the full rules including collision handling.

Short version:
- Default: `{out-dir}/{name}.json` where `name` comes from the config
- On collision (same name, different mime-type): `{out-dir}/{name}_{mime_suffix}.json` where `mime_suffix` is the mime-type without `application/`, dots replaced by underscores (e.g. `vnd_nc_helm_chart`)

### Examples

```bash
# Basic usage
am fetch \
  -c build-config.yaml \
  -o minis/

# With PURL registry mapping
am fetch \
  -c build-config.yaml \
  -o minis/ \
  -r registry-definition.yaml
```

Console output on success:

```
Component manifest written to minis/qubership-jaeger.json
```

### Important: chart name vs config name

The **output filename** is based on the component `name` from the **config**.
But the `component.name` field **inside** the mini-manifest comes from `Chart.yaml`.

If these differ, `generate` will not be able to match the mini-manifest to the config entry,
because matching is done by the name inside the file, not by the filename.

**Example of a mismatch:**

```yaml
# build-config.yaml
components:
  - name: jaeger-app          # ← config name (used for filename)
    mimeType: application/vnd.nc.helm.chart
    reference: "oci://registry.example.com/charts/qubership-jaeger:1.0"
```

```yaml
# Chart.yaml inside the downloaded chart
name: qubership-jaeger        # ← inner name (used for matching)
```

Result: file is written as `minis/jaeger-app.json`, but inside it has `name: qubership-jaeger`.
When `generate` reads the file, it indexes it as `(qubership-jaeger, helm.chart)`,
but the config expects `(jaeger-app, helm.chart)` — this produces a **component not found warning**.

**Solution**: keep the `name` in the config consistent with the `name` field in `Chart.yaml`.

---

## `generate` (`gen`) — assemble the final manifest

Reads all mini-manifests, matches them to the build config,
and assembles the final Application Manifest JSON.

```
am generate [OPTIONS] [COMPONENT_FILES]...

Arguments:
  COMPONENT_FILES    Mini-manifest .json files or directories (glob *.json)

Options:
  -c, --config PATH    Build config YAML                           [required]
  -o, --out PATH       Output manifest file (JSON)                 [required]
  -v, --version TEXT   Override applicationVersion from config     [optional]
  -n, --name TEXT      Override applicationName from config        [optional]
  --validate           Validate output against JSON Schema         [optional]
```

### Loading mini-manifests

`COMPONENT_FILES` can be a mix of files and directories:

- If a **file** is passed — it is loaded directly.
- If a **directory** is passed — all `*.json` files inside are loaded, in alphabetical order.

```bash
# Files
am generate -c cfg.yaml -o out.json minis/jaeger.json minis/envoy.json

# Directory
am generate -c cfg.yaml -o out.json minis/

# Mix
am generate -c cfg.yaml -o out.json minis/ extra/custom.json
```

### Matching mini-manifests to config

Matching is done by `(name, mime-type)` read from the mini-manifest content —
**not by filename**.

See [mini-manifests.md — Matching in `generate`](mini-manifests.md#how-generate-loads-and-matches-mini-manifests).

### Assembly rules

See [manifest-assembly.md](manifest-assembly.md) for the complete description
of how the final manifest is built.

### --validate flag

When `--validate` is passed, after writing the output file, `generate` validates it
against the bundled JSON Schema (no network access). On failure, errors are printed
to stderr and exit code 1 is returned.

```bash
am generate -c cfg.yaml -o manifest.json --validate minis/
# Success:
# Manifest written to manifest.json
# Manifest is valid.

# Failure:
# Manifest written to manifest.json
# Validation FAILED:
#   - components -> 0 -> hashes -> 0 -> content: 'abc' does not match ...
# Error: Manifest does not conform to JSON Schema
```

### Examples

```bash
# Basic
am generate \
  -c build-config.yaml \
  -o manifest.json \
  minis/

# Override version and name
am generate \
  -c build-config.yaml \
  -o manifest.json \
  --version 2.0.0 \
  --name my-app-release \
  minis/

# With validation
am generate \
  -c build-config.yaml \
  -o manifest.json \
  --validate \
  minis/
```

---

## `validate` (`v`) — validate manifest against JSON Schema

Validates an existing manifest JSON file against the Application Manifest v2 JSON Schema.
The schema is bundled with the package — no network requests are made.

```
am validate [OPTIONS]

Options:
  -i, --input PATH    Manifest JSON file to validate               [required]
```

### Output

**Success** (exit code 0):
```
Manifest is valid: manifest.json
```

**Failure** (exit code 1):
```
Validation FAILED: manifest.json
  - components -> 0 -> hashes -> 0 -> content: 'abc' does not match '^([a-fA-F0-9]{64}|...)$'
  - root: 'bomFormat' is a required property
Error: Manifest does not conform to JSON Schema
```

Each error line shows the JSON path to the failing field and the validation message.

### Examples

```bash
am validate -i manifest.json
am validate -i /path/to/release/manifest.json
```

This command can be used independently — for example, to validate a manifest produced
by a different tool, or to re-validate a manifest after manual editing.
