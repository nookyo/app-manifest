# am — Application Manifest CLI

`am` builds an **Application Manifest** — a single JSON file that describes the complete
composition of a software release: every Docker image, every Helm chart, their versions,
cryptographic hashes, registry addresses, and dependency graph.

The manifest follows the [CycloneDX 1.6](https://cyclonedx.org/specification/overview/)
BOM (Bill of Materials) standard.

---

## Why you need it

Without a manifest, you cannot know exactly what is running in production:
which image digest, which chart version, from which registry.

With a manifest you can:

- Know exactly what is deployed: image digests, chart versions, registry addresses
- Track what changed between releases at the component level
- Feed vulnerability scanning, auditing, and deployment tooling with structured data
- Verify that every component is present and accounted for before a release

---

## Before you start

To build a manifest you need two things:

**1. `build-config.yaml`** — a file you write once per application.
It lists all components: Docker images, Helm charts, their types, registry references,
and how they depend on each other.

See [docs/configuration.md](docs/configuration.md) for the format and field reference.

**2. CI metadata JSON** — one JSON file per image or chart built in your CI pipeline.
Your CI system writes this file after a successful build and push.
It contains the component name, version, SHA-256 hash, and registry address.

See [docs/commands.md — Input format](docs/commands.md#input-format) for the exact JSON format
your CI must produce.

> If a component is not built in CI (e.g. a third-party Helm chart fetched from a registry),
> you do not need a CI metadata JSON for it — `am fetch` handles those automatically.

---

## How it works

Building a manifest is a **three-step pipeline**:

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
For each image or chart built in your CI pipeline, convert the CI-produced metadata JSON
into a mini-manifest (hash is taken from CI):

```bash
am component -i ci/jaeger-meta.json -o minis/jaeger.json
```

**Step 2 — `am fetch`**
For Helm charts and third-party images that are not built in CI (referenced by URL only),
download charts via `helm pull` and produce mini-manifests automatically:

```bash
am fetch -c build-config.yaml -o minis/
```

**Step 3 — `am generate`**
Read all mini-manifests and the build config, match components by identity, and assemble
the final Application Manifest:

```bash
am generate -c build-config.yaml -o manifest.json --validate minis/
```

> Steps 1 and 2 are independent and can run in parallel.
> Step 3 requires both to complete.

A **mini-manifest** is an intermediate file that describes exactly one component.
You never use mini-manifests directly — they are consumed by `am generate`.

For a complete worked example see [docs/examples.md](docs/examples.md).

---

## Documentation

| Document                                               | Description                                                                      |
| ------------------------------------------------------ | -------------------------------------------------------------------------------- |
| [docs/examples.md](docs/examples.md)                   | **Start here** — complete Jaeger walkthrough: config, CI metadata, commands, output |
| [docs/configuration.md](docs/configuration.md)         | `build-config.yaml` format: fields, component types, registry definition         |
| [docs/commands.md](docs/commands.md)                   | Full reference for all four `am` commands and their options                      |
| [docs/mini-manifests.md](docs/mini-manifests.md)       | Mini-manifest format, naming rules, collision handling                           |
| [docs/manifest-assembly.md](docs/manifest-assembly.md) | How `generate` builds the final manifest (detailed algorithm)                    |
| [docs/architecture.md](docs/architecture.md)           | High-level architecture and data flow                                            |
| [docs/design-decisions.md](docs/design-decisions.md)   | Motivation for key design choices                                                |

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

## Commands

| Command     | Alias | Description                                     |
| ----------- | ----- | ----------------------------------------------- |
| `component` | `c`   | CI metadata JSON to mini-manifest               |
| `fetch`     | `f`   | Helm chart / Docker reference to mini-manifest  |
| `generate`  | `gen` | Mini-manifests + build config to final manifest |
| `validate`  | `v`   | Validate manifest against JSON Schema           |

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
