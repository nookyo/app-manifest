# Example: Jaeger Application Manifest

A complete walkthrough of building an Application Manifest for a real-world Jaeger deployment.

All files referenced here are part of the repository under `tests/fixtures/`.

---

## Application layout

Jaeger consists of:

- One **standalone-runnable** (`cassandra`) — the deployment entry point
- One **Helm chart** (`qubership-jaeger`) — fetched from an OCI registry
- **11 Docker images** — split into two groups by their source:
  - 7 images referenced in the config — `fetch` builds their mini-manifests
  - 4 images built in CI — `component` builds their mini-manifests

---

## Files

### Build config

[jaeger_full_config.yaml](../tests/fixtures/configs/jaeger_full_config.yaml)

```yaml
applicationVersion: "1.2.3"
applicationName: "jaeger"
components:

  - name: cassandra
    mimeType: application/vnd.nc.standalone-runnable
    dependsOn:
      - name: qubership-jaeger
        mimeType: application/vnd.nc.helm.chart

  - name: qubership-jaeger
    mimeType: application/vnd.nc.helm.chart
    reference: "oci://sandbox.example.com/charts/qubership-jaeger:1.2.3"
    dependsOn:
      - name: jaeger-cassandra-schema
        mimeType: application/vnd.docker.image
        valuesPathPrefix: cassandraSchema
      - name: jaeger
        mimeType: application/vnd.docker.image
        valuesPathPrefix: jaeger
      # ... (11 dependsOn entries total)

  # Docker images with reference — mini-manifests built by fetch (no hash)
  - name: jaeger-cassandra-schema
    mimeType: application/vnd.docker.image
    reference: "docker.io/jaegertracing/jaeger-cassandra-schema:1.72.0"
  - name: jaeger
    mimeType: application/vnd.docker.image
    reference: "docker.io/jaegertracing/jaeger:2.9.0"
  # ... (7 reference images total)

  # Docker images without reference — mini-manifests built by component (from CI, with hash)
  - name: jaeger-readiness-probe
    mimeType: application/vnd.docker.image
  - name: jaeger-integration-tests
    mimeType: application/vnd.docker.image
  # ... (4 CI images total)
```

### CI metadata files (for `component` command)

Located in [tests/fixtures/metadata/](../tests/fixtures/metadata/):

| File                                     | Image                                     | Source      |
| ---------------------------------------- | ----------------------------------------- | ----------- |
| `jaeger_readiness_probe_metadata.json`   | `jaeger-readiness-probe`                  | Built in CI |
| `jaeger_integration_tests_metadata.json` | `jaeger-integration-tests`                | Built in CI |
| `spark_dependencies_metadata.json`       | `spark-dependencies-image`                | Built in CI |
| `qubership_dsp_metadata.json`            | `qubership-deployment-status-provisioner` | Built in CI |

Example metadata format:

```json
{
  "name": "jaeger-readiness-probe",
  "type": "container",
  "mime-type": "application/vnd.docker.image",
  "group": "qubership",
  "version": "1.2.3",
  "hashes": [
    { "alg": "SHA-256", "content": "a1a1a1...a1a1" }
  ],
  "reference": "sandbox.example.com/qubership/jaeger-readiness-probe:1.2.3"
}
```

---

## Pipeline commands

### Step 1 — `component`: CI-built images to mini-manifests

For each of the 4 images built in CI, run:

```bash
am component \
  -i tests/fixtures/metadata/jaeger_readiness_probe_metadata.json \
  -o minis/jaeger-readiness-probe.json

am component \
  -i tests/fixtures/metadata/jaeger_integration_tests_metadata.json \
  -o minis/jaeger-integration-tests.json

am component \
  -i tests/fixtures/metadata/spark_dependencies_metadata.json \
  -o minis/spark-dependencies-image.json

am component \
  -i tests/fixtures/metadata/qubership_dsp_metadata.json \
  -o minis/qubership-deployment-status-provisioner.json
```

Output: 4 mini-manifest JSON files, each with a SHA-256 hash.

### Step 2 — `fetch`: Helm chart + 7 Docker reference images to mini-manifests

```bash
am fetch \
  -c tests/fixtures/configs/jaeger_full_config.yaml \
  -o minis/
```

