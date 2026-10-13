# modelcar-maker

Python project to pair `olot` (https://github.com/containers/olot) with full lifecycle for KServe Model Car images
(https://kserve.github.io/website/docs/model-serving/storage/providers/oci). Designed for use locally, with
`podman`/`docker`, and within a Helm Chart for Kubernetes and OpenShift clusters.

## Project-specific architectural guidance.

- Treat inputs as trusted local/operator input.
- Avoid mock-heavy test suites, prefer to test mechanisms rather than wire behavior.
