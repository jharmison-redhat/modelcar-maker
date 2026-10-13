import base64
import os
import stat
import subprocess
from pathlib import Path

import pytest

import modelcar_maker.image.image as image_module
from modelcar_maker.image import BaseImage
from modelcar_maker.image import ModelcarImage
from modelcar_maker.image import Skopeo
from modelcar_maker.model import Model


def make_base(path: Path) -> BaseImage:
    return BaseImage(tagged_image="registry.example/repository/base:latest", skopeo=Skopeo(), update=True, path=path)


@pytest.fixture
def fake_token(tmp_path: Path, monkeypatch):
    token_path = tmp_path / "token"
    token_path.write_text("tok-123")
    monkeypatch.setattr(image_module, "_SA_TOKEN_PATH", token_path)
    return token_path


def test_write_sa_authfile_scheme_mode_and_atomicity(tmp_path: Path, fake_token) -> None:
    target = tmp_path / "config.json"
    image_module._write_sa_authfile(target, "registry.example:5000")

    contents = target.read_text()
    expected_auth = base64.b64encode(b"serviceaccount:tok-123").decode()
    assert contents == '{"auths": {"registry.example:5000": {"auth": "%s"}}}' % expected_auth
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("tmp*"))  # temp file is gone after replace


def test_write_sa_authfile_requires_registry(tmp_path: Path, fake_token) -> None:
    with pytest.raises(ValueError, match="registry"):
        image_module._write_sa_authfile(tmp_path / "config.json", "")


def test_write_sa_authfile_overwrites_atomically(tmp_path: Path, fake_token) -> None:
    target = tmp_path / "config.json"
    target.write_text("stale")
    image_module._write_sa_authfile(target, "registry.example")
    assert "serviceaccount:tok-123" in base64.b64decode(target.read_text().split('"auth": "')[1].split('"')[0]).decode()


def test_extra_args_no_token_emits_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(image_module, "_SA_TOKEN_PATH", tmp_path / "absent")
    skopeo = Skopeo(sa_authfile=tmp_path / ".docker" / "config.json", registry="registry.example")
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
    skopeo = Skopeo(sa_authfile=target, registry="registry.example")

    args = skopeo._extra_args

    assert args == {"params": ["--authfile", str(target)]}
    assert b"serviceaccount:tok-123" in base64.b64decode(
        target.read_text().split('"auth": "')[1].split('"')[0]
    )
    # temp file lived in the target's parent dir (same-filesystem replace)
    assert list(docker_dir.iterdir()) == [target]


def test_extra_args_sa_mode_registry_missing_raises(tmp_path: Path, fake_token) -> None:
    skopeo = Skopeo(sa_authfile=tmp_path / "config.json")
    with pytest.raises(ValueError, match="registry"):
        _ = skopeo._extra_args


def _make_image(tmp_path: Path, monkeypatch, skopeo: Skopeo) -> ModelcarImage:
    monkeypatch.chdir(tmp_path)
    return ModelcarImage(
        base_image=make_base(tmp_path / "base"),
        model=Model(repo_id="org/model"),
        skopeo=skopeo,
        registry="registry.example",
        repository="repository",
    )


def test_push_retries_once_with_fresh_authfile(tmp_path: Path, fake_token, monkeypatch) -> None:
    target = tmp_path / ".docker" / "config.json"
    target.parent.mkdir()
    skopeo = Skopeo(sa_authfile=target, registry="registry.example")
    image = _make_image(tmp_path, monkeypatch, skopeo)

    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, command, stderr="unauthorized: authentication required")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(image_module.subprocess, "run", run)
    monkeypatch.setattr(image_module, "skopeo_inspect", lambda **kwargs: '{"kind": "manifest"}')

    image.push()

    assert len(calls) == 2
    assert any("--authfile" in part for part in calls[1])


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
    skopeo = Skopeo(sa_authfile=tmp_path / "config.json", registry="registry.example")
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
