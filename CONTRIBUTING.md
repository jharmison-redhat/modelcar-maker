# Contributing

Use Python 3.11 or 3.12 and install the development tools:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Fast checks need no registry credentials or containers. The combined `py311,py312` command requires both interpreters;
if only one is available, run its single-factor environment instead (for example, `tox run -e py312`):

```bash
tox run -e format
tox run -e lint
tox run -e mypy
tox run -e pyright
tox run -e py311,py312
tox run -e build
tox run -e chart
```

`chart` requires `helm`. The slow real integration check requires network access to Hugging Face, `podman`, and
`skopeo`; it also needs a usable `REGISTRY_AUTH_FILE` when your environment requires registry authentication:

```bash
tox run -e integration
```

The integration check is deliberately separate from the fast unit environments.
