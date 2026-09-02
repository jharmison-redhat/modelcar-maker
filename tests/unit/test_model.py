from pathlib import Path

import pytest

import modelcar_maker.model.model as model_module
from modelcar_maker.model import Model
from modelcar_maker.model.model import reconcile_model_files

SHA = "a" * 40


class FakeApi:
    def model_info(self, repo_id: str):
        return type("Info", (), {"sha": SHA})()

    def list_repo_files(self, repo_id: str, revision: str) -> list[str]:
        assert revision == SHA
        return ["README.md", "weights/model.bin"]


def test_reconcile_prunes_stale_payload_and_unselected_metadata(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    (stage / "weights").mkdir(parents=True)
    (stage / "weights" / "model.bin").write_text("selected")
    (stage / "stale.bin").write_text("stale")
    metadata = stage / ".cache" / "huggingface" / "download"
    metadata.mkdir(parents=True)
    (metadata / "stale.bin.metadata").write_text("stale")
    selected_metadata = metadata / "weights" / "model.bin.metadata"
    selected_metadata.parent.mkdir()
    selected_metadata.write_text("selected")

    reconcile_model_files(stage, {Path("weights/model.bin")})

    assert (stage / "weights" / "model.bin").read_text() == "selected"
    assert selected_metadata.read_text() == "selected"
    assert not (stage / "stale.bin").exists()
    assert not (metadata / "stale.bin.metadata").exists()


def test_download_pins_revision_reconciles_selection_and_promotes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    live = tmp_path / "models" / "org--model"
    live.mkdir(parents=True)
    (live / "stale.bin").write_text("old")
    revisions = []

    def download(*, repo_id, revision, local_dir, filename, tqdm_class):
        revisions.append(revision)
        target = Path(local_dir) / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("new")
        metadata = Path(local_dir) / ".cache" / "huggingface" / "download" / Path(filename).parent
        metadata.mkdir(parents=True, exist_ok=True)
        (metadata / f"{Path(filename).name}.metadata").write_text(f"{revision}\netag\n")
        return str(target)

    monkeypatch.setattr(model_module, "HfApi", FakeApi)
    monkeypatch.setattr(model_module, "hf_hub_download", download)
    model = Model(repo_id="org/model", files=["weights/model.bin"])

    stats = model.download(progress=False)

    assert revisions == [SHA]
    assert model.commit == SHA
    assert stats == {"files": 1, "bytes": 3}
    assert (model.path / "weights" / "model.bin").read_text() == "new"
    assert not (model.path / "stale.bin").exists()


def test_failed_download_preserves_complete_live_model(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    live = tmp_path / "models" / "org--model"
    live.mkdir(parents=True)
    (live / "model.bin").write_text("old-complete")

    def fail_snapshot(repo_id, revision, local_dir, tqdm_class):
        assert revision == SHA
        (Path(local_dir) / "partial.bin").write_text("partial")
        raise RuntimeError("download failed")

    monkeypatch.setattr(model_module, "HfApi", FakeApi)
    monkeypatch.setattr(model_module, "snapshot_download", fail_snapshot)
    model = Model(repo_id="org/model")

    with pytest.raises(RuntimeError, match="download failed"):
        model.download()

    assert (live / "model.bin").read_text() == "old-complete"
    assert not (live / "partial.bin").exists()
    assert model.commit is None
    assert not list(live.parent.glob(".org--model.stage-*"))


def test_unverified_staged_revision_does_not_replace_live_model(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    live = tmp_path / "models" / "org--model"
    selected = live / "weights" / "model.bin"
    selected.parent.mkdir(parents=True)
    selected.write_text("old")
    metadata = live / ".cache" / "huggingface" / "download" / "weights"
    metadata.mkdir(parents=True)
    (metadata / "model.bin.metadata").write_text(f"{'b' * 40}\netag\n")

    monkeypatch.setattr(model_module, "HfApi", FakeApi)
    monkeypatch.setattr(model_module, "hf_hub_download", lambda **kwargs: str(selected))
    model = Model(repo_id="org/model", files=["weights/model.bin"])

    with pytest.raises(RuntimeError, match="not verified"):
        model.download()

    assert selected.read_text() == "old"
    assert model.commit is None


def test_selected_file_must_exist_at_pinned_revision(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(model_module, "HfApi", FakeApi)
    model = Model(repo_id="org/model", files=["missing.bin"])

    with pytest.raises(RuntimeError, match="not present"):
        model.download()
