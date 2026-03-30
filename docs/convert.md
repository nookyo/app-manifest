# `convert` (`cv`) — Convert between DD and AMv2

Converts between two formats:

- **DD -> AMv2**: Deployment Descriptor JSON → Application Manifest v2 (CycloneDX 1.6)
- **AMv2 -> DD**: Application Manifest v2 → Deployment Descriptor JSON

The conversion direction is specified explicitly via `--to-am` or `--to-dd`.

```
am convert [OPTIONS]

Options:
  -i, --input PATH        Input file: DD JSON or AMv2 JSON             [required]
  -o, --out PATH          Output file path                             [required]
  -r, --registry-def PATH Registry Definition YAML                     [required]
  --to-am                 Direction: DD → AMv2                         [required*]
  --to-dd                 Direction: AMv2 → DD                         [required*]
  -c, --config PATH       Build Config YAML (required for DD → AMv2)   [optional]
  -z, --zip PATH          Application ZIP archive (for DD → AMv2)      [optional]
  -n, --name TEXT         Override application name                    [optional]
  -v, --version TEXT      Override application version                 [optional]
```

`*` — exactly one of `--to-am` / `--to-dd` must be specified.

---

## DD → AMv2 (`--to-am`)

Converts a Deployment Descriptor JSON to an Application Manifest v2 (CycloneDX 1.6 BOM).

### When to use

Use this command when you have an existing DD and need to produce an AMv2 from it —
for example, when migrating from the DD-based workflow to AMv2, or when the source of
truth is a DD produced by another tool.

### What is required

| Option           | Required | Purpose                                                                       |
| ---------------- | -------- | ----------------------------------------------------------------------------- |
| `--input`        | yes      | DD JSON file                                                                  |
| `--out`          | yes      | Output AMv2 JSON file                                                         |
| `--registry-def` | yes      | Resolves `full_image_name` / `full_chart_name` → PURL                         |
| `--to-am`        | yes      | Sets direction                                                                |
| `--config`       | yes      | Provides standalone-runnable component, dependency wiring, `valuesPathPrefix` |
| `--zip`          | no       | ZIP archive containing `values.schema.json` and resource profiles             |
| `--name`         | no       | Override app name (default: `applicationName` from build config)              |
| `--version`      | no       | Override app version (default: `applicationVersion` from build config)        |

### Algorithm

The conversion follows these steps:

#### Step 1 — Transform services

For each entry in `DD.services[]`:

- **`image_type: "image"`** → creates one `application/vnd.docker.image` component at the top level
- **`image_type: "service"`** → creates:
  - one `application/vnd.docker.image` component at the top level
  - one `application/vnd.nc.helm.chart` component (the service chart), nested inside the app-chart

Field mapping for docker components:

| DD field                 | AMv2 field                         |
| ------------------------ | ---------------------------------- |
| `image_name`             | `name`                             |
| `docker_repository_name` | `group`                            |
| `docker_tag`             | `version`                          |
| `full_image_name`        | `purl` (via Registry Definition)   |
| `docker_digest`          | `hashes[0].content` (alg: SHA-256) |

Field mapping for service chart components:

| DD field       | AMv2 field |
| -------------- | ---------- |
| `service_name` | `name`     |
| `version`      | `version`  |

#### Step 2 — Transform app-chart

If `DD.charts[]` is non-empty, its first entry becomes an `application/vnd.nc.helm.chart` component.
All service charts from Step 1 are embedded as nested `components[]` of the app-chart.

Field mapping:

| DD field             | AMv2 field                       |
| -------------------- | -------------------------------- |
| `helm_chart_name`    | `name`                           |
| `helm_chart_version` | `version`                        |
| `full_chart_name`    | `purl` (via Registry Definition) |

If `DD.charts[]` is empty, service charts are placed at the top level (no umbrella chart).

#### Step 3 — Create standalone-runnable

A `application/vnd.nc.standalone-runnable` component is created from the Build Config.
Its `name` and `version` come from `--name` / `--version` flags or from the Build Config.

#### Step 4 — Extract ZIP contents (optional)

If `--zip` is provided, the archive is scanned for:

- `values.schema.json` → added as `application/vnd.nc.helm.values.schema` nested component
- `resource-profiles/*.yaml` → added as `application/vnd.nc.resource-profile-baseline` nested component

Both are base64-encoded and attached to the app-chart (or to each service chart if no app-chart).

#### Step 5 — Build dependencies

Dependencies are wired from the Build Config:

- `standalone-runnable` → app-chart + all standalone docker images
- `app-chart` → all nested service charts
- Each service chart → its docker image
- `valuesPathPrefix` from Build Config → `qubership:helm.values.artifactMappings` property on service charts

### PURL generation

`full_image_name` and `full_chart_name` from the DD are converted to PURLs using the Registry Definition:

```
full_image_name: "registry.example.com/namespace/image:tag"
  → pkg:docker/namespace/image@tag?registry_name=<regdef.name>

full_chart_name: "https://registry.example.com/path/chart-1.0.0.tgz"
  → pkg:helm/chart@1.0.0?registry_name=<regdef.name>
```

The registry host in the DD field is matched against `dockerConfig.groupUri` (for Docker)
and `helmAppConfig.repositoryDomainName` (for Helm) in the Registry Definition.
If a match is found, `registry_name` is set to the Registry Definition `name`;
otherwise the host is used as-is.

### Example

```bash
am convert \
  --to-am \
  --input deployment-descriptor.json \
  --out application-manifest.json \
  --registry-def registry-definition.yaml \
  --config build-config.yaml \
  --name "cloud-integration-platform" \
  --version "1.0.0"
```

