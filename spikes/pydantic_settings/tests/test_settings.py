import os
import tomllib
from pathlib import Path

import pytest
from config_spike.settings import DEFAULTS
from config_spike.settings import config_layers
from config_spike.settings import load_settings


def write_toml(path: Path, contents: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    return path


@pytest.fixture(autouse=True)
def isolate_modelcar_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep results independent of the operator environment running the tests."""
    for key in list(os.environ):
        if key.startswith("MODELCAR_MAKER_"):
            monkeypatch.delenv(key)


def test_precedence_nested_models_and_custom_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    system = write_toml(
        tmp_path / "system.toml",
        """[image]
repository = "system"
[image.labels]
system = "yes"
[models."org/model-name"]
files = ["system.bin"]
custom = "system"
""",
    )
    user = write_toml(
        tmp_path / "user.toml",
        """[image]
registry = "user"
[models."org/model-name"]
files = ["user.bin"]
""",
    )
    write_toml(tmp_path / "cwd" / "config.toml", '[image]\nbase_image = "cwd"\n')
    explicit = write_toml(
        tmp_path / "explicit.toml",
        """[image]
registry = "explicit"
[image.labels]
explicit = "yes"
[models."org/model-name"]
custom = "explicit"
extra_key = { enabled = true }
""",
    )
    monkeypatch.setenv("MODELCAR_MAKER_IMAGE__REGISTRY", "environment")
    monkeypatch.setenv("MODELCAR_MAKER_MODELS__ORG/MODEL-NAME__CUSTOM", "environment")

    settings = load_settings(system_files=[system], user_file=user, cwd=tmp_path / "cwd", config_file=explicit)

    assert settings.image.registry == "environment"
    assert settings.image.repository == "system"
    assert settings.image.base_image == "cwd"
    assert settings.image.labels == {"name": "{tag}", "system": "yes", "explicit": "yes"}
    assert settings.models.for_repo("org/model-name") == {
        "files": ["system.bin", "user.bin"],
        "custom": "environment",
        "extra_key": {"enabled": True},
    }


def test_dotenv_is_opt_in_and_lower_precedence_than_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotenv = write_toml(
        tmp_path / ".env",
        "MODELCAR_MAKER_IMAGE__REPOSITORY=from-dotenv\nMODELCAR_MAKER_IMAGE__PUSH=false\n",
    )
    monkeypatch.setenv("MODELCAR_MAKER_IMAGE__REPOSITORY", "from-environment")

    without_dotenv = load_settings()
    with_dotenv = load_settings(dotenv_file=dotenv)

    assert without_dotenv.image.push is True
    assert with_dotenv.image.repository == "from-environment"
    assert with_dotenv.image.push is False


def test_missing_files_are_ignored_and_invalid_toml_fails(tmp_path: Path) -> None:
    assert config_layers(config_file=tmp_path / "missing.toml") == DEFAULTS
    invalid = write_toml(tmp_path / "invalid.toml", "[image\n")
    with pytest.raises(tomllib.TOMLDecodeError):
        load_settings(config_file=invalid)


def test_construction_has_no_import_time_state(tmp_path: Path) -> None:
    first = write_toml(tmp_path / "first.toml", '[image]\nregistry = "first"\n')
    second = write_toml(tmp_path / "second.toml", '[image]\nregistry = "second"\n')

    assert load_settings(config_file=first).image.registry == "first"
    assert load_settings(config_file=second).image.registry == "second"
