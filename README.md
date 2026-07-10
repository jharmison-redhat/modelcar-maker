# modelcar-maker

Downloads Hugging Face models and packages them as minimal OCI container images with one file per layer. Designed for
use with KServe as [Modelcar images](https://kserve.github.io/website/docs/model-serving/storage/providers/oci).

## Install

Requires Python 3.11+.

```bash
pip install modelcar-maker
```

## Usage

```
modelcar-maker [OPTIONS] [MODEL]
```

| Argument / Option                          | Description                                                                                                        |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------ |
| `MODEL`                                    | Hugging Face model repo ID (e.g., `meta-llama/Llama-3.2-3B-Instruct`). Optional if `models.default` is configured. |
| `--registry`                               | Registry for the output image. Default: `quay.io`.                                                                 |
| `-r`, `--repository`                       | Repository within the registry. Default: `jharmison/models`.                                                       |
| `--backend`                                | Build backend. Default: `olot`.                                                                                    |
| `--base-image`                             | Base OCI image. Default: `registry.access.redhat.com/ubi10/ubi-micro:10.2`.                                        |
| `--arch`                                   | Target architecture(s). Repeat for multiple. Default: `amd64`, `arm64`.                                            |
| `--pull` / `--no-pull`                     | Pull base image if a newer version is available. Default: `--pull`.                                                |
| `--push` / `--no-push`                     | Push the image after building. Default: `--push`.                                                                  |
| `-a`, `--authfile`                         | Path to `.docker/config.json` style authfile for registry push.                                                    |
| `--image-clean-up` / `--no-image-clean-up` | Remove local container image after push. Default: off.                                                             |
| `--model-clean-up` / `--no-model-clean-up` | Remove downloaded model files after build. Default: off.                                                           |
| `--skip-if-exists` / `--no-skip-if-exists` | Skip if the tag already exists at the registry. Default: on.                                                       |
| `-v`                                       | Increase logging verbosity. Repeat for more.                                                                       |
| `-V`                                       | Print version and exit.                                                                                            |

**Image tag format:** `{registry}/{repository}/{normalized-model}-modelcar`

The model repo ID is normalized for use as a tag: slashes become `--`, dots become `_`, and the string is lowercased
before appending `-modelcar`. For example, `meta-llama/Llama-3.2-3B-Instruct` produces tag
`meta-llama--llama_3-2-3b-instruct-modelcar`.

**Examples:**

```bash
# Build and push a model
modelcar-maker meta-llama/Llama-3.2-3B-Instruct

# Build locally without pushing
modelcar-maker BAAI/bge-large-en-v1.5 --no-push

# Explicit multi-arch build with cleanup
modelcar-maker mistralai/Mistral-7B --arch amd64 --arch arm64 --image-clean-up --model-clean-up
```

## Configuration

Settings are resolved in layer order (later layers override earlier ones):

1. Built-in defaults ([`defaults.toml`](src/modelcar_maker/util/defaults.toml) useful as a reference)
2. `/usr/share/modelcar-maker/config.toml`
3. `/etc/modelcar-maker/config.toml`
4. `~/.config/modelcar-maker/config.toml`
5. `./config.toml` (current working directory)
6. The config in the path described by the `MODELCAR_MAKER_CONFIG` environment variable
7. Environment variables with prefix `MODELCAR_MAKER_`
8. CLI arguments override all of the above

Any setting can be overridden via environment variable by uppercasing the section and key and joining with underscores.
For example:

| Env Var                         | Overrides        |
| ------------------------------- | ---------------- |
| `MODELCAR_MAKER_IMAGE_REGISTRY` | `image.registry` |
| `MODELCAR_MAKER_IMAGE_PUSH`     | `image.push`     |
| `MODELCAR_MAKER_MODELS_DEFAULT` | `models.default` |

**Example `config.toml`:**

```toml
[image]
registry = "quay.io"
repository = "myorg/models"
push = true
architectures = ["amd64"]

[models]
default = [
  "meta-llama/Llama-3.2-3B-Instruct",
  "BAAI/bge-large-en-v1.5",
]
```

Setting default models allows running `modelcar-maker` with no arguments to build all listed models.

## Running as a Container

The tool is available as a container image at `ghcr.io/jharmison-redhat/modelcar-maker:latest` (versioned tags also
available).

```bash
podman run --rm ghcr.io/jharmison-redhat/modelcar-maker:latest --help
```

For authenticated pushes, mount a credential file. For gated downloads, provide the `HF_TOKEN` environment variable:

```bash
podman run --rm \
  -v "$HOME/.docker/config.json:/config.json" \
  -e HF_TOKEN="$HF_TOKEN" \
  ghcr.io/jharmison-redhat/modelcar-maker:latest \
  --authfile /config.json \
  meta-llama/Llama-3.2-3B-Instruct
```

To persist downloaded model files across runs, mount a volume at `/modelcar-maker`:

```bash
podman run --rm \
  -v modelcar-cache:/modelcar-maker \
  -v "$HOME/.docker/config.json:/config.json" \
  -e HF_TOKEN="$HF_TOKEN" \
  ghcr.io/jharmison-redhat/modelcar-maker:latest \
  --authfile /config.json \
  --skip-if-exists \
  meta-llama/Llama-3.2-3B-Instruct
```

`docker` may work but is untested.

## License

ISC. See [LICENSE](LICENSE).
