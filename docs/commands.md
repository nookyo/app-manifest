# CLI Commands Reference

Full reference for all four `am` commands.

| Command     | Alias | Description                                     |
| ----------- | ----- | ----------------------------------------------- |
| `component` | `c`   | CI metadata JSON to mini-manifest               |
| `fetch`     | `f`   | Helm chart / Docker reference to mini-manifest  |
| `generate`  | `gen` | Mini-manifests + build config to final manifest |
| `validate`  | `v`   | Validate manifest against JSON Schema           |

---

## `component` (`c`) — CI metadata to mini-manifest

Converts a CI-produced metadata JSON file into a CycloneDX mini-manifest
for a single Docker image or Helm chart.

Use this command when the component has already been built and pushed in CI —
the hash, version, and registry address are already known.

```
am component [OPTIONS]

Options:
  -i, --input PATH        CI metadata JSON file                    [required]
  -o, --out PATH          Output mini-manifest JSON file           [required]
  -r, --registry-def PATH Registry Definition YAML                 [optional]
```

### Input format

Your CI pipeline must produce one JSON file per built component and pass it to this command.
`am component` reads that file and converts it into a mini-manifest.

> **Contract**: the `name` and `mime-type` fields must exactly match
> the `name` and `mimeType` of the corresponding entry in `build-config.yaml`.
> If they differ, `am generate` will not be able to find the component.

#### Who creates this file?

The CI metadata JSON is created by your CI system — typically a build script or pipeline step
that runs after `docker push` or `helm push`. It captures what was just built and pushed.

You are responsible for writing this file in your CI pipeline in the format described below.
See [`tests/fixtures/metadata/`](../tests/fixtures/metadata/) for real examples.

#### Docker image

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
Includes nested components embedded in the chart archive
(`values.schema.json`, resource profiles) as base64-encoded attachments:

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

#### Field reference

| Field        | Required | Description                                                                    |
| ------------ | -------- | ------------------------------------------------------------------------------ |
| `name`       | yes      | Component name — must match `name` in `build-config.yaml`                      |
| `type`       | yes      | CycloneDX type: `container` for Docker images, `application` for Helm/other    |
| `mime-type`  | yes      | Component mime-type — must match `mimeType` in `build-config.yaml`             |
| `group`      | no       | Registry namespace or organisation (e.g. `core`, `envoyproxy`)                 |
| `version`    | no       | Image tag or chart version                                                     |
| `hashes`     | no       | List of `{ "alg": "SHA-256", "content": "<hex>" }` objects                     |
| `reference`  | no       | Full address in the registry, used for PURL generation                         |
| `appVersion` | no       | Helm only: application version (may differ from chart version)                 |
| `components` | no       | Helm only: nested components (values.schema.json, resource profiles)           |

Supported hash algorithms: `MD5`, `SHA-1`, `SHA-256`, `SHA-512`.

### Output

A mini-manifest JSON file (CycloneDX BOM with one component in `components[]`).
See [mini-manifests.md](mini-manifests.md) for the format and naming rules.

### Examples

```bash
# Docker image, with PURL registry mapping
am component \
  -i ci-output/jaeger-meta.json \
  -o minis/jaeger.json \
  -r registry-definition.yaml

# Helm chart built in CI (no helm pull needed)
am component \
  -i ci-output/chart-meta.json \
  -o minis/my-chart.json
```

---

## `fetch` (`f`) — download charts and parse image references

Reads the build config and processes all components that have a `reference` field:

- **Helm charts**: downloaded from OCI registries via `helm pull`.
  Chart metadata, embedded files (`values.schema.json`, resource profiles),
  and the SHA-256 hash of the `.tgz` archive are extracted.
- **Docker images**: not downloaded. A mini-manifest is built directly from the
  `reference` field (version and namespace parsed from the URL). No hash is produced.

> **Note**: for Helm chart processing, `helm` CLI must be installed and accessible in `PATH`.
> Docker image processing does not require any external tools.

