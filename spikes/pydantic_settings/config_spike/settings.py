"""Configuration loading prototype; no settings instance is created at import time."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import PydanticBaseSettingsSource
from pydantic_settings import SettingsConfigDict

DEFAULTS: dict[str, Any] = {
    "meta": {"verbosity": 0},
    "image": {
        "registry": "quay.io",
        "repository": "jharmison/models",
        "base_image": "registry.access.redhat.com/ubi10/ubi-micro:10.2",
        "cleanup": False,
        "skip_if_exists": True,
        "push": True,
        "pull": True,
        "labels": {"name": "{tag}"},
        "annotations": {},
    },
    "models": {"cleanup": False, "default": []},
}


def merge_config(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge mappings and append lists, matching observed Dynaconf merging."""
    merged = deepcopy(dict(base))
    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = merge_config(current, value)
        elif isinstance(current, list) and isinstance(value, list):
            merged[key] = [*current, *deepcopy(value)]
        else:
            merged[key] = deepcopy(value)
    return merged


def read_toml(path: Path) -> dict[str, Any]:
    """Read one optional TOML layer; a missing file has no effect."""
    if not path.is_file():
        return {}
    with path.open("rb") as stream:
        return tomllib.load(stream)


def config_layers(
    *,
    system_files: Sequence[Path] = (),
    user_file: Path | None = None,
    cwd: Path | None = None,
    config_file: Path | None = None,
) -> dict[str, Any]:
    """Build file values in the same low-to-high order as the current Dynaconf module."""
    paths = [*system_files]
    if user_file is not None:
        paths.append(user_file)
    if cwd is not None:
        paths.append(cwd / "config.toml")
    if config_file is not None:
        paths.append(config_file)

    values = deepcopy(DEFAULTS)
    for path in paths:
        values = merge_config(values, read_toml(path))
    return values


class MetaSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verbosity: int = 0


class ImageSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registry: str
    repository: str
    base_image: str
    cleanup: bool
    skip_if_exists: bool
    push: bool
    pull: bool
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)


class ModelsSettings(BaseModel):
    """Known options plus arbitrary repository-id keys and their arbitrary values."""

    model_config = ConfigDict(extra="allow")

    cleanup: bool = False
    default: list[str] = Field(default_factory=list)

    def for_repo(self, repo_id: str) -> dict[str, Any]:
        value = (self.model_extra or {}).get(repo_id, {})
        if not isinstance(value, dict):
            raise TypeError(f"Model configuration for {repo_id!r} must be a table")
        return value


class Settings(BaseSettings):
    """Typed settings whose environment and dotenv sources override supplied file values."""

    model_config = SettingsConfigDict(
        env_prefix="MODELCAR_MAKER_",
        env_nested_delimiter="__",
        extra="forbid",
    )

    meta: MetaSettings
    image: ImageSettings
    models: ModelsSettings

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Pydantic normally gives init values priority. File values are passed as
        # init values here, so reverse that order to retain Dynaconf's env priority.
        return env_settings, dotenv_settings, init_settings, file_secret_settings


def load_settings(
    *,
    system_files: Sequence[Path] = (),
    user_file: Path | None = None,
    cwd: Path | None = None,
    config_file: Path | None = None,
    dotenv_file: Path | None = None,
) -> Settings:
    """Construct settings on demand, without an import-time global instance."""
    return Settings(
        _env_file=dotenv_file if dotenv_file is not None else None,
        **config_layers(
            system_files=system_files,
            user_file=user_file,
            cwd=cwd,
            config_file=config_file,
        ),
    )
