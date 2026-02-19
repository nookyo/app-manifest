# Architecture Overview

This document explains the high-level architecture of the CLI and how data flows through it.

---

## Goals

- Produce Application Manifest v2 as a CycloneDX 1.6 BOM.
- Keep build steps independent so CI jobs can run in parallel.
- Support both CI-built artifacts and referenced external artifacts.
- Make output deterministic in structure, but allow local identifiers to be ephemeral.

---

## High-level flow

```
CI metadata JSON --------┐
                         ├─> mini-manifests (CycloneDX BOM, 1 component)
Build config YAML -------┘
       |
       v
am generate -> final Application Manifest (CycloneDX BOM)
       |
       v
am validate (JSON Schema)
```

---

## Modules and responsibilities

**CLI entry**
- `src/app_manifest/cli.py` wires commands to services and handles validation and output.

**Models**
- `src/app_manifest/models/config.py` validates build config YAML.
- `src/app_manifest/models/metadata.py` validates CI component metadata JSON.
- `src/app_manifest/models/cyclonedx.py` defines the output BOM structure.
- `src/app_manifest/models/regdef.py` defines Registry Definition format.

**Services**
- `component_builder.py`: CI metadata -> mini-manifest.
- `artifact_fetcher.py`: Helm pull or Docker reference -> mini-manifest.
- `manifest_builder.py`: build config + mini-manifests -> final manifest.
- `purl.py`: PURL generation with registry mapping.
- `validator.py`: JSON Schema validation.

---

## Data contracts

**Build config**
- Source of truth for component identity: `(name, mimeType)`.
- Defines dependency graph used by `generate`.

**Mini-manifests**
- One component per file in `components[0]`.
- Indexed by `(name, mime-type)` inside the file.

**Final manifest**
- Contains metadata, top-level components, and full dependency graph.
- `bom-ref` values are generated per run and used for internal linking only.

---

## Extension points

- Add new component types by extending `MimeType` and updating builder logic.
- Customize PURL resolution via Registry Definition.
- Add new validations by extending the JSON Schema and `validator.py`.
