---
name: dev-workflows
description: Manage Python development environment setup, code formatting, linting, and testing for this project. Use when creating or modifying Python files, setting up the project, or verifying changes.
metadata:
  author: modelcar-maker
  version: "1.0"
---

# Dev Workflows

## Environment Setup

Before doing any development work, ensure the virtual environment exists and dependencies are installed:

1. Create the virtual environment with Python 3.12:
   ```bash
   uv venv -p 3.12
   ```

2. Install the project in editable mode with dev dependencies:
   ```bash
   uv pip install -e ".[dev]"
   ```

If `.venv/` is missing or Python version is wrong, start with step 1. If dependencies are missing, run step 2.

## Code Formatting

Before finishing any task that creates or modifies Python files, run the formatter:

```bash
uv run tox -e format
```

This runs isort (import sorting) and black (code formatting). Do not skip formatting even for "small" changes. Do not ask the user before running format — just do it.

## Linting

After formatting, or before committing, verify that lint passes:

```bash
uv run tox -e lint
```

This runs black --check, isort --check-only, flake8, and mypy. Fix any failures that arise.

## Testing

After making changes, run the test suite:

```bash
uv run tox -e py312
```

This runs pytest against the test suite and verifies everything passes.

## Full Verification Workflow

For a complete check before considering a task done, run all default environments:

```bash
uv run tox
```

This runs `py311`, `py312`, `py313`, and `lint`. Fix any failures before finishing.

## Troubleshooting

- If the formatter is not available, install dev dependencies: `uv pip install -e ".[dev]"`
- If formatting changes create new lint issues, fix them inline
