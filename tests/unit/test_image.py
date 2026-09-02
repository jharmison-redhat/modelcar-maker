import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

import modelcar_maker.image.image as image_module
from modelcar_maker.image import BaseImage
from modelcar_maker.image import ModelcarImage
from modelcar_maker.image import Skopeo
from modelcar_maker.image.image import _reference_at_digest
from modelcar_maker.model import Model

REMOTE_MANIFEST = '{"schemaVersion":2}'
REMOTE_DIGEST = f"sha256:{hashlib.sha256(REMOTE_MANIFEST.encode()).hexdigest()}"


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("registry.example/repository/base:latest", f"registry.example/repository/base@{REMOTE_DIGEST}"),
        ("registry.example:5000/repository/base:v1", f"registry.example:5000/repository/base@{REMOTE_DIGEST}"),
        ("registry.example/repository/base", f"registry.example/repository/base@{REMOTE_DIGEST}"),
    ],
)
def test_reference_at_digest_replaces_tag(reference: str, expected: str) -> None:
    assert _reference_at_digest(reference, REMOTE_DIGEST) == expected


def write_layout(path: Path, marker: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "index.json").write_text(json.dumps({"manifests": [{"digest": f"sha256:{marker}"}]}))
    (path / "marker").write_text(marker)


def write_metadata(base: BaseImage, reference: str, digest: str) -> None:
    base.metadata_path.write_text(json.dumps({"requested_reference": reference, "resolved_digest": digest}))


def make_base(path: Path, update: bool = True) -> BaseImage:
    return BaseImage(
        tagged_image="registry.example/repository/base:latest",
        skopeo=Skopeo(),
        update=update,
        path=path,
    )


def test_matching_cache_metadata_avoids_pull(tmp_path: Path, monkeypatch) -> None:
    base = make_base(tmp_path / "base")
    write_layout(base.path, "old")
    write_metadata(base, base.tagged_image, REMOTE_DIGEST)
    pulls = []
    monkeypatch.setattr(image_module, "skopeo_inspect", lambda **kwargs: REMOTE_MANIFEST)
    monkeypatch.setattr(image_module, "skopeo_pull", lambda **kwargs: pulls.append(kwargs))

    base.pull()

    assert pulls == []
    assert (base.path / "marker").read_text() == "old"


def test_pull_atomically_replaces_layout_and_metadata(tmp_path: Path, monkeypatch) -> None:
    base = make_base(tmp_path / "base")
    write_layout(base.path, "old")
    write_metadata(base, "other.example/base:old", "sha256:old")
    pulled_references = []

    def pull(*, base_image, dest, **kwargs):
        pulled_references.append(base_image)
        write_layout(Path(dest), "new")
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(image_module, "skopeo_inspect", lambda **kwargs: REMOTE_MANIFEST)
    monkeypatch.setattr(image_module, "skopeo_pull", pull)

    base.pull()

    assert pulled_references == [f"registry.example/repository/base@{REMOTE_DIGEST}"]
    assert (base.path / "marker").read_text() == "new"
    metadata = json.loads(base.metadata_path.read_text())
    assert metadata == {"requested_reference": base.tagged_image, "resolved_digest": REMOTE_DIGEST}
    assert base.metadata_path.parent == base.path.parent
    assert not base.metadata_path.is_relative_to(base.path)


def test_failed_staging_preserves_valid_cache_and_metadata(tmp_path: Path, monkeypatch) -> None:
    base = make_base(tmp_path / "base")
    write_layout(base.path, "old")
    write_metadata(base, base.tagged_image, "sha256:old")
    old_metadata = base.metadata_path.read_text()

    def fail_pull(*, base_image, dest, **kwargs):
        (Path(dest) / "partial").write_text("partial")
        return subprocess.CompletedProcess([], 1, stderr="failed")

    monkeypatch.setattr(image_module, "skopeo_inspect", lambda **kwargs: REMOTE_MANIFEST)
    monkeypatch.setattr(image_module, "skopeo_pull", fail_pull)

    with pytest.raises(RuntimeError, match="Pull of"):
        base.pull()

    assert (base.path / "marker").read_text() == "old"
    assert base.metadata_path.read_text() == old_metadata
    assert not list(tmp_path.glob(".base.stage-*"))


def test_pull_exception_preserves_valid_cache(tmp_path: Path, monkeypatch) -> None:
    base = make_base(tmp_path / "base")
    write_layout(base.path, "old")
    write_metadata(base, base.tagged_image, "sha256:old")
    old_metadata = base.metadata_path.read_text()
    monkeypatch.setattr(image_module, "skopeo_inspect", lambda **kwargs: REMOTE_MANIFEST)

    def fail_pull(**kwargs):
        raise subprocess.CalledProcessError(1, ["skopeo", "copy"])

    monkeypatch.setattr(image_module, "skopeo_pull", fail_pull)

    with pytest.raises(subprocess.CalledProcessError):
        base.pull()

    assert (base.path / "marker").read_text() == "old"
    assert base.metadata_path.read_text() == old_metadata


def test_inspection_failure_preserves_valid_cache(tmp_path: Path, monkeypatch) -> None:
    base = make_base(tmp_path / "base")
    write_layout(base.path, "old")
    monkeypatch.setattr(image_module, "skopeo_inspect", lambda **kwargs: None)

    with pytest.raises(RuntimeError, match="Unable to resolve"):
        base.pull()

    assert (base.path / "marker").read_text() == "old"


def test_no_pull_uses_legacy_or_mismatched_cache_without_inspection(tmp_path: Path, monkeypatch) -> None:
    base = make_base(tmp_path / "base", update=False)
    write_layout(base.path, "old")
    write_metadata(base, "another.example/base:tag", "sha256:other")

    def unexpected_inspect(**kwargs):
        raise AssertionError("no-pull must not inspect")

    monkeypatch.setattr(image_module, "skopeo_inspect", unexpected_inspect)

    base.pull()

    assert base.exists
    assert (base.path / "marker").read_text() == "old"


def test_no_pull_without_cache_fails_explicitly_when_build_needs_it(tmp_path: Path) -> None:
    base = make_base(tmp_path / "missing", update=False)
    base.pull()

    with pytest.raises(RuntimeError, match="No usable base image cache"):
        base.copy_to(tmp_path / "build")


def test_image_boundary_validation_and_extra_fields(tmp_path: Path) -> None:
    skopeo = Skopeo()
    base = make_base(tmp_path / "base")
    model = Model(repo_id="org/model")

    with pytest.raises(ValidationError):
        ModelcarImage(base_image=base, model=model, skopeo=skopeo, registry="", repository="repo")
    with pytest.raises(ValidationError):
        ModelcarImage(base_image=base, model=model, skopeo=skopeo, registry="registry", repository="")
    with pytest.raises(ValidationError):
        ModelcarImage(
            base_image=base,
            model=model,
            skopeo=skopeo,
            registry="registry",
            repository="repo",
            tag="bad/tag",
        )
    with pytest.raises(ValidationError):
        Model(repo_id="org/model", unexpected=True)


def test_image_digest_reads_oci_index(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    image = ModelcarImage(
        base_image=make_base(tmp_path / "base"),
        model=Model(repo_id="org/model"),
        skopeo=Skopeo(),
        registry="registry",
        repository="repository",
    )
    write_layout(image.layout_dir, "manifest")

    assert image.digest() == "sha256:manifest"
