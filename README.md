# Application Manifest CLI (am)

`am` is a CLI tool that generates an **Application Manifest** in three steps:
component mini-manifests → Helm chart mini-manifests → final Application Manifest.

The manifest is a single JSON file describing the complete composition of a software
release: every Docker image, every Helm chart, their versions, cryptographic hashes,
registry addresses, and dependency graph. It follows the
[CycloneDX 1.6](https://cyclonedx.org/specification/overview/) BOM standard.

---

## Why you need it

The Application Manifest is the **input to the NC deployment tooling** that deploys
applications to ArgoCD. Before a deployment can start, the tool needs to know:

- Which Docker images to deploy, their exact versions and digests
- Which Helm charts to use, their versions and registry locations
- How all components depend on each other

The manifest provides all of this in a single structured file, produced at release time
in CI. Without it, the deployment tool has no source of truth about what to deploy.

---

## Installation

```bash
git clone https://github.com/netcracker/app-manifest.git
cd app-manifest
pip install -e .
am --help
```

Requires [Python 3.12+](https://www.python.org/), [Pydantic v2](https://docs.pydantic.dev/latest/),
[Click](https://click.palletsprojects.com/), and the [Helm CLI](https://helm.sh/docs/intro/install/)
(for the `fetch` command only).

---

## How it works

Building a manifest is a **three-step pipeline**. You need two input files:

- **`build-config.yaml`** — a file you write once per application. Lists all components,
  their types, registry references, and dependencies.
  See the [Build Config Reference](docs/configuration.md) for the format.

- **CI metadata JSON** — one file per image built in CI, written by your pipeline after
  `docker push`. Contains name, version, SHA-256 hash, and registry address.
  See [Input format](docs/commands.md#input-format) for the exact format your CI must produce.

Each command produces or consumes a **mini-manifest** — a small intermediate CycloneDX BOM
for exactly one component. Mini-manifests are not the final output; they are consumed by
`am generate` to build the Application Manifest.

```
  Your inputs                  am commands                     Output
  ──────────                   ───────────                     ──────

  CI metadata JSON  ─────────► am component ──────────────►  mini-manifest (with hash)
                                                                      │
  build-config.yaml ─────────► am fetch     ──────────────►  mini-manifest (no hash)
                                                                      │
  build-config.yaml ─────────► am generate  ◄───────────────────────┘
  + all mini-manifests                              │
                                                    ▼
                                          Application Manifest JSON
```

**Step 1 — `am component`**
For each image built in CI, convert the metadata JSON into a mini-manifest:

```bash
am component -i ci/jaeger-meta.json -o minis/jaeger.json
```

**Step 2 — `am fetch`**
For Helm charts and third-party images referenced by URL (not built in CI),
download and produce mini-manifests automatically:

```bash
am fetch -c build-config.yaml -o minis/
```

**Step 3 — `am generate`**
Assemble the final manifest from all mini-manifests and the build config:

```bash
am generate -c build-config.yaml -o manifest.json --validate minis/
```

> Steps 1 and 2 are independent and can run in parallel.
> Step 3 requires both to complete.

For a complete worked example see [Examples](docs/examples.md).

---

## Commands

| Command     | Alias | Description                                     |
| ----------- | ----- | ----------------------------------------------- |
| `component` | `c`   | CI metadata JSON to mini-manifest               |
| `fetch`     | `f`   | Helm chart / Docker reference to mini-manifest  |
| `generate`  | `gen` | Mini-manifests + build config to final manifest |
| `validate`  | `v`   | Validate manifest against JSON Schema           |

---

## Documentation
| Reference                                           | Description                                                              |
| --------------------------------------------------- | ------------------------------------------------------------------------ |
| [**Getting Started**](docs/getting-started.md)      | **Start here** — step-by-step guide from zero to a ready manifest        |
| [**Build Config Reference**](docs/configuration.md) | `build-config.yaml` format: fields, component types, registry definition |
| [**Commands Reference**](docs/commands.md)          | Full reference for all four `am` commands and their options              |
| [**Examples**](docs/examples.md)                    | Complete Jaeger example: config, CI metadata, pipeline commands, output  |
| [**Mini-manifests**](docs/mini-manifests.md)        | Mini-manifest format, naming rules, collision handling                   |
| [**Manifest Assembly**](docs/manifest-assembly.md)  | How `generate` builds the final manifest (detailed algorithm)            |
| [**PURL Reference**](docs/purl.md)                  | How Package URLs are generated for Docker and Helm components            |
| [**Architecture**](docs/architecture.md)            | High-level architecture and data flow                                    |
| [**Design Decisions**](docs/design-decisions.md)    | Motivation for key design choices                                        |

---

## Glossary

- **Application Manifest**: the final CycloneDX 1.6 BOM JSON produced by `am generate`.
  Describes the full composition of an application release.
- **Mini-manifest**: an intermediate CycloneDX BOM for exactly one component.
  Produced by `am component` or `am fetch`, consumed by `am generate`. Not a final output.
- **Build config**: the `build-config.yaml` file you maintain — lists all application components,
  their types, references, and dependencies.
- **CI metadata**: a JSON file your CI pipeline produces after building a component —
  contains name, version, hash, registry address. Used by `am component`.
- **Standalone-runnable**: a special component type representing the application entry point —
  the top-level deployment artifact. It has no image or chart of its own; it groups the
  application's dependencies and carries the application version in the manifest.
- **PURL (Package URL)**: a standard identifier for a package, e.g.
  `pkg:docker/org/image@1.0?registry_name=myregistry`. Used in manifests for traceability.
- **Registry definition**: optional YAML that maps registry hostnames to logical names used in PURLs.

---

## Project structure

```
app-manifest/
├── src/app_manifest/
│   ├── cli.py                  # CLI entry point (Click)
│   ├── models/                 # Pydantic models
│   │   ├── config.py           # Build config schema
│   │   ├── cyclonedx.py        # CycloneDX BOM models
│   │   ├── metadata.py         # CI metadata schema
│   │   └── regdef.py           # Registry definition schema
│   ├── services/               # Business logic
│   │   ├── artifact_fetcher.py # Helm pull + Docker reference parsing
│   │   ├── component_builder.py# CI metadata to mini-manifest
│   │   ├── manifest_builder.py # Mini-manifests to final manifest
│   │   ├── validator.py        # JSON Schema validation
│   │   └── ...
│   └── schemas/
│       └── application-manifest.schema.json
├── tests/
│   ├── fixtures/
│   │   ├── configs/            # Build config YAML examples
│   │   ├── metadata/           # CI metadata JSON examples
│   │   ├── regdefs/            # Registry definition YAML examples
│   │   └── examples/           # Generated manifest examples
│   ├── test_e2e.py
│   └── ...
└── docs/
```
