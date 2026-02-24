# Getting Started

This guide walks you through building your first Application Manifest from scratch.

By the end you will have a `manifest.json` that inventories all Docker images and Helm charts
of your application with their versions, hashes, and registry addresses.

---

## Prerequisites

Before you begin, make sure you have:

- **Python 3.12+** installed
- **`am` CLI** installed (see [Installation](../README.md#installation))
- **`helm` CLI** installed and in `PATH` — required only if your app uses Helm charts
  that are fetched from a registry (not built in CI)
- Access to your Docker/Helm registry (for `am fetch` to run `helm pull`)

---

## What you need to prepare

`am` requires two types of input:

| Input | Who creates it | When |
|---|---|---|
| `build-config.yaml` | You, once per application | Before running `am` |
| CI metadata JSON (one per image) | Your CI pipeline | After each `docker push` / `helm push` |

**`build-config.yaml`** describes the structure of your application: all Docker images,
Helm charts, their types, registry references, and dependencies.

**CI metadata JSON** is a small JSON file your CI pipeline writes after building each image.
It contains the image name, version, SHA-256 hash, and registry address —
information that only becomes known after the build.

If a component is not built in CI (e.g. a third-party chart from a public registry),
you do not need a CI metadata JSON for it — `am fetch` handles it automatically.

> **No Helm charts?** If your application uses only Docker images (no Helm charts),
> you can skip Step 4 (`am fetch`) entirely — it only applies to components with a
> `reference` field. Run Steps 3 and 5 only.

---

## Step 1: Write your build-config.yaml

Create a `build-config.yaml` that lists all components of your application.

Start with this minimal template and fill in your own values:

```yaml
applicationName: "my-app"          # the name of your application
applicationVersion: "1.0.0"        # the version of this release

components:
  # The application entry point — always present, always one
  - name: my-app
    mimeType: application/vnd.nc.standalone-runnable
    dependsOn:
      - name: my-app
        mimeType: application/vnd.nc.helm.chart

  # Your Helm chart — fetched from OCI registry by `am fetch`
  - name: my-app
    mimeType: application/vnd.nc.helm.chart
    reference: "oci://registry.example.com/charts/my-app:1.0.0"
    dependsOn:
      - name: my-app-backend
        mimeType: application/vnd.docker.image
        valuesPathPrefix: images.backend   # path in values.yaml for this image

  # Docker image built in CI — `am component` will provide the mini-manifest
  - name: my-app-backend
    mimeType: application/vnd.docker.image

  # Third-party image — `am fetch` will produce the mini-manifest from the reference
  - name: envoy
    mimeType: application/vnd.docker.image
    reference: "docker.io/envoyproxy/envoy:v1.32.6"
```

**Rules to remember:**

- Every application needs exactly one `standalone-runnable` — the deployment entry point.
- A Docker image **with** `reference` is handled by `am fetch` (no hash, no download).
- A Docker image **without** `reference` must have a CI metadata JSON produced by your CI pipeline.
- A Helm chart **with** `reference` is downloaded by `am fetch` via `helm pull`.

See the [Build Config Reference](configuration.md) for the full field reference.

---

## Step 2: Set up CI to produce metadata JSON

For each Docker image (or Helm chart) built in your CI pipeline, add a step that writes
a JSON file with the build output. This file is what `am component` reads.

**Example for a Docker image** — add this after `docker push`:

```bash
cat > ci-output/my-app-backend-meta.json << EOF
{
  "name": "my-app-backend",
  "type": "container",
  "mime-type": "application/vnd.docker.image",
  "group": "myorg",
  "version": "${IMAGE_TAG}",
  "hashes": [
    {
      "alg": "SHA-256",
      "content": "${IMAGE_DIGEST}"
    }
  ],
  "reference": "registry.example.com/myorg/my-app-backend:${IMAGE_TAG}"
}
EOF
```

Where `IMAGE_TAG` and `IMAGE_DIGEST` are variables set by your CI after the build.
`IMAGE_DIGEST` must be the raw SHA-256 hex string (64 characters), **without** the `sha256:` prefix.

How to get it in common CI systems:

**GitHub Actions:**
```yaml
- name: Build and push
  id: push
  uses: docker/build-push-action@v5
  with:
    push: true
    tags: registry.example.com/myorg/my-app-backend:${{ env.IMAGE_TAG }}

- name: Write metadata
  run: |
    IMAGE_DIGEST=$(echo "${{ steps.push.outputs.digest }}" | cut -d':' -f2)
    cat > ci-output/my-app-backend-meta.json << EOF
    { ..., "hashes": [{ "alg": "SHA-256", "content": "${IMAGE_DIGEST}" }] }
    EOF
```

**GitLab CI:**
```yaml
build:
  script:
    - docker build -t $CI_REGISTRY_IMAGE/my-app-backend:$CI_COMMIT_SHORT_SHA .
    - docker push $CI_REGISTRY_IMAGE/my-app-backend:$CI_COMMIT_SHORT_SHA
    - IMAGE_DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' $CI_REGISTRY_IMAGE/my-app-backend:$CI_COMMIT_SHORT_SHA | cut -d'@' -f2 | cut -d':' -f2)
    - echo "{\"hashes\":[{\"alg\":\"SHA-256\",\"content\":\"$IMAGE_DIGEST\"}]}" > ci-output/my-app-backend-meta.json
```

> **Important**: `name` and `mime-type` in this file must exactly match the corresponding
> entry in your `build-config.yaml`, otherwise `am generate` will not find the component.

See [commands.md — Input format](commands.md#input-format) for the full JSON field reference.

---

## Step 3: Run `am component` for CI-built images

For each CI metadata JSON file produced in Step 2, run `am component` to convert it
into a mini-manifest:

```bash
mkdir -p minis/

am component \
  -i ci-output/my-app-backend-meta.json \
  -o minis/my-app-backend.json
```

If you have multiple CI-built images, run once per image:

```bash
am component -i ci-output/image-a-meta.json -o minis/image-a.json
am component -i ci-output/image-b-meta.json -o minis/image-b.json
```

These steps can run in parallel in CI.

**Output**: a mini-manifest JSON file for each image, with the SHA-256 hash from CI.

---

## Step 4: Run `am fetch` for Helm charts and referenced images

For all components that have a `reference` in your config (Helm charts and third-party images),
run `am fetch` once — it processes all of them in a single call:

```bash
am fetch \
  -c build-config.yaml \
  -o minis/
```

This will:
- Download each Helm chart via `helm pull`, extract metadata and embedded files
- Parse each Docker image reference (no download — just extracts version and namespace)
- Write mini-manifests to the `minis/` directory

**Output**: one mini-manifest per component. Helm chart manifests include a SHA-256 hash;
Docker image manifests from references do not (image was not downloaded).

Steps 3 and 4 are **independent** and can run in parallel in CI.

---

## Step 5: Run `am generate` to assemble the manifest

Once all mini-manifests are ready, assemble the final Application Manifest:

```bash
am generate \
  -c build-config.yaml \
  -o manifest.json \
  --validate \
  minis/
```

`--validate` checks the output against the bundled JSON Schema after writing.

**Output**: `manifest.json` — the Application Manifest describing your full release.

Console output on success:

```
Manifest written to manifest.json
Manifest is valid.
```

If a component is missing (no mini-manifest found), you will see a warning:

```
WARNING: component 'my-app-backend' (application/vnd.docker.image) not found in mini-manifests — skipped
```

This means either Step 2/3 was not run for that component, or `name`/`mime-type` don't match
the config. Check that the names are consistent.

---

## Step 6: Validate independently (optional)

You can validate an existing manifest at any time without regenerating it:

```bash
am validate -i manifest.json
```

Useful for re-validating after manual edits or as a separate CI gate.

---

## Full pipeline in CI (example)

```bash
#!/bin/bash
set -e

mkdir -p minis/

# Step 3 — CI-built images (run after docker push in your build jobs)
am component -i ci-output/my-app-backend-meta.json -o minis/my-app-backend.json

# Step 4 — Helm charts and referenced images
am fetch -c build-config.yaml -o minis/

# Step 5 — Assemble and validate
am generate -c build-config.yaml -o manifest.json --validate minis/

echo "Manifest ready: manifest.json"
```

---

## What's next

- See [Examples](examples.md) for a complete real-world walkthrough using Jaeger
- See the [Build Config Reference](configuration.md) for the full `build-config.yaml` field reference
- See the [Commands Reference](commands.md) for all command options and flags
