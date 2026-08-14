# modelcar-maker

Downloads Hugging Face models and packages them as minimal OCI container images with one file per layer. Builds on top
of [olot](https://github.com/containers/olot) with a full publishing workflow. Designed for use with KServe as
[Modelcar images](https://kserve.github.io/website/docs/model-serving/storage/providers/oci).

## Install

Requires Python 3.11+ and skopeo available in `$PATH`

```bash
pip install modelcar-maker
```

## Usage

```
modelcar-maker [OPTIONS] [MODEL]
```

| Argument / Option                          | Description                                                                                                                                                    |
| ------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `MODEL`                                    | Hugging Face model repo ID (e.g., `meta-llama/Llama-3.2-3B-Instruct`). Optional if `models.default` is configured.                                             |
| `-f`, `--file`                             | Specific files to grab from the repo (can be specified more than once), otherwise grabs all (with some notable exceptions, skipping pytorch bins for example). |
| `--registry`                               | Registry for the output image. Default: `quay.io`.                                                                                                             |
| `-r`, `--repository`                       | Repository within the registry. Default: `jharmison/models`.                                                                                                   |
| `-t`, `--tag`                              | The tag to push with, otherwise build from normalized model name.                                                                                              |
| `--base-image`                             | Base OCI image. Default: `registry.access.redhat.com/ubi10/ubi-micro:10.2`.                                                                                    |
| `--pull` / `--no-pull`                     | Pull base image if a newer version is available. Default: `--pull`.                                                                                            |
| `--push` / `--no-push`                     | Push the image after building. Default: `--push`.                                                                                                              |
| `-a`, `--authfile`                         | Path to skopeo-compatible authfile for registry push (used on push, pull, and inspect actions for `skopeo`, otherwise inherits default behavior).              |
| `--image-clean-up` / `--no-image-clean-up` | Remove local container image after push. Default: `--no-image-clean-up`.                                                                                       |
| `--model-clean-up` / `--no-model-clean-up` | Remove downloaded model files after build. Default: `--no-model-clean-up`.                                                                                     |
| `--skip-if-exists` / `--no-skip-if-exists` | Skip if the tag already exists at the registry. Default: `--skip-if-exists`.                                                                                   |
| `-v`, `--verbose`                          | Increase logging verbosity. Repeat for more.                                                                                                                   |
| `-V`, `--version`                          | Print version and exit.                                                                                                                                        |

**Image tag format:** `{registry}/{repository}:{specified tag or normalized-model}-modelcar`

When no tag is specified, the model repo ID is normalized for use as a tag: slashes become `--`, dots become `_`, and
the string is lowercased before appending `-modelcar`. For example, `meta-llama/Llama-3.2-3B-Instruct` produces tag
`meta-llama--llama_3-2-3b-instruct-modelcar`.

**Examples:**

```bash
# Build and push a model
modelcar-maker meta-llama/Llama-3.2-3B-Instruct

# Build locally without pushing
modelcar-maker BAAI/bge-large-en-v1.5 --no-push

# Build with cleanup
modelcar-maker mistralai/Mistral-7B --image-clean-up --model-clean-up
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

Models may have additional configuration applied to them by using the model repository ID in a subkey under the `models`
section of the config. This allows mapping tags and file lists to specific models via config.

**Example `config.toml`:**

```toml
[image]
registry = "quay.io"
repository = "myorg/models"

[models]
default = [
  "meta-llama/Llama-3.2-3B-Instruct",
  "BAAI/bge-large-en-v1.5",
  "unsloth/Qwen3.5-9B-MTP-GGUF",
]
"unsloth/Qwen3.5-9B-MTP-GGUF".files = ["README.md", "Qwen3.5-9B-Q4_K_S.gguf"]
"unsloth/Qwen3.5-9B-MTP-GGUF".tag = "unsloth--qwen3_5-9b-q4_k_s-modelcar"
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

## Running in Kubernetes/OpenShift

A helm chart is published at `oci://ghcr.io/jharmison-redhat/modelcar-maker-chart` (with versioned tags).

You can view the full default values list with the following command:

```bash
helm show values oci://ghcr.io/jharmison-redhat/modelcar-maker-chart
```

A minimal `values.yaml` to publish a few models to an ImageStream in an OpenShift cluster might look like this (but
probably with a real HF_TOKEN specified):

```yaml
---
huggingFace:
  token: hf_asdfasdf
models:
  - repo: TinyLlama/TinyLlama-1.1B-Chat-v1.0
  - repo: unsloth/Qwen3.5-9B-MTP-GGUF
    files:
      - README.md
      - Qwen3.5-9B-Q4_K_S.gguf
    tag: unsloth--qwen3_5-9b-q4_k_s-modelcar
  - repo: unsloth/Nemotron-3-Nano-30B-A3B-GGUF
    files:
      - README.md
      - Nemotron-3-Nano-30B-A3B-UD-Q8_K_XL.gguf
    tag: unsloth--nemotron-3-nano-30b-a3b-ud-q8_k_xl-modelcar
```

Which, if that file was named `values.yaml` in your local directory, you could install with a command like this:

```bash
helm_args=(
    upgrade --install                                   # this lets you reuse the same command to upgrade
                                                        # or change values later

    -n modelcars --create-namespace                     # pick whichever namespace you like

    modelcar-maker                                      # the release-name would allow you to install different
                                                        # copies of the chart,
                                                        # for all the reasons you might want to do that

    oci://ghcr.io/jharmison-redhat/modelcar-maker-chart # You can specify a version tag, too, if you like

    -f values.yaml                                      # Keep your values updated over time to rerun
)
helm "${helm_args[@]}"
```

For non-OpenShift Kubernetes, you'll want to disable the ImageStream and specify a destination registry and push secret.
A minimal configuration for that looks like this (note that you still need to specify models):

```yaml
---
modelcar:
  image:
    registry: quay.io
    repository: youruser/yourrepo
  imageStream:
    create: false
  dockerConfigJson: |
    {
      "auths": {
        "quay.io": {
          "auth": "ZmFrZXVzZXI6ZmFrZXBhc3N3b3Jk"
        }
      }
    }
```

The `dockerConfigJson` section here should be the entire pull secret necessary for building your Modelcar image,
including pulling the base image (if you override the publicly pullable default) and pushing the built image. There are
lots of ways to make these, including by hand, using `podman login` with credentials from your registry and copying the
file it creates, etc. It doesn't require any special handling and is passed directly to `skopeo` as
`~/.docker/config.json` which it always falls back to using.

## License

ISC. See [LICENSE](LICENSE).
