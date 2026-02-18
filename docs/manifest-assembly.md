# Manifest Assembly: How `generate` Builds the Final Manifest

This document describes the complete algorithm used by `am generate`
to assemble the final Application Manifest from a build config and mini-manifests.

---

## Contents

- [Inputs](#inputs)
- [Step 1 — Identify sub-charts](#step-1--identify-sub-charts)
- [Step 2 — Assign bom-refs](#step-2--assign-bom-refs)
- [Step 3 — Build top-level components](#step-3--build-top-level-components)
  - [standalone-runnable](#standalone-runnable)
  - [docker image](#docker-image)
  - [helm chart](#helm-chart)
    - [Properties: isLibrary](#properties-islibrary)
    - [Properties: artifactMappings](#properties-artifactmappings)
    - [Nested components from mini-manifest](#nested-components-from-mini-manifest)
    - [Sub-charts as nested components](#sub-charts-as-nested-components)
    - [Version selection](#version-selection)
- [Step 4 — Build the dependency graph](#step-4--build-the-dependency-graph)
  - [App dependency](#app-dependency)
  - [Component dependencies](#component-dependencies)
  - [Sub-chart dependencies](#sub-chart-dependencies)
- [Step 5 — Build metadata](#step-5--build-metadata)
- [Step 6 — Assemble and write the BOM](#step-6--assemble-and-write-the-bom)
- [bom-ref format](#bom-ref-format)
- [Umbrella Helm (app-chart) example](#umbrella-helm-app-chart-example)
- [Final manifest structure reference](#final-manifest-structure-reference)

---

## Inputs

| Input | Source |
|-------|--------|
| Build config | `--config` YAML file |
| Mini-manifests | Positional `COMPONENT_FILES` arguments (files or directories) |
| Version override | `--version` option (overrides `applicationVersion` from config) |
| Name override | `--name` option (overrides `applicationName` from config) |

The mini-manifests are loaded into an index keyed by `(name, mime-type)`.
See [mini-manifests.md — Indexing](mini-manifests.md#indexing-by-name-mime-type).

---

## Step 1 — Identify sub-charts

Before building any components, `generate` scans the config to find **sub-charts**.

**Definition**: a component is a sub-chart if some helm chart in the config depends on it
via `dependsOn` and the dependency's `mimeType` is also a helm chart type.

```
helm chart A
  dependsOn:
    - name: B
      mimeType: application/vnd.nc.helm.chart   ← B is a sub-chart
    - name: img
      mimeType: application/vnd.docker.image    ← not a sub-chart
```

Sub-charts are collected into a set of `(name, mimeType)` pairs.

**Consequences of being a sub-chart:**
- The sub-chart is **not** placed in the top-level `components[]`.
- It is **nested** inside the parent helm chart's `components[]`.
- It **still appears** in `dependencies[]` (with its own docker image dependencies).

---

## Step 2 — Assign bom-refs

Before building any component, a `bom-ref` is generated for **every** component in
the config (including sub-charts). All bom-refs are pre-computed and stored in a lookup
table keyed by `(name, mimeType)`.

This ensures that bom-refs in `dependencies[]` and `artifactMappings` are consistent
with the bom-refs of the actual components.

**bom-ref format:**

```
{name}:{uuid4}
```

Where `{uuid4}` is a randomly generated UUID4. Example:

```
jaeger:cb021ef2-3a47-4f1a-b09c-1234567890ab
```

A separate bom-ref is generated for the application itself (from `metadata.component`):

```
{applicationName}:{uuid4}
```

See [bom-ref format](#bom-ref-format) for details.

---

## Step 3 — Build top-level components

For each component in the config that is **not** a sub-chart, a top-level component
is built according to its type.

### standalone-runnable

Built **entirely from the build config** — no mini-manifest is needed or looked up.

```yaml
# Config entry:
- name: my-app
  mimeType: application/vnd.nc.standalone-runnable
```

```json
// Resulting component:
{
  "bom-ref": "my-app:xxxxxxxx-...",
  "type": "application",
  "mime-type": "application/vnd.nc.standalone-runnable",
  "name": "my-app",
  "version": "<applicationVersion>",
  "properties": [],
  "components": []
}
```

- `version` is always `applicationVersion` (from config or `--version` override).
- `properties` and `components` are always empty arrays (required by JSON Schema).
- This component type **never** produces a "not found" warning.

### docker image

Taken **directly from the mini-manifest**. Only the `bom-ref` is replaced
(the one pre-computed in step 2). All other fields — `name`, `version`, `group`,
`purl`, `hashes` — come unchanged from the mini-manifest.

```json
// Mini-manifest component:
{
  "bom-ref": "jaeger:old-ref-from-fetch",
  "type": "container",
  "mime-type": "application/vnd.docker.image",
  "name": "jaeger",
  "version": "build3",
  "purl": "pkg:docker/...",
  "hashes": [{ "alg": "SHA-256", "content": "..." }]
}

// After generate — bom-ref replaced, everything else preserved:
{
  "bom-ref": "jaeger:cb021ef2-...",   ← new, pre-computed bom-ref
  "type": "container",
  "mime-type": "application/vnd.docker.image",
  "name": "jaeger",
  "version": "build3",
  "purl": "pkg:docker/...",
  "hashes": [{ "alg": "SHA-256", "content": "..." }]
}
```

### helm chart

Built from the mini-manifest with **additional enrichment**:
`properties`, nested components, and sub-charts are added or updated.

#### Properties: isLibrary

Every helm chart component in the final manifest has a `properties[]` array containing:

```json
{ "name": "isLibrary", "value": false }
```

This field is always present, always `false`.

#### Properties: artifactMappings

If the helm chart config entry has Docker image dependencies with `valuesPathPrefix`,
a second property is added:

```json
{
  "name": "qubership:helm.values.artifactMappings",
  "value": {
    "{docker-bom-ref}": { "valuesPathPrefix": "{prefix}" },
    "{docker-bom-ref}": { "valuesPathPrefix": "{prefix}" }
  }
}
```

**Rules for artifactMappings:**
- Only `dependsOn` entries with a **non-helm mime-type** (i.e., Docker images) are included.
- Only entries where `valuesPathPrefix` is **not null** are included.
- The key is the **pre-computed bom-ref** of the Docker image dependency.
- The value is `{ "valuesPathPrefix": "..." }`.

**Example:**

```yaml
# Config:
- name: qubership-jaeger
  mimeType: application/vnd.nc.helm.chart
  dependsOn:
    - name: jaeger
      mimeType: application/vnd.docker.image
      valuesPathPrefix: images.jaeger       # ← included
    - name: envoy
      mimeType: application/vnd.docker.image
      valuesPathPrefix: images.envoy        # ← included
    - name: sub-chart
      mimeType: application/vnd.nc.helm.chart   # ← excluded (helm type)
    - name: no-prefix-image
      mimeType: application/vnd.docker.image
      # valuesPathPrefix absent              # ← excluded (null prefix)
```

```json
// Resulting property:
{
  "name": "qubership:helm.values.artifactMappings",
  "value": {
    "jaeger:cb021ef2-...": { "valuesPathPrefix": "images.jaeger" },
    "envoy:a0d218af-...":  { "valuesPathPrefix": "images.envoy" }
  }
}
```

If there are no qualifying dependencies, the `artifactMappings` property is **omitted**
entirely (only `isLibrary` remains).

#### Nested components from mini-manifest

A helm chart mini-manifest may contain nested components in its own `components[]`:

- `values.schema.json` (mime-type: `application/vnd.nc.helm.values.schema`)
- `resource-profile-baselines` (mime-type: `application/vnd.nc.resource-profile-baseline`)

These are carried over into the final manifest unchanged, except that their `bom-ref`
is regenerated in the format `{name}:{uuid4}`.

#### Sub-charts as nested components

If the helm chart is an **app-chart (umbrella)** — i.e., it has helm charts in its
`dependsOn` — each such sub-chart is built and inserted into the helm chart's `components[]`,
**after** the values.schema.json and resource-profiles from the mini-manifest.

A sub-chart component in the final manifest:

```json
{
  "bom-ref": "qip-engine:xxxxxxxx-...",
  "type": "application",
  "mime-type": "application/vnd.nc.helm.chart",
  "name": "qip-engine",
  "properties": [
    { "name": "isLibrary", "value": false },
    {
      "name": "qubership:helm.values.artifactMappings",
      "value": {
        "qip-engine-image:yyyyyyyy-...": { "valuesPathPrefix": "image" }
      }
    }
  ],
  "components": []
}
```

Sub-charts are built from the **config** (not from a mini-manifest).
Sub-charts do **not** get version, hashes, or purl in the final manifest —
only name, bom-ref, properties, and an empty components array.

#### Version selection

The `version` of a helm chart in the final manifest is selected as follows:

1. Use `version` from the mini-manifest component (which comes from `Chart.yaml` — `appVersion`,
   or `Chart.yaml` — `version` as fallback).
2. If the mini-manifest has no `version` — fall back to `applicationVersion` from the build config.

---

## Step 4 — Build the dependency graph

The `dependencies[]` array describes which components depend on which.
Each entry has the form:

```json
{ "ref": "{bom-ref}", "dependsOn": ["{bom-ref}", ...] }
```

An entry is only created when `dependsOn` is non-empty. Components with no dependencies
do not get an entry in `dependencies[]`.

### App dependency

The application itself (from `metadata.component`) depends on all **top-level** components:

```json
{
  "ref": "{app-bom-ref}",
  "dependsOn": [
    "{standalone-bom-ref}",
    "{helm-chart-bom-ref}",
    "{docker-bom-ref}",
    ...
  ]
}
```

Sub-charts are **not** included here — they are top-level only in terms of the config,
but in the final manifest they are nested and excluded from this list.

### Component dependencies

For each top-level component (excluding sub-charts) that has a `dependsOn` list:

```json
{
  "ref": "{component-bom-ref}",
  "dependsOn": ["{dep-bom-ref}", "{dep-bom-ref}", ...]
}
```

- The order of `dependsOn` follows the order of `dependsOn` in the config.
- All dependency types are included: docker images, helm charts, sub-charts — as long
  as they have bom-refs (i.e., they are listed in the config).

### Sub-chart dependencies

Sub-charts also get their own entry in `dependencies[]`:

```json
{
  "ref": "{sub-chart-bom-ref}",
  "dependsOn": ["{docker-image-bom-ref}", ...]
}
```

This makes the full dependency chain visible, even though sub-charts are nested inside
the parent helm chart's `components[]`.

---

## Step 5 — Build metadata

The `metadata` section of the final manifest:

```json
{
  "timestamp": "2026-02-17T13:46:12Z",
  "component": {
    "bom-ref": "{applicationName}:{uuid4}",
    "type": "application",
    "mime-type": "application/vnd.nc.application",
    "name": "{applicationName}",
    "version": "{applicationVersion}"
  },
  "tools": {
    "components": [
      { "type": "application", "name": "am-build-cli", "version": "0.1.0" }
    ]
  }
}
```

- `timestamp` is the current UTC time in `YYYY-MM-DDTHH:MM:SSZ` format.
- `applicationName` and `applicationVersion` come from the build config, unless
  `--name` or `--version` overrides are provided on the command line.
- The application's `bom-ref` is the root reference used in the first `dependencies[]` entry.

---

## Step 6 — Assemble and write the BOM

The final BOM is assembled from all the above parts and written as formatted JSON:

```json
{
  "serialNumber": "urn:uuid:{uuid4}",
  "$schema": "...",
  "bomFormat": "CycloneDX",
  "specVersion": "1.6",
  "version": 1,
  "metadata": { ... },
  "components": [ ... ],
  "dependencies": [ ... ]
}
```

- `serialNumber` is a freshly generated UUID4 on every run.
- Fields with `null` values are excluded from the output (`exclude_none=True`).
- JSON is indented with 2 spaces, UTF-8 encoded, no ASCII escaping of non-ASCII characters.

---

## bom-ref format

All `bom-ref` values in the final manifest follow the same pattern:

```
{name}:{uuid4}
```

Examples:
```
jaeger:cb021ef2-3a47-4f1a-b09c-1234567890ab
qubership-jaeger:9688fe60-0000-4000-a000-000000000001
values.schema.json:a0875db6-1234-5678-abcd-ef0123456789
```

**Key properties of bom-refs:**

- **Random** — UUID4 is generated fresh on every `generate` run.
- **Non-deterministic** — the same input always produces different bom-refs.
- **Local scope** — bom-refs are identifiers within a single BOM file only.
  They are not stable across runs and must not be stored or compared externally.
- **Consistent within one run** — a bom-ref assigned to a component in step 2
  is the same bom-ref used in `dependencies[]` and `artifactMappings`.

The `serialNumber` at the BOM root level is similarly regenerated (UUID4) on every run.

---

## Umbrella Helm (app-chart) example

An umbrella helm chart is a chart that contains sub-charts.
In the build config, this is expressed as a helm chart depending on other helm charts.

**Config:**

```yaml
applicationName: "qubership-integration-platform"
applicationVersion: "1.2.3"

components:
  - name: qubership-integration-platform
    mimeType: application/vnd.nc.standalone-runnable
    dependsOn:
      - name: qubership-integration-platform
        mimeType: application/vnd.nc.helm.chart

  - name: qubership-integration-platform
    mimeType: application/vnd.nc.helm.chart
    dependsOn:
      - name: qip-engine            # ← helm type, becomes sub-chart
        mimeType: application/vnd.nc.helm.chart
      - name: qip-runtime-catalog   # ← helm type, becomes sub-chart
        mimeType: application/vnd.nc.helm.chart

  - name: qip-engine
    mimeType: application/vnd.nc.helm.chart
    dependsOn:
      - name: qip-engine-image
        mimeType: application/vnd.docker.image
        valuesPathPrefix: image

  - name: qip-runtime-catalog
    mimeType: application/vnd.nc.helm.chart
    dependsOn:
      - name: qip-catalog-image
        mimeType: application/vnd.docker.image
        valuesPathPrefix: image

  - name: qip-engine-image
    mimeType: application/vnd.docker.image

  - name: qip-catalog-image
    mimeType: application/vnd.docker.image
```

**Sub-chart detection (step 1):**

The app-chart `qubership-integration-platform (helm)` has two helm deps, so
`qip-engine` and `qip-runtime-catalog` are sub-charts.

**Top-level `components[]` in final manifest:**

```
components[]
├── qubership-integration-platform (standalone-runnable)
├── qubership-integration-platform (helm / app-chart)
│   └── components[]
│       ├── qip-engine (sub-chart)
│       │   └── artifactMappings: qip-engine-image
│       └── qip-runtime-catalog (sub-chart)
│           └── artifactMappings: qip-catalog-image
├── qip-engine-image (docker)
└── qip-catalog-image (docker)
```

**`dependencies[]` in final manifest:**

```json
[
  {
    "ref": "qubership-integration-platform:{uuid}",   // app
    "dependsOn": [
      "qubership-integration-platform:{uuid}",        // standalone
      "qubership-integration-platform:{uuid}",        // helm (app-chart)
      "qip-engine-image:{uuid}",
      "qip-catalog-image:{uuid}"
    ]
  },
  {
    "ref": "qubership-integration-platform:{uuid}",   // standalone
    "dependsOn": [
      "qubership-integration-platform:{uuid}"         // helm (app-chart)
    ]
  },
  {
    "ref": "qubership-integration-platform:{uuid}",   // helm (app-chart)
    "dependsOn": [
      "qip-engine:{uuid}",
      "qip-runtime-catalog:{uuid}"
    ]
  },
  {
    "ref": "qip-engine:{uuid}",                       // sub-chart
    "dependsOn": ["qip-engine-image:{uuid}"]
  },
  {
    "ref": "qip-runtime-catalog:{uuid}",              // sub-chart
    "dependsOn": ["qip-catalog-image:{uuid}"]
  }
]
```

---

## Final manifest structure reference

```
CycloneDX BOM (Application Manifest v2)
│
├── serialNumber        "urn:uuid:{uuid4}"  — new on every run
├── $schema             path to bundled JSON Schema
├── bomFormat           "CycloneDX"
├── specVersion         "1.6"
├── version             1
│
├── metadata
│   ├── timestamp       "YYYY-MM-DDTHH:MM:SSZ" (UTC)
│   ├── component       the application (mime-type: application/vnd.nc.application)
│   │   ├── bom-ref     "{appName}:{uuid4}"
│   │   ├── type        "application"
│   │   ├── mime-type   "application/vnd.nc.application"
│   │   ├── name        applicationName (or --name override)
│   │   └── version     applicationVersion (or --version override)
│   └── tools
│       └── components[]
│           └── { type: "application", name: "am-build-cli", version: "0.1.0" }
│
├── components[]        top-level components (sub-charts NOT here)
│   │
│   ├── standalone-runnable
│   │   ├── bom-ref     "{name}:{uuid4}"
│   │   ├── type        "application"
│   │   ├── mime-type   "application/vnd.nc.standalone-runnable"
│   │   ├── name        (from config)
│   │   ├── version     applicationVersion
│   │   ├── properties  []
│   │   └── components  []
│   │
│   ├── helm chart (simple or app-chart)
│   │   ├── bom-ref     "{name}:{uuid4}"
│   │   ├── type        "application"
│   │   ├── mime-type   "application/vnd.nc.helm.chart"
│   │   ├── name        (from Chart.yaml via mini-manifest)
│   │   ├── version     appVersion from Chart.yaml, fallback: applicationVersion
│   │   ├── purl        "pkg:helm/..." (from mini-manifest)
│   │   ├── hashes[]    [{ alg: "SHA-256", content: "..." }]
│   │   ├── properties[]
│   │   │   ├── { name: "isLibrary", value: false }
│   │   │   └── { name: "qubership:helm.values.artifactMappings", value: { ... } }
│   │   └── components[]
│   │       ├── values.schema.json  (if present in chart)
│   │       │   ├── bom-ref         "{name}:{uuid4}"
│   │       │   ├── type            "data"
│   │       │   ├── mime-type       "application/vnd.nc.helm.values.schema"
│   │       │   └── data[]          [{ type, name, contents: { attachment: { base64 } } }]
│   │       ├── resource-profile-baselines  (if present in chart)
│   │       │   ├── bom-ref         "{name}:{uuid4}"
│   │       │   ├── type            "data"
│   │       │   ├── mime-type       "application/vnd.nc.resource-profile-baseline"
│   │       │   └── data[]          [one entry per *.yaml profile file]
│   │       └── sub-chart (if app-chart / umbrella)
│   │           ├── bom-ref         "{name}:{uuid4}"
│   │           ├── type            "application"
│   │           ├── mime-type       "application/vnd.nc.helm.chart"
│   │           ├── name            (from config)
│   │           ├── properties[]    [isLibrary, artifactMappings]
│   │           └── components      []
│   │
│   └── docker image
│       ├── bom-ref     "{name}:{uuid4}"
│       ├── type        "container"
│       ├── mime-type   "application/vnd.docker.image"
│       ├── name        (from mini-manifest)
│       ├── version     (from mini-manifest)
│       ├── group       (from mini-manifest, if present)
│       ├── purl        (from mini-manifest, if present)
│       └── hashes[]    (from mini-manifest, if present)
│
└── dependencies[]
    ├── { ref: app,        dependsOn: [all top-level components] }
    ├── { ref: standalone, dependsOn: [helm chart] }
    ├── { ref: helm,       dependsOn: [sub-charts or docker images] }
    ├── { ref: sub-chart,  dependsOn: [docker images] }
    └── (entries omitted if dependsOn would be empty)
```
