# Design Decisions and Motivation

This document captures why the CLI is built the way it is.

---

## Why mini-manifests

**Problem**
Artifacts are produced by different CI jobs at different times.

**Decision**
Use mini-manifests (one component per CycloneDX BOM) as the unit of work.

**Benefits**
- Parallelizable CI pipelines.
- Reusable artifacts across releases.
- Clear isolation between build, fetch, and assemble steps.

---

## Why matching by `(name, mime-type)`

**Problem**
Filenames are unreliable and can be arbitrary.

**Decision**
`generate` matches mini-manifests by the component identity stored inside the file.

**Benefits**
- Stable matching even if files are renamed or reorganized.
- Explicit identity controlled by build config.

---

## Why `bom-ref` is regenerated

**Problem**
`bom-ref` is a local identifier and should not be used as a persistent key across runs.

**Decision**
Generate new `bom-ref` values on every `generate` run.

**Benefits**
- Avoids leaking or coupling to internal IDs.
- Keeps BOM integrity local to a single output file.

---

## Why Helm charts are fetched via `helm pull`

**Problem**
Charts can include metadata and embedded files (values schema, resource profiles)
that must be included in the manifest.

**Decision**
Use `helm pull` to download and inspect chart contents.

**Benefits**
- Accurate metadata and content extraction.
- SHA-256 hash of the chart archive for integrity.

---

## Why Docker images from references have no hash

**Problem**
Hashes require downloading image content, which is expensive and may not be possible in CI.

**Decision**
When only a reference is provided, create a minimal mini-manifest without hashes.

**Benefits**
- Fast, no registry authentication required.
- Still enables dependency tracking and PURL generation.

---

## Why Registry Definition is optional

**Problem**
Not every environment has a consistent registry naming scheme.

**Decision**
Treat Registry Definition as optional and fall back to hostnames.

**Benefits**
- Works out of the box without extra files.
- Enables consistent `registry_name` when available.
