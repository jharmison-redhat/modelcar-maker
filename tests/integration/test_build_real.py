"""
End-to-end integration test that builds a real modelcar image using the CLI.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterator

import pytest
from typer.testing import CliRunner

from modelcar_maker.cli.cli import cli
from modelcar_maker.image.common import list_model_files
from modelcar_maker.image.types import Backend
from modelcar_maker.util import normalize

runner = CliRunner()

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
MODEL_DIR = Path("models") / NORMALIZED
OLOT_LAYOUT_DIR = Path("tmp") / f"{NORMALIZED}-modelcar"


def _which(tool: str) -> bool:
    return shutil.which(tool) is not None


def _run(cmd: list[str], check: bool = True) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)!r}\n" f"stdout: {result.stdout}\n" f"stderr: {result.stderr}"
        )
    return result.stdout


def _host_platform() -> str:
    """Return platform string for the host machine (e.g., 'linux/amd64').

    uname -m reflects the actual kernel architecture, which is more
    reliable than platform.machine() under QEMU emulation in CI.
    """
    uname_machine = subprocess.run(["uname", "-m"], capture_output=True, text=True, check=True).stdout.strip()
    arch = "amd64" if uname_machine in ("x86_64", "amd64") else uname_machine
    return f"linux/{arch}"


def _verify_image(image_tag: str, platform: str, modelcard_path: str, model_name: str, normalized: str) -> None:
    """Common image verification: model contents, modelcard path, and labels."""
    ls_output = _run(
        [
            "podman",
            "run",
            "--platform",
            platform,
            "--rm",
            "--pull=never",
            "--entrypoint",
            "/bin/sh",
            image_tag,
            "-c",
            "ls -1 /models",
        ]
    )
    ls_lines = set(ls_output.strip().splitlines())
    for fname in EXPECTED_FILES:
        assert fname in ls_lines, f"Expected {fname} in /models, got {ls_lines}"

    _run(
        [
            "podman",
            "run",
            "--platform",
            platform,
            "--rm",
            "--pull=never",
            "--entrypoint",
            "/bin/sh",
            image_tag,
            "-c",
            f"test -f {modelcard_path}",
        ]
    )

    inspect_output = _run(["podman", "image", "inspect", image_tag])
    inspect_data = json.loads(inspect_output.strip())
    labels = inspect_data[0].get("Labels", {})

    assert labels.get("model.name") == model_name
    assert labels.get("model.commit"), "model.commit label should be non-empty"
    assert labels.get("io.k8s.display-name") == f"{normalized}-modelcar"


@pytest.fixture
def build_args() -> list[str]:
    return [
        MODEL,
        "--no-push",
        "--no-skip-if-exists",
        "--registry",
        "localhost",
        "--repository",
        "modelcar-maker-integration-test",
    ]


@pytest.fixture(autouse=True)
def cleanup() -> Iterator[None]:
    """Ensure image and artifacts are cleaned up after each test."""
    yield
    _run(["podman", "image", "rm", IMAGE_TAG], check=False)

    if OLOT_LAYOUT_DIR.exists():
        shutil.rmtree(OLOT_LAYOUT_DIR, ignore_errors=True)


@pytest.mark.slow
def test_build_real_model_no_push_olot(build_args: list[str]) -> None:
    """Build a real tiny model with olot backend, verify the image, and clean up."""
    if not _which("podman"):
        pytest.skip("podman is required for image verification")
    if not _which("skopeo"):
        pytest.skip("skopeo is required for olot backend verification")

    if OLOT_LAYOUT_DIR.exists():
        shutil.rmtree(OLOT_LAYOUT_DIR, ignore_errors=True)

    result = runner.invoke(cli, build_args + ["--backend", str(Backend.OLOT)])
    assert result.exit_code == 0, result.output

    # 1. Assert model was downloaded
    assert MODEL_DIR.is_dir(), f"Expected model dir {MODEL_DIR} to exist"
    for fname in EXPECTED_FILES:
        assert (MODEL_DIR / fname).exists(), f"Expected file {fname} in model dir"

    # Verify list_model_files matches hardcoded expectations
    files, modelcard = list_model_files(MODEL_DIR)
    assert files == EXPECTED_FILES
    assert modelcard == EXPECTED_MODELCARD

    # 2. Convert OCI layout to docker-archive via skopeo, then load into podman
    host_plat = _host_platform()
    host_arch = host_plat.split("/", 1)[1]

    with tempfile.TemporaryDirectory() as tmp:
        tar_path = Path(tmp) / "modelcar.tar"
        _run(
            [
                "skopeo",
                "copy",
                "--override-os",
                "linux",
                "--override-arch",
                host_arch,
                f"oci:{OLOT_LAYOUT_DIR.resolve()}",
                f"docker-archive:{tar_path}:{IMAGE_TAG}",
            ]
        )
        _run(["podman", "load", "-i", str(tar_path)])

    # 3. Verify image — skopeo produced host-arch image
    _verify_image(IMAGE_TAG, host_plat, "/models/README.md", MODEL, NORMALIZED)
