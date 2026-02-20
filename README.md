# am — Application Manifest CLI

## What is an Application Manifest?

An **Application Manifest** is a single JSON file that inventories the complete
composition of an application release: all its Docker images, Helm charts, versions,
cryptographic hashes, PURLs, and their dependency graph.

It is built on the [CycloneDX 1.6](https://cyclonedx.org/specification/overview/)
BOM (Bill of Materials) standard — an open format for describing software components
and supply chain metadata.

Built with [Python 3.12+](https://www.python.org/), [Pydantic v2](https://docs.pydantic.dev/latest/),
[Click](https://click.palletsprojects.com/), and the [Helm CLI](https://helm.sh/docs/intro/install/).

**Why do you need it?**

- Know exactly what is deployed: image digests, chart versions, registry locations
- Track changes between releases at the component level
- Feed auditing, vulnerability scanning, and deployment tooling with structured data
- Enforce that no component is missing or unverified before a release

---

## How it works

Building a manifest is a **three-step pipeline**.

You need two input files that you maintain yourself:

- **`build-config.yaml`** — lists all components of your application (Docker images, Helm charts),
  their types, OCI references, and dependencies. You write this once per application.
  See [docs/configuration.md](docs/configuration.md) for the format, field reference, and annotated example.

- **CI metadata JSON** — produced by your CI system after building each image: contains
  the image name, version, SHA-256 hash, and registry reference. One file per CI-built image.
  See [docs/commands.md](docs/commands.md#component-c--ci-metadata-to-mini-manifest) for the exact format and field descriptions.

The pipeline produces intermediate files called **mini-manifests** — one per component —
that are combined in the final step into the Application Manifest.

```
  Your inputs                 Tool actions                    Outputs
  ──────────                  ────────────                    ───────

  CI metadata JSON   ──►  am component  ──►  mini-manifest (with hash)
                                                      │
  build-config.yaml  ──►  am fetch      ──►  mini-manifest (no hash)
                                                      │
  build-config.yaml  ──►  am generate  ◄──────────────┘
       +                                      │
  all mini-manifests                          ▼
                                    Application Manifest JSON
```

**Step 1** — for each image built in your CI pipeline, convert the CI-produced
metadata JSON into a mini-manifest (hash taken from CI):

```bash
am component -i ci/jaeger-meta.json -o minis/jaeger.json
```

**Step 2** — for Helm charts and third-party Docker images referenced by URL
(not built in CI), fetch and produce mini-manifests automatically:

```bash
am fetch -c build-config.yaml -o minis/
```

**Step 3** — read all mini-manifests and the build config, match by component
identity `(name, mimeType)`, and assemble the final manifest:

```bash
am generate -c build-config.yaml -o manifest.json --validate minis/
```

> Steps 1 and 2 are independent and can run in parallel.
> Step 3 requires both to complete.

For a complete worked example see [docs/examples.md](docs/examples.md).

---

## Documentation

| Document                                               | Description                                                         |
| ------------------------------------------------------ | ------------------------------------------------------------------- |
| [docs/configuration.md](docs/configuration.md)                         | **Build config format** — workflow, fields, component types, registry definition |
| [docs/commands.md](docs/commands.md)                   | Full reference for all four commands and their options              |
| [docs/mini-manifests.md](docs/mini-manifests.md)       | Mini-manifest format, file naming rules, collision handling         |
| [docs/manifest-assembly.md](docs/manifest-assembly.md) | How `generate` assembles the final manifest                         |
| [docs/examples.md](docs/examples.md)                   | Complete Jaeger example: config, metadata, and final manifest       |
| [docs/architecture.md](docs/architecture.md)           | High-level system architecture and data flow                        |
| [docs/design-decisions.md](docs/design-decisions.md)   | Motivation for key design choices                                   |

---

## Installation

```bash
git clone https://github.com/netcracker/app-manifest.git
cd app-manifest
pip install -e .
am --help
```

Requires **Python 3.12+** and [`helm` CLI](https://helm.sh/docs/intro/install/) (for the `fetch` command).

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
- **Mini-manifest**: a minimal CycloneDX BOM for exactly one component (one Docker image or Helm chart).
  An intermediate file produced by `am component` or `am fetch` and consumed by `am generate`.
  Not the final output — you do not use mini-manifests directly.
- **Build config**: a YAML file you maintain that lists all application components,
  their types, OCI references, and dependencies. Required by `am fetch` and `am generate`.
- **CI metadata**: a JSON file your CI pipeline produces after building an image,
  containing the image name, version, SHA-256 hash, and registry reference. Used by `am component`.
  See [docs/commands.md](docs/commands.md) for the exact format.
- **PURL (Package URL)**: a standard identifier for a software package, e.g.
  `pkg:docker/org/image@1.0?registry_name=myregistry`. Used in manifests for traceability.
- **Registry definition**: optional YAML that maps registry hostnames to a logical name used in PURLs.

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
│   │   ├── configs/            # Build config YAML files
│   │   ├── metadata/           # CI metadata JSON files
│   │   ├── regdefs/            # Registry definition YAML files
│   │   └── examples/           # Generated manifest examples
│   ├── test_e2e.py             # End-to-end pipeline tests
│   └── ...
└── docs/
```
