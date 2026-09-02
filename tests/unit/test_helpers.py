import os
from pathlib import Path

import pytest

import modelcar_maker.util.helpers as helpers_module
from modelcar_maker.util.helpers import Truthy
from modelcar_maker.util.helpers import atomic_replace_directory
from modelcar_maker.util.helpers import cleanup
from modelcar_maker.util.helpers import normalize
from modelcar_maker.util.helpers import seed_directory


class TestNormalize:
    def test_lowercase_and_replace_slash(self):
        assert normalize("MyOrg/MyModel") == "myorg--mymodel"

    def test_replace_dot(self):
        assert normalize("org/model.v2") == "org--model_v2"

    def test_already_normalized(self):
        assert normalize("already--normalized") == "already--normalized"


class TestTruthy:
    @pytest.mark.parametrize("value", ["true", "True", "TRUE"])
    def test_true(self, value):
        assert bool(Truthy(value)) is True

    @pytest.mark.parametrize("value", ["yes", "Yes", "YES"])
    def test_yes(self, value):
        assert bool(Truthy(value)) is True

    @pytest.mark.parametrize("value", ["1"])
    def test_one(self, value):
        assert bool(Truthy(value)) is True

    @pytest.mark.parametrize("value", ["false", "no", "0", "", "maybe", "2"])
    def test_false(self, value):
        assert bool(Truthy(value)) is False


def test_cleanup_nested_files_and_skipped_top_level_entry(tmp_path: Path) -> None:
    root = tmp_path / "root"
    nested = root / "nested" / "deeper"
    nested.mkdir(parents=True)
    (nested / "file").write_text("remove")
    (root / "keep").mkdir()
    (root / "keep" / "file").write_text("keep")

    assert cleanup(root, skip=["keep"])
    assert [path.name for path in root.iterdir()] == ["keep"]
    assert (root / "keep" / "file").read_text() == "keep"


def test_cleanup_unlinks_directory_symlink_without_traversing_it(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)

    assert cleanup(root)
    assert not (root / "link").exists()
    assert (outside / "keep").read_text() == "keep"


def test_seed_directory_hardlinks_payload_but_copies_mutable_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source"
    stage = tmp_path / "stage"
    source.mkdir()
    stage.mkdir()
    (source / "payload").write_text("data")
    metadata = source / ".cache" / "huggingface" / "download" / "payload.metadata"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("metadata")

    seed_directory(source, stage)

    assert os.stat(source / "payload").st_ino == os.stat(stage / "payload").st_ino
    assert os.stat(metadata).st_ino != os.stat(stage / metadata.relative_to(source)).st_ino


def test_atomic_replace_restores_live_paths_when_companion_promotion_fails(tmp_path: Path, monkeypatch) -> None:
    live = tmp_path / "live"
    stage = tmp_path / "stage"
    live_metadata = tmp_path / "live.json"
    stage_metadata = tmp_path / "stage.json"
    live.mkdir()
    stage.mkdir()
    (live / "marker").write_text("old")
    (stage / "marker").write_text("new")
    live_metadata.write_text("old")
    stage_metadata.write_text("new")
    rename = Path.rename
    events: list[tuple[Path, Path]] = []

    def fail_companion(self: Path, target: Path) -> Path:
        events.append((self, target))
        if self == stage_metadata:
            raise OSError("promotion failed")
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_companion)

    with pytest.raises(OSError, match="promotion failed"):
        atomic_replace_directory(
            stage,
            live,
            companion_replacements=((stage_metadata, live_metadata),),
        )

    assert (live / "marker").read_text() == "old"
    assert live_metadata.read_text() == "old"
    displaced = next(index for index, event in enumerate(events) if event[1].name.startswith(".live.rollback-"))
    restored = next(index for index, event in enumerate(events) if event[0].name.startswith(".live.backup-"))
    assert displaced < restored


def test_atomic_replace_rolls_back_keyboard_interrupt(tmp_path: Path, monkeypatch) -> None:
    live = tmp_path / "live"
    stage = tmp_path / "stage"
    live_metadata = tmp_path / "live.json"
    stage_metadata = tmp_path / "stage.json"
    live.mkdir()
    stage.mkdir()
    (live / "marker").write_text("old")
    (stage / "marker").write_text("new")
    live_metadata.write_text("old")
    stage_metadata.write_text("new")
    rename = Path.rename

    def interrupt_companion(self: Path, target: Path) -> Path:
        if self == stage_metadata:
            raise KeyboardInterrupt()
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", interrupt_companion)

    with pytest.raises(KeyboardInterrupt):
        atomic_replace_directory(stage, live, companion_replacements=((stage_metadata, live_metadata),))

    assert (live / "marker").read_text() == "old"
    assert live_metadata.read_text() == "old"


def test_atomic_replace_ignores_backup_cleanup_failure_after_commit(tmp_path: Path, monkeypatch) -> None:
    live = tmp_path / "live"
    stage = tmp_path / "stage"
    live.mkdir()
    stage.mkdir()
    (live / "marker").write_text("old")
    (stage / "marker").write_text("new")

    def fail_cleanup(path: Path) -> None:
        raise OSError(f"cannot remove {path}")

    monkeypatch.setattr(helpers_module, "remove_path", fail_cleanup)

    atomic_replace_directory(stage, live)

    assert (live / "marker").read_text() == "new"
