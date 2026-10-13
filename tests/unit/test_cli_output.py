import importlib
import json
from pathlib import Path

from typer.testing import CliRunner

cli_module = importlib.import_module("modelcar_maker.cli.cli")


runner = CliRunner()


class FakeSkopeo:
    def __init__(self, authfile=None, **kwargs):
        pass


class FakeBase:
    pull_calls = 0
    fail_pull = False

    def __init__(self, **kwargs):
        self.path = Path("base")

    def pull(self):
        type(self).pull_calls += 1
        if type(self).fail_pull:
            raise RuntimeError("base unavailable")


class FakeModel:
    cleanup_calls: list[str] = []
    cleanup_error = False
    progress_values: list[bool] = []

    def __init__(self, repo_id, files):
        self.repo_id = repo_id
        self.path = Path(repo_id)

    def download(self, progress=True):
        type(self).progress_values.append(progress)
        return {"payload_files": 2, "payload_bytes": 3}

    def exists(self):
        return False

    def cleanup(self):
        type(self).cleanup_calls.append(self.repo_id)
        if type(self).cleanup_error:
            raise RuntimeError("model cleanup broke")


class FakeImage:
    cleanup_calls: list[str] = []
    cleanup_error = False

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
        type(self).cleanup_calls.append(self.model.repo_id)
        if type(self).cleanup_error:
            raise RuntimeError("image cleanup broke")


def configure_models(monkeypatch, models):
    FakeBase.pull_calls = 0
    FakeBase.fail_pull = False
    FakeModel.cleanup_calls = []
    FakeModel.cleanup_error = False
    FakeModel.progress_values = []
    FakeImage.cleanup_calls = []
    FakeImage.cleanup_error = False
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
    assert data["results"][0]["download"] == {"payload_files": 2, "payload_bytes": 3}
    assert data["results"][0]["build"] == {"files": 2, "layers": 2}
    assert data["results"][0]["push"] == {"digest": "sha256:remote"}
    assert FakeModel.progress_values == [False, False]
    assert "build failed" in result.stderr


def test_fail_fast_and_quiet_text_output(monkeypatch) -> None:
    configure_models(monkeypatch, ["bad", "ok"])

    result = runner.invoke(cli_module.cli, ["--quiet", "--fail-fast"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "build failed" in result.stderr


def test_requested_cleanups_both_run_after_failure_without_replacing_primary_error(monkeypatch) -> None:
    configure_models(monkeypatch, ["bad"])
    FakeImage.cleanup_error = True
    FakeModel.cleanup_error = True

    result = runner.invoke(
        cli_module.cli,
        ["--output", "json", "--image-clean-up", "--model-clean-up", "--no-push"],
    )

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["results"][0]["error"] == "build failed"
    assert FakeImage.cleanup_calls == ["bad"]
    assert FakeModel.cleanup_calls == ["bad"]
    assert "image cleanup failed" in result.stderr
    assert "model cleanup failed" in result.stderr


def test_cleanup_only_failure_is_reported_as_failure(monkeypatch) -> None:
    configure_models(monkeypatch, ["ok"])
    FakeImage.cleanup_error = True

    result = runner.invoke(cli_module.cli, ["--output", "json", "--image-clean-up", "--no-push"])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "failed"
    assert data["results"][0]["status"] == "failed"
    assert data["results"][0]["error"] == "image cleanup failed: image cleanup broke"


def test_skip_cleanup_runs_once_and_all_skips_are_success(monkeypatch) -> None:
    configure_models(monkeypatch, ["skip"])

    result = runner.invoke(
        cli_module.cli,
        ["--output", "json", "--image-clean-up", "--model-clean-up", "--no-push"],
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["status"] == "success"
    assert data["results"][0]["status"] == "skipped"
    assert FakeImage.cleanup_calls == ["skip"]
    assert FakeModel.cleanup_calls == ["skip"]


def test_failed_plus_skipped_without_success_is_failed(monkeypatch) -> None:
    configure_models(monkeypatch, ["skip", "bad"])

    result = runner.invoke(cli_module.cli, ["--output", "json", "--no-push"])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "failed"


def test_shared_base_failure_stops_without_retry_and_sets_top_level_error(monkeypatch) -> None:
    configure_models(monkeypatch, ["first", "second"])
    FakeBase.fail_pull = True

    result = runner.invoke(cli_module.cli, ["--output", "json", "--no-push"])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert FakeBase.pull_calls == 1
    assert len(data["results"]) == 1
    assert data["status"] == "failed"
    assert "shared base image cache" in data["error"]
    assert "base unavailable" in data["error"]


def test_service_account_auth_and_authfile_are_mutually_exclusive(tmp_path) -> None:
    authfile = tmp_path / "auth.json"
    authfile.write_text("user")

    result = runner.invoke(
        cli_module.cli,
        ["build", "-a", str(authfile), "--service-account-auth"],
    )

    assert result.exit_code == 2
    assert "mutually exclusive" in result.stderr