Output:
- `minis/qubership-jaeger.json` — Helm chart mini-manifest with SHA-256 hash and nested `values.schema.json` / `resource-profiles`
- `minis/jaeger-cassandra-schema.json` — Docker mini-manifest, **no hash** (reference only)
- `minis/jaeger.json`, `minis/example-hotrod.json`, `minis/envoy.json`, etc.

### Step 3 — `generate`: assemble the final manifest

```bash
am generate \
  --validate \
  -c tests/fixtures/configs/jaeger_full_config.yaml \
  -o manifest.json \
  minis/
```

Output: `manifest.json` validated against the JSON Schema.

---

## Output manifest structure

[jaeger_manifest.json](../tests/fixtures/examples/jaeger_manifest.json)

```
components (13 total):
  cassandra                              standalone-runnable  (no hash)
  qubership-jaeger                       helm.chart           SHA-256 ✓
  jaeger-cassandra-schema                docker.image         (no hash — from reference)
  jaeger                                 docker.image         (no hash — from reference)
  example-hotrod                         docker.image         (no hash — from reference)
  jaeger-es-index-cleaner               docker.image         (no hash — from reference)
  jaeger-es-rollover                     docker.image         (no hash — from reference)
  envoy                                  docker.image         (no hash — from reference)
  openjdk                                docker.image         (no hash — from reference)
  jaeger-readiness-probe                 docker.image         SHA-256 ✓ (from CI)
  jaeger-integration-tests               docker.image         SHA-256 ✓ (from CI)
  spark-dependencies-image               docker.image         SHA-256 ✓ (from CI)
  qubership-deployment-status-provisioner docker.image        SHA-256 ✓ (from CI)

dependencies (3 entries):
  cassandra              -> [qubership-jaeger]
  qubership-jaeger       -> [jaeger-cassandra-schema, jaeger, jaeger-readiness-probe,
                             example-hotrod, jaeger-integration-tests,
                             jaeger-es-index-cleaner, jaeger-es-rollover,
                             envoy, openjdk, spark-dependencies-image,
                             qubership-deployment-status-provisioner]
  metadata (app)         -> [cassandra, qubership-jaeger, all 11 docker images]
```

### Fragment: standalone and helm components

```json
{
  "components": [
    {
      "bom-ref": "cassandra:...",
      "type": "application",
      "mime-type": "application/vnd.nc.standalone-runnable",
      "name": "cassandra",
      "version": "1.2.3"
    },
    {
      "bom-ref": "qubership-jaeger:...",
      "type": "application",
      "mime-type": "application/vnd.nc.helm.chart",
      "name": "qubership-jaeger",
      "version": "1.2.3",
      "purl": "pkg:helm/charts/qubership-jaeger@1.2.3?registry_name=sandbox.example.com",
      "hashes": [{ "alg": "SHA-256", "content": "..." }],
      "components": [
        { "mime-type": "application/vnd.nc.helm.values.schema", "name": "values.schema.json", "..." },
        { "mime-type": "application/vnd.nc.resource-profile-baseline", "name": "resource-profile-baselines", "..." }
      ]
    },
    {
      "bom-ref": "jaeger-cassandra-schema:...",
      "type": "container",
      "mime-type": "application/vnd.docker.image",
      "name": "jaeger-cassandra-schema",
      "version": "1.72.0",
      "group": "jaegertracing",
      "purl": "pkg:docker/jaegertracing/jaeger-cassandra-schema@1.72.0?registry_name=docker.io"
      // no "hashes" — image not downloaded, built from reference only
    }
  ]
}
```

---

## Validate separately

After building, you can validate the manifest independently:

```bash
am validate -i manifest.json
# Manifest is valid: manifest.json
```

This is useful for re-validating an existing manifest (e.g. after manual editing, or in a separate CI step).

---

## PURL mapping for reference images

| Config `reference`                                        | Generated PURL                                                                    |
| --------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `docker.io/jaegertracing/jaeger-cassandra-schema:1.72.0`  | `pkg:docker/jaegertracing/jaeger-cassandra-schema@1.72.0?registry_name=docker.io` |
| `docker.io/envoyproxy/envoy:v1.32.6`                      | `pkg:docker/envoyproxy/envoy@v1.32.6?registry_name=docker.io`                     |
| `docker.io/library/openjdk:11`                            | `pkg:docker/library/openjdk@11?registry_name=docker.io`                           |
| `oci://sandbox.example.com/charts/qubership-jaeger:1.2.3` | `pkg:helm/charts/qubership-jaeger@1.2.3?registry_name=sandbox.example.com`        |
