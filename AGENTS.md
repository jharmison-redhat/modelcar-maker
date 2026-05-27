# modelcar-maker

## Purpose

A CLI tool that downloads Hugging Face models and packages them as OCI container images ("modelcars"), placing each model file in its own layer for efficient distribution.

## Key Concepts

- **Modelcar**: A minimal container image containing a downloaded Hugging Face model, tagged with `{normalized-model}-modelcar`.
- **Backends**:
  - `podman`: Traditional container build using a generated Containerfile.
  - `olot`: Uses OCI Layer On Top to stack model files as OCI layers directly, avoiding a traditional container build.
- **Layers per file**: Each model file becomes an individual image layer for deduplication and selective pulling.

## Modules

| Module | Description |
|---|---|
| `cli/cli.py` | Typer CLI. `build` command accepts model, registry, backend, push/cleanup flags. |
| `__init__.py` | Core `process()` workflow: download -> build -> push -> cleanup. |
| `download/hf_download.py` | Downloads models via `huggingface_hub.snapshot_download`. Extracts commit hash from metadata. |
| `image/podman.py` | Podman backend: renders Containerfile, runs `podman build/push/image rm`. |
| `image/olot.py` | OLOT backend: pulls base image into OCI layout, adds model files as layers with `olot`, pushes with `oras-py`. |
| `image/template.py` | Renders `Containerfile.j2` with model files, labels, and base image. |
| `image/common.py` | Shared utilities: `_image()` (image ref naming), `list_model_files()`, file filtering. |
| `image/types.py` | Dataclasses (`BuildArgs`, `BuildResult`, `PushArgs`, `RmArgs`) and `Backend` enum. |
| `util/config.py` | Dynaconf settings. Loads `defaults.toml`, system/user/cwd config, env vars (`MODELCAR_MAKER_*`). |
| `util/helpers.py` | `normalize()` (repo ID -> safe tag/folder), `Truthy` string class. |
| `util/logging.py` | Logger factory with verbosity-controlled stderr handler. |

## Usage

```bash
# Build and push a single model
modelcar-maker build meta-llama/Llama-3.2-3B-Instruct --push

# Build all default models with olot backend
modelcar-maker build --backend olot

# Build without pushing, clean up after
modelcar-maker build BAAI/bge-large-en-v1.5 --no-push --image-clean-up --model-clean-up
```

## Configuration

Config is layered via Dynaconf:

1. `src/modelcar_maker/util/defaults.toml` (built-in defaults)
2. `/usr/share/modelcar-maker/config.toml`
3. `/etc/modelcar-maker/config.toml`
4. `~/.config/modelcar-maker/config.toml`
5. `./config.toml` (project root)
6. Environment variables (`MODELCAR_MAKER_*`)

Key settings: `image.registry`, `image.repository`, `image.backend`, `image.push`, `image.cleanup`, `models.default` (list), `models.cleanup`.

## Development

Load the `dev-workflows` skill before creating or modifying Python files, setting up the project, or verifying changes. That skill contains the canonical instructions for environment setup, formatting, linting, and testing.
