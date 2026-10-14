import base64
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

import modelcar_maker.image.credentials as credentials_module
import modelcar_maker.image.image as image_module
from modelcar_maker.image import BaseImage
from modelcar_maker.image import ModelcarImage
from modelcar_maker.image import Skopeo
from modelcar_maker.image.credentials import SaCredentials
from modelcar_maker.image.credentials import write_sa_authfile
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


# --- service-account authfile construction (targets image.py) ---


@pytest.fixture
def fake_token(tmp_path: Path, monkeypatch):
    token_path = tmp_path / "token"
    token_path.write_text("tok-123")
    monkeypatch.setattr(credentials_module, "SA_TOKEN_PATH", token_path)
    return token_path


def _make_image(tmp_path: Path, monkeypatch, skopeo: Skopeo) -> ModelcarImage:
    monkeypatch.chdir(tmp_path)
    return ModelcarImage(
        base_image=make_base(tmp_path / "base"),
        model=Model(repo_id="org/model"),
        skopeo=skopeo,
        registry="registry.example",
        repository="repository",
    )


def test_write_sa_authfile_scheme_mode_and_atomicity(tmp_path: Path, fake_token) -> None:
    target = tmp_path / "config.json"
    credentials_module.write_sa_authfile(target, "registry.example:5000")

    contents = target.read_text()
    expected_auth = base64.b64encode(b"serviceaccount:tok-123").decode()
    assert contents == '{"auths": {"registry.example:5000": {"auth": "%s"}}}' % expected_auth
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("tmp*"))  # temp file is gone after replace


def test_write_sa_authfile_requires_registry(tmp_path: Path, fake_token) -> None:
    with pytest.raises(ValueError, match="registry"):
        write_sa_authfile(tmp_path / "config.json", "")


def test_write_sa_authfile_overwrites_atomically(tmp_path: Path, fake_token) -> None:
    target = tmp_path / "config.json"
    target.write_text("stale")
    credentials_module.write_sa_authfile(target, "registry.example")
    assert "serviceaccount:tok-123" in base64.b64decode(target.read_text().split('"auth": "')[1].split('"')[0]).decode()


def test_write_sa_authfile_fchmod_failure_closes_fd_and_cleans_up(tmp_path: Path, fake_token, monkeypatch) -> None:
    target = tmp_path / "config.json"

    real_mkstemp = credentials_module.tempfile.mkstemp
    captured = {}

    def mkstemp(**kwargs):
        fd, name = real_mkstemp(**kwargs)
        captured["fd"] = fd
        captured["name"] = name
        return fd, name

    monkeypatch.setattr(credentials_module.tempfile, "mkstemp", mkstemp)

    def broken_fchmod(*args):
        raise OSError("fchmod failed")

    monkeypatch.setattr(credentials_module.os, "fchmod", broken_fchmod)

    with pytest.raises(OSError, match="fchmod failed"):
        credentials_module.write_sa_authfile(target, "registry.example")

    assert not Path(captured["name"]).exists()  # temp file removed
    with pytest.raises(OSError):  # fd is closed
        os.fstat(captured["fd"])


def test_extra_args_no_token_emits_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(credentials_module, "SA_TOKEN_PATH", tmp_path / "absent")
    skopeo = Skopeo(sa=SaCredentials(authfile=tmp_path / ".docker" / "config.json", registry="registry.example"))
    assert skopeo._extra_args == {}


def test_extra_args_explicit_authfile_is_never_touched(tmp_path: Path, fake_token) -> None:
    authfile = tmp_path / "auth.json"
    authfile.write_text("user-provided")
    skopeo = Skopeo(authfile=authfile)
    assert skopeo._extra_args["params"] == ["--authfile", str(authfile)]
    assert authfile.read_text() == "user-provided"


def test_extra_args_sa_mode_builds_authfile_at_target(tmp_path: Path, fake_token) -> None:
    docker_dir = tmp_path / ".docker"
    docker_dir.mkdir()
    target = docker_dir / "config.json"
    skopeo = Skopeo(sa=SaCredentials(authfile=target, registry="registry.example"))

    args = skopeo._extra_args

    assert args == {"params": ["--authfile", str(target)]}
    assert b"serviceaccount:tok-123" in base64.b64decode(target.read_text().split('"auth": "')[1].split('"')[0])
    # temp file lived in the target's parent dir (same-filesystem replace)
    assert list(docker_dir.iterdir()) == [target]


def test_extra_args_sa_mode_registry_missing_raises(tmp_path: Path, fake_token) -> None:
    skopeo = Skopeo(sa=SaCredentials(authfile=tmp_path / "config.json", registry=""))
    with pytest.raises(ValueError, match="registry"):
        _ = skopeo._extra_args


def test_push_retries_once_with_fresh_authfile(tmp_path: Path, fake_token, monkeypatch) -> None:
    target = tmp_path / ".docker" / "config.json"
    target.parent.mkdir()
    skopeo = Skopeo(sa=SaCredentials(authfile=target, registry="registry.example"))
    image = _make_image(tmp_path, monkeypatch, skopeo)

    calls = []
    contents = []

    def run(command, **kwargs):
        calls.append(command)
        # the authfile was rebuilt from the live token before this push
        contents.append(target.read_text())
        if len(calls) == 1:
            fake_token.write_text("tok-456")
            raise subprocess.CalledProcessError(1, command, stderr="unauthorized: authentication required")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(image_module.subprocess, "run", run)
    monkeypatch.setattr(image_module, "skopeo_inspect", lambda **kwargs: '{"kind": "manifest"}')

    image.push()

    assert len(calls) == 2
    assert any("--authfile" in part for part in calls[1])
    first_auth = base64.b64decode(contents[0].split('"auth": "')[1].split('"')[0]).decode()
    second_auth = base64.b64decode(contents[1].split('"auth": "')[1].split('"')[0]).decode()
    assert first_auth == "serviceaccount:tok-123"
    assert second_auth == "serviceaccount:tok-456"
    assert contents[0] != contents[1]


def test_push_no_retry_with_explicit_authfile(tmp_path: Path, fake_token, monkeypatch) -> None:
    authfile = tmp_path / "auth.json"
    authfile.write_text("user")
    skopeo = Skopeo(authfile=authfile)
    image = _make_image(tmp_path, monkeypatch, skopeo)

    calls = []

    def run(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command, stderr="unauthorized: authentication required")

    monkeypatch.setattr(image_module.subprocess, "run", run)

    with pytest.raises(RuntimeError, match="Failed to push"):
        image.push()

    assert len(calls) == 1


def test_push_no_retry_on_non_auth_failure(tmp_path: Path, fake_token, monkeypatch) -> None:
    skopeo = Skopeo(sa=SaCredentials(authfile=tmp_path / "config.json", registry="registry.example"))
    image = _make_image(tmp_path, monkeypatch, skopeo)

    calls = []

    def run(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command, stderr="manifest unknown")

    monkeypatch.setattr(image_module.subprocess, "run", run)

    with pytest.raises(RuntimeError, match="manifest unknown"):
        image.push()

    assert len(calls) == 1


def test_push_success_failure_message_includes_stderr(tmp_path: Path, monkeypatch) -> None:
    skopeo = Skopeo()
    image = _make_image(tmp_path, monkeypatch, skopeo)

    def run(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr="boom")

    monkeypatch.setattr(image_module.subprocess, "run", run)

    with pytest.raises(RuntimeError, match="boom"):
        skopeo.push(src=Path("src"), dest="registry.example/repository:latest")
