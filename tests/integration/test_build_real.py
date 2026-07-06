"""
End-to-end integration test that builds a real modelcar image using the CLI,
for both podman and olot backends.
"""

import json
import platform as _platform
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from modelcar_maker.cli.cli import cli
from modelcar_maker.image.common import list_model_files
from modelcar_maker.image.types import Backend
from modelcar_maker.util import normalize

runner = CliRunner()


def _host_platform() -> tuple[str, str]:
    """Return (platform string, arch), e.g. ('linux/amd64', 'amd64')."""
    machine = _platform.machine()
    if machine in ("x86_64", "amd64"):
        return "linux/amd64", "amd64"
    if machine in ("aarch64", "arm64"):
        return "linux/arm64", "arm64"
    return f"linux/{machine}", machine


_HOST_PLATFORM, _HOST_ARCH = _host_platform()

MODEL = "prajjwal1/bert-tiny"
NORMALIZED = normalize(MODEL)
EXPECTED_FILES = [
    "README.md",
    "config.json",
    "pytorch_model.bin",
    "vocab.txt",
]
EXPECTED_MODELCARD = "README.md"
IMAGE_TAG = f"localhost/modelcar-maker-integration-test:{NORMALIZED}-modelcar"
PODMAN_BUILD_DIR = Path("models") / NORMALIZED
OLOT_LAYOUT_DIR = Path("tmp") / NORMALIZED


def _which(tool: str) -> bool:
    return shutil.which(tool) is not None


def _run(cmd: list[str], check: bool = True) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)!r}\n" f"stdout: {result.stdout}\n" f"stderr: {result.stderr}"
        )
    return result.stdout


@pytest.mark.slow
@pytest.mark.parametrize("backend", [Backend.PODMAN, Backend.OLOT])
def test_build_real_model_no_push(backend):
    """Build a real tiny model with the CLI, inspect the image, and clean up."""
    if not _which("podman"):
        pytest.skip("podman is required for image verification")
    if backend == Backend.OLOT and not _which("skopeo"):
        pytest.skip("skopeo is required for olot backend verification")

    # Pre-clean stale artifacts from previous failed runs
    if backend == Backend.PODMAN:
        stale_containerfile = PODMAN_BUILD_DIR / "Containerfile"
        if stale_containerfile.exists():
            stale_containerfile.unlink()
    else:
        if OLOT_LAYOUT_DIR.exists():
            shutil.rmtree(OLOT_LAYOUT_DIR, ignore_errors=True)

    args = [
        MODEL,
        "--no-push",
        "--no-skip-if-exists",
        "--backend",
        str(backend),
        "--registry",
        "localhost",
        "--repository",
        "modelcar-maker-integration-test",
    ]
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output

    # 1. Assert model was downloaded
    assert PODMAN_BUILD_DIR.is_dir(), f"Expected model dir {PODMAN_BUILD_DIR} to exist"
    for fname in EXPECTED_FILES:
        assert (PODMAN_BUILD_DIR / fname).exists(), f"Expected file {fname} in model dir"

    # Verify list_model_files matches hardcoded expectations
    files, modelcard = list_model_files(PODMAN_BUILD_DIR)
    assert files == EXPECTED_FILES
    assert modelcard == EXPECTED_MODELCARD

    # 2. Load / verify image
    if backend == Backend.OLOT:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tar_path = Path(tmp) / "modelcar.tar"
            _run(
                [
                    "skopeo",
                    "copy",
                    "--override-os",
                    "linux",
                    "--override-arch",
                    _HOST_ARCH,
                    f"oci:{OLOT_LAYOUT_DIR.resolve()}",
                    f"docker-archive:{tar_path}:{IMAGE_TAG}",
                ]
            )
            _run(["podman", "load", "-i", str(tar_path)])

    try:
        # Verify model files are present in the image at /models
        ls_output = _run(
            [
                "podman",
                "run",
                "--platform",
                _HOST_PLATFORM,
                "--rm",
                "--pull=never",
                "--entrypoint",
                "/bin/sh",
                IMAGE_TAG,
                "-c",
                "ls -1 /models",
            ]
        )
        ls_lines = set(ls_output.strip().splitlines())
        for fname in EXPECTED_FILES:
            assert fname in ls_lines, f"Expected {fname} in /models, got {ls_lines}"

        # Verify modelcard exists at the correct path for this backend
        modelcard_path = "/modelcard.md" if backend == Backend.PODMAN else "/models/README.md"
        _run(
            [
                "podman",
                "run",
                "--platform",
                _HOST_PLATFORM,
                "--rm",
                "--pull=never",
                "--entrypoint",
                "/bin/sh",
                IMAGE_TAG,
                "-c",
                f"test -f {modelcard_path}",
            ]
        )

        # Verify labels via podman image inspect
        inspect_output = _run(["podman", "image", "inspect", IMAGE_TAG])
        inspect_data = json.loads(inspect_output.strip())
        labels = inspect_data[0].get("Labels", {})

        assert labels.get("model.name") == MODEL
        assert labels.get("model.commit"), "model.commit label should be non-empty"
        assert labels.get("io.k8s.display-name") == f"{NORMALIZED}-modelcar"

    finally:
        # 3. Clean up image from containers-storage
        _run(["podman", "image", "rm", IMAGE_TAG], check=False)

        # 4. Clean up backend-specific artifacts
        if backend == Backend.PODMAN:
            containerfile = PODMAN_BUILD_DIR / "Containerfile"
            if containerfile.exists():
                containerfile.unlink()
        else:
            if OLOT_LAYOUT_DIR.exists():
                shutil.rmtree(OLOT_LAYOUT_DIR, ignore_errors=True)
