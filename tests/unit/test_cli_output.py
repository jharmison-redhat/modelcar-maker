import importlib
import json
from pathlib import Path

from typer.testing import CliRunner

cli_module = importlib.import_module("modelcar_maker.cli.cli")


runner = CliRunner()


class FakeSkopeo:
    def __init__(self, authfile=None):
        pass


class FakeBase:
    def __init__(self, **kwargs):
        self.path = Path("base")

    def pull(self):
        pass


class FakeModel:
    def __init__(self, repo_id, files):
        self.repo_id = repo_id
        self.path = Path(repo_id)

    def download(self, progress=True):
        return {"files": 2, "bytes": 3}

    def exists(self):
        return False

    def cleanup(self):
        pass


class FakeImage:
    def __init__(self, model, registry, repository, **kwargs):
        self.model = model
        self.tagged_image = f"{registry}/{repository}:{model.repo_id}"
        self.layout_dir = Path(model.repo_id)

    def exists_remote(self):
        return self.model.repo_id == "skip"

    def exists_local(self):
        return False

    def build(self):
        if self.model.repo_id == "bad":
            raise RuntimeError("build failed")
        return {"files": 2, "layers": 2}

    def digest(self):
        return "sha256:local"

    def push(self):
        return "sha256:remote"

    def cleanup(self):
        pass


def configure_models(monkeypatch, models):
    monkeypatch.setattr(cli_module, "Skopeo", FakeSkopeo)
    monkeypatch.setattr(cli_module, "BaseImage", FakeBase)
    monkeypatch.setattr(cli_module, "Model", FakeModel)
    monkeypatch.setattr(cli_module, "ModelcarImage", FakeImage)
    monkeypatch.setattr(cli_module.settings.models, "default", models)


def test_json_output_is_single_document_and_continues_after_failure(monkeypatch) -> None:
    configure_models(monkeypatch, ["ok", "skip", "bad"])

    result = runner.invoke(cli_module.cli, ["--output", "json", "--push"])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "partial"
    assert list(data) == ["status", "duration_seconds", "results", "error"]
    assert data["error"] is None
    assert [item["status"] for item in data["results"]] == ["success", "skipped", "failed"]
    assert data["results"][0]["download"] == {"files": 2, "bytes": 3}
    assert data["results"][0]["build"] == {"files": 2, "layers": 2}
    assert data["results"][0]["push"] == {"digest": "sha256:remote"}
    assert "build failed" in result.stderr


def test_fail_fast_and_quiet_text_output(monkeypatch) -> None:
    configure_models(monkeypatch, ["bad", "ok"])

    result = runner.invoke(cli_module.cli, ["--quiet", "--fail-fast"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "build failed" in result.stderr
