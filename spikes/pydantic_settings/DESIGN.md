# Pydantic Settings configuration spike

This isolated prototype does not import or modify production configuration code. It uses
`pydantic-settings` only from this directory's `pyproject.toml`. Run it with:

```console
python -m pip install -e '.[test]'
pytest
ruff check .
ruff format --check .
```

## Prototype design

`load_settings()` is an on-demand factory. It starts with built-in defaults, then merges
optional system files, a user file, `cwd/config.toml`, and an explicit file in that
order. Missing files are ignored; invalid existing TOML raises the standard
`tomllib.TOMLDecodeError`. Pydantic Settings sources are deliberately ordered as
environment, optional dotenv, then merged file values. This makes environment variables
highest priority, unlike the normal Pydantic init-value-first order.

Nested variables use `MODELCAR_MAKER_IMAGE__REGISTRY` and
`MODELCAR_MAKER_MODELS__ORG/MODEL-NAME__CUSTOM`. `ModelsSettings` has typed known
fields and allows repository-id extras, so TOML table names containing slashes/dashes
and arbitrary per-model keys remain available through `for_repo()`. The prototype
recursively merges tables and appends lists, rather than relying on a Pydantic source
to merge files. No module-level `settings` instance exists.

## Current Dynaconf characterization

The current `util/config.py` (Dynaconf `>=3.2.13,<3.3.0`) constructs a global object
at import time. It loads the package defaults, then includes
`/usr/share/modelcar-maker/config.toml`, `/etc/modelcar-maker/config.toml`, the XDG
user file, `Path.cwd() / "config.toml"`, and finally `MODELCAR_MAKER_CONFIG`. A
relative explicit path is resolved from the import-time cwd. With `merge_enabled=True`,
a local experiment using the installed compatible Dynaconf release found later scalar
values win, nested mappings retain keys from each layer, and lists append (for example
`["default.bin"]` plus `["user.bin"]`).

The same experiment verified `MODELCAR_MAKER_IMAGE__REGISTRY` overrides file values.
Dynaconf accepts slash/dash repository table keys; nested environment spelling is the
same double-underscore convention, although setting an environment variable containing
a slash requires an API such as `os.environ` or `env`, not ordinary shell assignment.
Missing include files are ignored. Existing malformed TOML raises Dynaconf's TOML parse
error when the lazy settings object is first accessed. The spike raises immediately and
tests that behavior. Dynaconf's
current module enables dotenv by default unless `MODELCAR_MAKER_SKIP_LOAD_FILES` is
truthy, which also suppresses all include files.

## Comparison and recommendation

The spike can preserve the observed precedence and merge behavior, supports the needed
nested and arbitrary model configuration, and makes test-time construction deterministic.
However, preserving Dynaconf behavior requires custom TOML discovery, explicit deep
merge/list-append logic, reversed Pydantic source ordering, permissive dynamic model
extras, and a decision about validation of arbitrary values. That is more code and more
customization than the present Dynaconf loader.

**Recommendation: retain Dynaconf.** Under the PLAN criteria, a migration is justified
only when it is simpler *and* preserves precedence. The prototype meets the latter but
not the former. Validate only final domain objects at their boundaries; do not layer
full Dynaconf validation with duplicate Pydantic settings validation.