> **Important**: the `name` in the build config must match the `name` inside `Chart.yaml`
> of the downloaded chart, otherwise `generate` will not find the component.
> See [Chart name vs config name](#important-chart-name-vs-config-name) below.

```
am fetch [OPTIONS]

Options:
  -c, --config PATH       Build config YAML                        [required]
  -o, --out PATH          Output directory for mini-manifest files  [required]
  -r, --registry-def PATH Registry Definition YAML                 [optional]
```

### Which components are processed

`fetch` processes components that satisfy **both** conditions:

1. `mimeType` is a Helm chart type (`application/vnd.nc.helm.chart`, `application/vnd.qubership.helm.chart`)
   or a Docker image (`application/vnd.docker.image`)
2. `reference` field is present and non-empty

Components without `reference` are skipped silently.
`standalone-runnable` components are always skipped.

**What is extracted from a Helm chart:**

| Data                 | Source                       | Written to                                        |
| -------------------- | ---------------------------- | ------------------------------------------------- |
| Chart name           | `Chart.yaml` — `name`        | `component.name`                                  |
| Application version  | `Chart.yaml` — `appVersion`  | `component.version`                               |
| Chart version        | `Chart.yaml` — `version`     | `component.version` (fallback if no `appVersion`) |
| SHA-256 hash         | `.tgz` archive               | `component.hashes[0]`                             |
| PURL                 | `reference` + regdef         | `component.purl`                                  |
| `values.schema.json` | Chart root directory         | Nested component (base64-encoded)                 |
| Resource profiles    | `resource-profiles/*.yaml`   | Nested component (base64-encoded)                 |

### Output file naming

Files are written to `{out-dir}/{name}.json` where `name` comes from the config.

If two components share the same name but have different mime-types, a mime-type suffix
is added to avoid overwriting: `{out-dir}/{name}_{mime_suffix}.json`.
The suffix is the mime-type without `application/`, dots replaced by underscores
(e.g. `vnd_nc_helm_chart`).

See [mini-manifests.md — Naming in `fetch`](mini-manifests.md#naming-in-fetch) for full rules.

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
Component manifest written to minis/envoy.json
```

### Important: chart name vs config name

The **output filename** is based on the `name` from the **config**.
But the `component.name` **inside** the mini-manifest comes from `Chart.yaml`.

If these differ, `generate` will not match the mini-manifest to the config entry,
because matching is done by the name inside the file, not the filename.

**Example of a mismatch:**

```yaml
# build-config.yaml
- name: jaeger-app                   # ← used for the output filename
  mimeType: application/vnd.nc.helm.chart
  reference: "oci://registry.example.com/charts/qubership-jaeger:1.0"
```

```yaml
# Chart.yaml inside the downloaded chart
name: qubership-jaeger               # ← used for matching in generate
```

Result: file is written as `minis/jaeger-app.json`, but inside it says `name: qubership-jaeger`.
When `generate` reads it, it indexes it as `(qubership-jaeger, helm.chart)` but the config
expects `(jaeger-app, helm.chart)` — this produces a **component not found** warning.

**Solution**: keep the `name` in the config consistent with `name` in `Chart.yaml`.

---

## `generate` (`gen`) — assemble the final manifest

Reads all mini-manifests, matches them to the build config,
and assembles the final Application Manifest JSON.

```
am generate [OPTIONS] [COMPONENT_FILES]...

Arguments:
  COMPONENT_FILES    Mini-manifest .json files or directories (all *.json inside)

Options:
  -c, --config PATH    Build config YAML                           [required]
  -o, --out PATH       Output manifest file (JSON)                 [required]
  -v, --version TEXT   Override applicationVersion from config     [optional]
  -n, --name TEXT      Override applicationName from config        [optional]
  --validate           Validate output against JSON Schema         [optional]
```

### Passing mini-manifests

`COMPONENT_FILES` can be files, directories, or a mix:

```bash
# Pass a directory — all *.json files inside are loaded (alphabetical order)
am generate -c cfg.yaml -o out.json minis/

# Pass individual files
am generate -c cfg.yaml -o out.json minis/jaeger.json minis/envoy.json

# Mix of files and directories
am generate -c cfg.yaml -o out.json minis/ extra/custom.json
```

### How components are matched

Matching is done by `(name, mime-type)` read from the mini-manifest content —
**not by filename**. The filename is irrelevant.

See [mini-manifests.md — How generate loads and matches mini-manifests](mini-manifests.md#how-generate-loads-and-matches-mini-manifests).

### Assembly

See [manifest-assembly.md](manifest-assembly.md) for the complete description
of how the final manifest is built from mini-manifests and the build config.

### --validate flag

When `--validate` is passed, after writing the output file `generate` validates it
against the bundled JSON Schema (no network access required).

```bash
am generate -c cfg.yaml -o manifest.json --validate minis/
# Success:
#   Manifest written to manifest.json
#   Manifest is valid.

# Failure:
#   Manifest written to manifest.json
#   Validation FAILED:
#     - components -> 0 -> hashes -> 0 -> content: 'abc' does not match ...
#   Error: Manifest does not conform to JSON Schema
```

On validation failure, the manifest file is still written and exit code 1 is returned.

### Examples

```bash
# Basic
am generate \
  -c build-config.yaml \
  -o manifest.json \
  minis/

# Override version and name (useful in CI for release tagging)
am generate \
  -c build-config.yaml \
  -o manifest.json \
  --version 2.0.0 \
  --name my-app-release \
  minis/

# With schema validation
am generate \
  -c build-config.yaml \
  -o manifest.json \
  --validate \
  minis/
```

---

## `validate` (`v`) — validate manifest against JSON Schema

Validates an existing manifest JSON file against the Application Manifest JSON Schema.
The schema is bundled with the package — no network requests are made.

Use this command to:
- Verify a manifest produced by `am generate` (or re-verify after manual editing)
- Validate a manifest produced by a different tool
- Add a validation-only step in CI without regenerating the manifest

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

---

## Exit codes

All `am` commands follow the same exit code convention:

| Code | Meaning                                                                                    |
| ---- | ------------------------------------------------------------------------------------------ |
| 0    | Success. Warnings printed to stderr do not affect the exit code.                           |
| 1    | Error: invalid input, file not found, YAML/JSON parse error, helm failure, schema invalid. |
