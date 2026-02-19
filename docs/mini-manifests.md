# Mini-manifests: Format, Naming, and Collisions

A **mini-manifest** is an intermediate CycloneDX BOM file that describes a single component.
It is produced by the `component` or `fetch` commands, and consumed by `generate`.

---

## Contents

- [Mini-manifests: Format, Naming, and Collisions](#mini-manifests-format-naming-and-collisions)
  - [Contents](#contents)
  - [What is a mini-manifest](#what-is-a-mini-manifest)
  - [Mini-manifest format](#mini-manifest-format)
    - [Docker image mini-manifest](#docker-image-mini-manifest)
      - [From CI metadata (`component` command)](#from-ci-metadata-component-command)
      - [From config reference (`fetch` command)](#from-config-reference-fetch-command)
    - [Helm chart mini-manifest](#helm-chart-mini-manifest)
  - [File naming rules](#file-naming-rules)
    - [Naming in `component`](#naming-in-component)
    - [Naming in `fetch`](#naming-in-fetch)
    - [Collision detection and resolution in `fetch`](#collision-detection-and-resolution-in-fetch)
  - [How `generate` loads and matches mini-manifests](#how-generate-loads-and-matches-mini-manifests)
    - [Loading order](#loading-order)
    - [Indexing by (name, mime-type)](#indexing-by-name-mime-type)
    - [Collision in the index](#collision-in-the-index)
    - [Component not found](#component-not-found)
  - [Warnings reference](#warnings-reference)

--- v

## What is a mini-manifest

A mini-manifest is a regular CycloneDX 1.6 BOM file with exactly **one component**
in the top-level `components[]` array.

```
mini-manifest (CycloneDX BOM)
├── metadata       ← tool info + timestamp
└── components[0]  ← the described component (Docker image or Helm chart)
```

Mini-manifests serve as the **unit of work** in the pipeline:
each component is processed independently, producing its own mini-manifest,
and then `generate` assembles them into the final Application Manifest.

**Why not build everything in one step?**

- Docker images and Helm charts are built/fetched in different CI jobs at different times.
- Mini-manifests can be produced in parallel, cached, and reused across releases.
- `generate` can be re-run with different configs without re-fetching any charts.

---

## Mini-manifest format

### Docker image mini-manifest

A Docker image mini-manifest can be produced in two ways, which differ in whether the hash is present.

#### From CI metadata (`component` command)

The image has already been built and pushed in CI.
The hash is known from the build output and is written to the mini-manifest.

```json
{
  "serialNumber": "urn:uuid:3aa317a2-f1c2-4b3e-b9a4-000000000001",
  "bomFormat": "CycloneDX",
  "specVersion": "1.6",
  "version": 1,
  "metadata": {
    "timestamp": "2026-02-17T13:00:00Z",
    "component": {
      "bom-ref": "am-build-cli:00000001-0000-0000-0000-000000000000",
      "type": "application",
      "name": "am-build-cli",
      "version": "0.1.0"
    },
    "tools": {
      "components": [
        { "type": "application", "name": "am-build-cli", "version": "0.1.0" }
      ]
    }
  },
  "components": [
    {
      "bom-ref": "jaeger:cb021ef2-3a47-4f1a-b09c-1234567890ab",
      "type": "container",
      "mime-type": "application/vnd.docker.image",
      "name": "jaeger",
      "version": "build3",
      "group": "core",
      "purl": "pkg:docker/core/jaeger@build3?registry_name=sandbox.example.com",
      "hashes": [
        { "alg": "SHA-256", "content": "a1b2c3d4e5f6..." }
      ]
    }
  ],
  "dependencies": []
}
```

#### From config reference (`fetch` command)

When a Docker image component in the build config has a `reference` field,
`fetch` creates a minimal mini-manifest directly from that reference
**without downloading the image** — so the hash is absent.

```yaml
# build-config.yaml
components:
  - name: envoy
    mimeType: application/vnd.docker.image
    reference: "docker.io/envoyproxy/envoy:v1.32.6"
```

Produces:

```json
{
  "components": [
    {
      "bom-ref": "envoy:9a1b2c3d-...",
      "type": "container",
      "mime-type": "application/vnd.docker.image",
      "name": "envoy",
      "version": "v1.32.6",
      "group": "envoyproxy",
      "purl": "pkg:docker/envoyproxy/envoy@v1.32.6?registry_name=docker.io"
    }
  ]
}
```

Note the absence of the `hashes` field — the image was not downloaded, only the reference was parsed.

The `name` in the mini-manifest is taken from the **config** (`envoy`), not from the reference,
to ensure that `generate` can match it to the config entry.

**Reference format parsing rules:**

| Reference format                     | `name`    | `version` | `group`      | Default registry |
| ------------------------------------ | --------- | --------- | ------------ | ---------------- |
| `registry.io/org/image:tag`          | `image`   | `tag`     | `org`        | `registry.io`    |
| `docker.io/envoyproxy/envoy:v1.32.6` | `envoy`   | `v1.32.6` | `envoyproxy` | `docker.io`      |
| `ghcr.io/netcracker/jaeger:1.0`      | `jaeger`  | `1.0`     | `netcracker` | `ghcr.io`        |
| `myorg/myimage:v1.0`                 | `myimage` | `v1.0`    | `myorg`      | `docker.io`      |
| `ubuntu:22.04`                       | `ubuntu`  | `22.04`   | `library`    | `docker.io`      |

Key fields of the component:

| Field       | Description                                                                                               |
| ----------- | --------------------------------------------------------------------------------------------------------- |
| `bom-ref`   | Local identifier: `{name}:{uuid4}`. Regenerated by `generate` in the final manifest.                      |
| `type`      | CycloneDX component type: `container` for Docker                                                          |
| `mime-type` | Must match `mimeType` in build config                                                                     |
| `name`      | Must match `name` in build config                                                                         |
| `version`   | Image tag (from CI metadata or parsed from reference)                                                     |
| `group`     | Registry namespace / organisation                                                                         |
| `purl`      | Package URL (generated from `reference` + registry definition)                                            |
| `hashes`    | Present when produced by `component` from CI metadata; **absent** when produced by `fetch` from reference |

### Helm chart mini-manifest

Produced by `am fetch` (via `helm pull`) or by `am component`
(when helm metadata comes from CI).

```json
{
  "serialNumber": "urn:uuid:...",
  "bomFormat": "CycloneDX",
  "specVersion": "1.6",
  "version": 1,
  "metadata": { "..." },
  "components": [
    {
      "bom-ref": "qubership-jaeger:9688fe60-...",
      "type": "application",
      "mime-type": "application/vnd.nc.helm.chart",
      "name": "qubership-jaeger",
      "version": "1.2.3",
      "purl": "pkg:helm/charts/qubership-jaeger@1.2.3?registry_name=sandbox.example.com",
      "hashes": [
        { "alg": "SHA-256", "content": "5c85a95b..." }
      ],
      "components": [
        {
          "bom-ref": "values.schema.json:a0875db6-...",
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
                  "content": "eyJhcHBsaWNhdGlvbi9zY2hlbWEiOiB7fX0="
                }
              }
            }
          ]
        },
        {
          "bom-ref": "resource-profile-baselines:b1c2d3e4-...",
          "type": "data",
          "mime-type": "application/vnd.nc.resource-profile-baseline",
          "name": "resource-profile-baselines",
          "data": [
            {
              "type": "configuration",
              "name": "default.yaml",
              "contents": {
                "attachment": {
                  "contentType": "application/yaml",
                  "encoding": "base64",
                  "content": "cmVzb3VyY2VzOiB7fQ=="
                }
              }
            }
          ]
        }
      ]
    }
  ],
  "dependencies": []
}
```

The helm chart component contains **nested components** in its own `components[]`:

| Nested component             | `mime-type`                                    | Source                                  |
| ---------------------------- | ---------------------------------------------- | --------------------------------------- |
| `values.schema.json`         | `application/vnd.nc.helm.values.schema`        | `{chart-root}/values.schema.json`       |
| `resource-profile-baselines` | `application/vnd.nc.resource-profile-baseline` | `{chart-root}/resource-profiles/*.yaml` |

Both are embedded as base64-encoded `attachment` objects.
If a chart does not have `values.schema.json` or a `resource-profiles/` directory,
the corresponding nested component is simply absent.

The top-level `components[]` is **always present**, even for charts with no nested
components (it will be an empty array `[]`). This satisfies the JSON Schema requirement.

---

## File naming rules

### Naming in `component`

The output path is **fully controlled by the caller** via the `-o` option.
The tool writes exactly one file to that path.

```bash
am component -i meta.json -o minis/jaeger.json
am component -i meta.json -o output/release-001.json
am component -i meta.json -o /tmp/artifact.json
```

There is **no automatic naming** and **no collision detection** in `component`.
If the specified file already exists, it is silently overwritten.

> **Rule**: the filename you choose has **no effect on how `generate` matches the
> mini-manifest** to a build config entry. Matching is done by the `name` and `mime-type`
> fields inside the file, not by the filename. You can name the file anything.

### Naming in `fetch`

`fetch` writes files **automatically** to the output directory,
because it processes multiple components in a single call.

**Default naming scheme:**

```
{out-dir}/{config-name}.json
```

Where `{config-name}` is the `name` field of the component in `build-config.yaml`.

**Example:**

```yaml
# build-config.yaml
components:
  - name: qubership-jaeger        # ← this becomes the filename base
    mimeType: application/vnd.nc.helm.chart
    reference: "oci://..."
```

```
minis/qubership-jaeger.json       ← output file
```

### Collision detection and resolution in `fetch`

A collision occurs when two components in the config share the **same name** but have
**different mime-types**. Without special handling, both would be written to the same
file, with the second overwriting the first.

`fetch` detects collisions **before writing any file** by counting occurrences of each name.
If a name appears more than once, all components with that name use a **mime-type suffix** filename:

```
{out-dir}/{config-name}_{mime_suffix}.json
```

**Deriving the suffix from mime-type:**

The suffix is everything after `application/`, with dots replaced by underscores:

```
application/vnd.nc.helm.chart       ->  vnd_nc_helm_chart
application/vnd.qubership.helm.chart ->  vnd_qubership_helm_chart
application/vnd.docker.image        ->  vnd_docker_image
```

This guarantees uniqueness — two components with the same name but different mime-types
always produce different filenames, even if their vendor prefix is the same.

**Full collision example:**

Config:
```yaml
components:
  - name: my-chart
    mimeType: application/vnd.nc.helm.chart
    reference: "oci://registry-nc.example.com/charts/my-chart:1.0"

  - name: my-chart
    mimeType: application/vnd.qubership.helm.chart
    reference: "oci://registry-qs.example.com/charts/my-chart:1.0"
```

Without collision detection, both would go to `minis/my-chart.json`.

With collision detection, `fetch` writes:

```
minis/my-chart_vnd_nc_helm_chart.json          ← application/vnd.nc.helm.chart
minis/my-chart_vnd_qubership_helm_chart.json   ← application/vnd.qubership.helm.chart
```

And prints to stderr:
```
WARNING: duplicate component name 'my-chart' — using filename 'my-chart_vnd_nc_helm_chart.json' to avoid collision
WARNING: duplicate component name 'my-chart' — using filename 'my-chart_vnd_qubership_helm_chart.json' to avoid collision
```

The collision warning is printed for **every** duplicate, including the first one.
Exit code remains 0.

**Edge case:**

If the mime-type contains no `/`, the suffix falls back to `"unknown"`, producing `{name}_unknown.json`.

---

## How `generate` loads and matches mini-manifests

### Loading order

`generate` accepts positional arguments: a mix of files and directories.

```bash
am generate -c cfg.yaml -o out.json FILE_OR_DIR ...
```

Processing rules:

1. Each argument is evaluated:
   - If it is a **file** — loaded directly.
   - If it is a **directory** — all `*.json` files inside are found and sorted **alphabetically by filename**, then loaded in that order.
2. Files from explicit arguments are loaded in the order they appear on the command line.
3. Mixing is allowed: `minis/ extra/custom.json` loads all files from `minis/` first (alphabetically), then `extra/custom.json`.

```bash
# All equivalent — matching is by content, not by filename
am generate -c cfg.yaml -o out.json minis/
am generate -c cfg.yaml -o out.json minis/jaeger.json minis/envoy.json minis/chart.json
```

### Indexing by (name, mime-type)

Each loaded file is parsed and the **first element** of `components[]` is extracted.
The component is indexed by the key:

```
key = (component["name"], component["mime-type"])
```

**This means:**

- The filename is irrelevant — only the content is used for matching.
- `minis/artifact-007.json` containing `{ "name": "jaeger", "mime-type": "application/vnd.docker.image" }`
  will be correctly matched to the config entry `{ name: jaeger, mimeType: application/vnd.docker.image }`.

When `generate` looks up a config component in the index, it constructs the same key:
```python
key = (comp_config.name, comp_config.mime_type.value)
```

### Collision in the index

If two different files contain a component with the **same `name` and `mime-type`**,
the second file processed **silently overwrites** the first entry in the index.

**No warning is issued.**

Loading order determines which file wins:
- Within a directory: alphabetical order (e.g., `a.json` before `b.json`).
- Across multiple arguments: left-to-right order on the command line.

To avoid this problem, do not place two files describing the same component in the
same input set. If you intentionally want to override a component, place the preferred
file later in the argument list (or give it a lexicographically later filename in the directory).

### Component not found

If the build config contains a component `(name, mimeType)` that is not present in any
loaded mini-manifest, `generate` **skips** that component and prints a warning to stderr:

```
WARNING: component 'my-service' (application/vnd.docker.image) not found in mini-manifests — skipped
```

Effects of a missing component:
- It does not appear in `components[]` of the final manifest.
- It does not appear in any `dependsOn[]` list in `dependencies[]` — other components
  that depended on it will have an incomplete dependency list.
- Exit code remains **0**. The manifest is still written.
- `--validate` may subsequently fail if the JSON Schema requires certain components.

**`standalone-runnable` is never looked up** — it is always created from the build config
directly, so it never produces this warning.

---

## Warnings reference

| Warning                                                                  | Command    | Cause                                                  | Effect                                       |
| ------------------------------------------------------------------------ | ---------- | ------------------------------------------------------ | -------------------------------------------- |
| `component '{name}' ({mime-type}) not found in mini-manifests — skipped` | `generate` | Component in config has no matching mini-manifest      | Component skipped; exit code 0               |
| `duplicate component name '{name}' — using filename '{file}'`            | `fetch`    | Two config entries share the same name                 | Mime-type suffix filename used; exit code 0  |
| `Multiple .tgz files found after helm pull, using first: ...`            | `fetch`    | `helm pull` wrote more than one `.tgz` to the temp dir | First file (sorted) is used; exit code 0     |
| `no group for component '{name}' (reference '...' has no namespace/org)` | `fetch`    | Docker reference has no org, e.g. `docker.io/envoy:v1` | `group` absent in mini-manifest; exit code 0 |

All warnings go to **stderr** only and never affect the exit code.
