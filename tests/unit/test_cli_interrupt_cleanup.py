"""Interrupt during build must still run requested model cleanup (finally block)."""

import importlib
import shutil

import pytest
from typer.testing import CliRunner

cli_module = importlib.import_module("modelcar_maker.cli.cli")

runner = CliRunner()

MODEL_DIR: object = None  # set by fixture to a tmp_path subdir


class FakeSkopeo:
    def __init__(self, **kwargs):
        pass


class FakeBase:
    def __init__(self, **kwargs):
        self.path = "base"

    def pull(self):
        pass


class FakeModel:
    def __init__(self, repo_id, files):
        self.repo_id = repo_id
        self.path = MODEL_DIR / repo_id
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / "weights.bin").write_bytes(b"x")

    def download(self, progress=True):
        return {"payload_files": 1, "payload_bytes": 1}

    def cleanup(self):
        print("CLEANUP-RAN", self.path)
        shutil.rmtree(self.path)


class FakeImage:
    def __init__(self, base_image, model, **kwargs):
        self.model = model
        self.tagged_image = f"{model.repo_id}"
        self.layout_dir = self.model.path

    def exists_remote(self):
        return False

    def build(self):
        print("BUILD-RAN", self.model.path)
        raise KeyboardInterrupt

    def cleanup(self):
        pass


@pytest.fixture(autouse=True)
def model_dirs(tmp_path, monkeypatch):
    root = tmp_path / "models"
    global MODEL_DIR
    previous = MODEL_DIR
    MODEL_DIR = root
    monkeypatch.setattr(cli_module, "Skopeo", FakeSkopeo)
    monkeypatch.setattr(cli_module, "BaseImage", FakeBase)
    monkeypatch.setattr(cli_module, "Model", FakeModel)
    monkeypatch.setattr(cli_module, "ModelcarImage", FakeImage)
    yield root
    MODEL_DIR = previous


def test_keyboard_interrupt_during_build_still_cleans_model_dir():
    result = runner.invoke(cli_module.cli, ["build", "--model-clean-up", "--no-push"])

    assert result.exit_code == 130
    assert not (MODEL_DIR / "build").exists()