With optional ZIP:

```bash
am convert \
  --to-am \
  --input deployment-descriptor.json \
  --out application-manifest.json \
  --registry-def registry-definition.yaml \
  --config build-config.yaml \
  --zip cloud-integration-platform-1.0.0.zip
```

---

## AMv2 → DD (`--to-dd`)

Converts an Application Manifest v2 (CycloneDX 1.6 BOM) back to a Deployment Descriptor JSON.

### When to use

Use this command when you need to reconstruct a DD from an AMv2 —
for example, when passing artifact references to legacy deployment tooling that expects DD format.

### What is required

| Option           | Required | Purpose                                                |
| ---------------- | -------- | ------------------------------------------------------ |
| `--input`        | yes      | AMv2 JSON file                                         |
| `--out`          | yes      | Output DD JSON file                                    |
| `--registry-def` | yes      | Resolves PURLs → `full_image_name` / `full_chart_name` |
| `--to-dd`        | yes      | Sets direction                                         |

Build Config is **not** needed — the AMv2 already contains all structural information.

### Algorithm

#### Step 1 — Identify app-chart and service chart mappings

The app-chart is identified as the root-level `application/vnd.nc.helm.chart` component
with a non-empty `components[]` array.

For each nested helm chart (service chart), the `qubership:helm.values.artifactMappings`
property is read to find which docker image bom-ref it maps to.

#### Step 2 — Extract services from docker images

For each `application/vnd.docker.image` at the root level:

- If the docker image has **no associated service chart** → `image_type: "image"`
- If the docker image has an **associated service chart** → `image_type: "service"`, with `service_name` and `version` from the service chart

Field mapping:

| AMv2 field          | DD field                                                       |
| ------------------- | -------------------------------------------------------------- |
| `name`              | `image_name`                                                   |
| `group`             | `docker_repository_name`                                       |
| `version`           | `docker_tag`                                                   |
| `purl`              | `full_image_name`, `docker_registry` (via Registry Definition) |
| `hashes[0].content` | `docker_digest` (SHA-256, without prefix)                      |

#### Step 3 — Extract app-chart

The app-chart component is converted to a `DD.charts[]` entry.

| AMv2 field | DD field                                                     |
| ---------- | ------------------------------------------------------------ |
| `name`     | `helm_chart_name`                                            |
| `version`  | `helm_chart_version`                                         |
| `purl`     | `full_chart_name`, `helm_registry` (via Registry Definition) |

If no app-chart exists, `DD.charts[]` is empty.

#### Step 4 — Populate remaining DD sections

All other DD sections (`metadata`, `include`, `infrastructures`, `configurations`,
`frontends`, `smartplug`, `jobs`, `libraries`, `complexes`, `additionalArtifacts`, `descriptors`)
are output as empty values. They cannot be reconstructed from AMv2.

### PURL → artifact reference conversion

PURLs in AMv2 are converted back to DD artifact references using the Registry Definition:

```
pkg:docker/namespace/image@tag?registry_name=<name>
  → "<dockerConfig.groupUri>/namespace/image:tag"

pkg:helm/chart@1.0.0?registry_name=<name>
  → "https://<helmAppConfig.repositoryDomainName>/<helmGroupRepoName>/chart-1.0.0.tgz"
```

If `registry_name` does not match the Registry Definition `name`, it is used as-is as the registry host.

### What is NOT reconstructed

The following AMv2 components are not included in the DD output (ignored):

| AMv2 component                                 | Reason                     |
| ---------------------------------------------- | -------------------------- |
| `application/vnd.nc.standalone-runnable`       | No DD equivalent           |
| `application/vnd.nc.helm.values.schema`        | Embedded in ZIP, not in DD |
| `application/vnd.nc.resource-profile-baseline` | Embedded in ZIP, not in DD |

### Example

```bash
am convert \
  --to-dd \
  --input application-manifest.json \
  --out deployment-descriptor.json \
  --registry-def registry-definition.yaml
```

---

## Round-trip

The two directions are designed to be inverse of each other.
A DD → AMv2 → DD round-trip preserves:

- All `services[]` entries (count, `image_name`, `image_type`, `full_image_name`, `docker_digest`)
- All `charts[]` entries (count, `helm_chart_name`, `helm_chart_version`, `full_chart_name`)

Fields that are **not** preserved in round-trip:

| Field                                      | Reason                                  |
| ------------------------------------------ | --------------------------------------- |
| `docker_registry`                          | Reconstructed from PURL on the way back |
| `helm_registry`                            | Reconstructed from PURL on the way back |
| DD sections other than `services`/`charts` | Not represented in AMv2                 |
| `bom-ref` values                           | Regenerated on each conversion          |

---

## Exit codes

| Code | Meaning                                                            |
| ---- | ------------------------------------------------------------------ |
| 0    | Success. Warnings (if any) are printed to stderr.                  |
| 1    | Error: missing required option, invalid input, PURL parse failure. |

---

## Warnings

Non-fatal issues are printed to stderr with a `WARNING:` prefix. The output file is still written.

| Warning                                       | Meaning                                                           |
| --------------------------------------------- | ----------------------------------------------------------------- |
| `cannot generate PURL for 'X'`                | `full_image_name` could not be parsed — PURL field will be absent |
| `cannot convert PURL to artifact ref for 'X'` | PURL format unexpected — `full_image_name` will be empty          |
| `ZIP file not found: ...`                     | `--zip` path does not exist — ZIP step is skipped                 |
| `cannot open ZIP file '...'`                  | ZIP file is corrupt or not a valid ZIP archive                    |
