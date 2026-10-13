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

CONFIG = b'{"architecture":"amd64","os":"linux"}'


def descriptor(contents: bytes, media_type: str) -> dict[str, str | int]:
    return {
        "mediaType": media_type,
        "digest": f"sha256:{hashlib.sha256(contents).hexdigest()}",
        "size": len(contents),
    }


def manifest_contents(marker: str) -> bytes:
    layer = marker.encode()
    return json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": descriptor(CONFIG, "application/vnd.oci.image.config.v1+json"),
            "layers": [descriptor(layer, "application/vnd.oci.image.layer.v1.tar")],
        },
        separators=(",", ":"),
    ).encode()


REMOTE_MANIFEST = manifest_contents("new").decode()
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


def write_blob(path: Path, contents: bytes) -> None:
    digest = hashlib.sha256(contents).hexdigest()
    blob = path / "blobs" / "sha256" / digest
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(contents)


def write_layout(path: Path, marker: str) -> str:
    path.mkdir(parents=True, exist_ok=True)
    manifest = manifest_contents(marker)
    layer = marker.encode()
    write_blob(path, CONFIG)
    write_blob(path, layer)
    write_blob(path, manifest)
    manifest_descriptor = descriptor(manifest, "application/vnd.oci.image.manifest.v1+json")
    (path / "oci-layout").write_text(json.dumps({"imageLayoutVersion": "1.0.0"}))
    (path / "index.json").write_text(json.dumps({"schemaVersion": 2, "manifests": [manifest_descriptor]}))
    (path / "marker").write_text(marker)
    return str(manifest_descriptor["digest"])


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
    write_layout(base.path, "new")
    write_metadata(base, base.tagged_image, REMOTE_DIGEST)
    pulls = []
    monkeypatch.setattr(image_module, "skopeo_inspect", lambda **kwargs: REMOTE_MANIFEST)
    monkeypatch.setattr(image_module, "skopeo_pull", lambda **kwargs: pulls.append(kwargs))

    base.pull()

    assert pulls == []
    assert (base.path / "marker").read_text() == "new"


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


@pytest.mark.parametrize("problem", ["missing-layout", "missing-blob", "missing-child", "corrupt-blob", "wrong-size"])
def test_oci_layout_validation_rejects_incomplete_or_corrupt_content(tmp_path: Path, problem: str) -> None:
    base = make_base(tmp_path / "base", update=False)
    write_layout(base.path, "valid")
    index = json.loads((base.path / "index.json").read_text())
    manifest_descriptor = index["manifests"][0]
    manifest_blob = base.path / "blobs" / "sha256" / manifest_descriptor["digest"].removeprefix("sha256:")

    if problem == "missing-layout":
        (base.path / "oci-layout").unlink()
    elif problem == "missing-blob":
        manifest_blob.unlink()
    elif problem == "missing-child":
        manifest = json.loads(manifest_blob.read_text())
        config_digest = manifest["config"]["digest"].removeprefix("sha256:")
        (base.path / "blobs" / "sha256" / config_digest).unlink()
    elif problem == "corrupt-blob":
        contents = manifest_blob.read_bytes()
        manifest_blob.write_bytes(b"x" + contents[1:])
    else:
        manifest_descriptor["size"] += 1
        (base.path / "index.json").write_text(json.dumps(index))

    assert not base.exists


def test_pull_rejects_top_manifest_digest_mismatch_and_preserves_cache(tmp_path: Path, monkeypatch) -> None:
    base = make_base(tmp_path / "base")
    write_layout(base.path, "old")
    write_metadata(base, base.tagged_image, "sha256:old")

    def pull(*, base_image, dest, **kwargs):
        write_layout(Path(dest), "different")
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(image_module, "skopeo_inspect", lambda **kwargs: REMOTE_MANIFEST)
    monkeypatch.setattr(image_module, "skopeo_pull", pull)

    with pytest.raises(RuntimeError, match="matching"):
        base.pull()

    assert (base.path / "marker").read_text() == "old"


def test_push_requires_remote_digest_after_successful_copy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    image = ModelcarImage(
        base_image=make_base(tmp_path / "base"),
        model=Model(repo_id="org/model"),
        skopeo=Skopeo(),
        registry="registry",
        repository="repository",
    )
    monkeypatch.setattr(image_module.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(image_module, "skopeo_inspect", lambda **kwargs: None)

    with pytest.raises(RuntimeError, match="could not determine its remote digest"):
        image.push()


def test_build_removes_partial_layout_without_index(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / ".workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    base = make_base(tmp_path / "base")
    model = Model(repo_id="org/model", commit="a" * 40)
    model.path.mkdir(parents=True)
    visible_model_file = model.path / "weights.bin"
    visible_model_file.write_text("weights")
    image = ModelcarImage(
        base_image=base,
        model=model,
        skopeo=Skopeo(),
        registry="registry",
        repository="repository",
    )
    image.layout_dir.mkdir(parents=True)
    (image.layout_dir / "stale").write_text("stale")

    def copy_to(self: BaseImage, dest: Path) -> None:
        assert not dest.exists()
        dest.mkdir()
        (dest / "fresh").write_text("fresh")

    build_arguments = {}

    def build_layers(**kwargs) -> None:
        build_arguments.update(kwargs)

    monkeypatch.setattr(BaseImage, "copy_to", copy_to)
    monkeypatch.setattr(image_module, "oci_layers_on_top", build_layers)

    image.build()

    assert not (image.layout_dir / "stale").exists()
    assert (image.layout_dir / "fresh").read_text() == "fresh"
    assert build_arguments["model_files"] == [visible_model_file]


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
        Model(repo_id="org/model", unexpected=True)  # type: ignore[call-arg]


def test_image_digest_reads_oci_index(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    image = ModelcarImage(
        base_image=make_base(tmp_path / "base"),
        model=Model(repo_id="org/model"),
        skopeo=Skopeo(),
        registry="registry",
        repository="repository",
    )
    expected = write_layout(image.layout_dir, "manifest")

    assert image.digest() == expected
