import subprocess
from pathlib import Path
from typing import Any
from typing import Generator

from rich import print

from ..util import logger
from ..util import normalize


def _utf8ify(line_bytes: bytes | str | None = None) -> str:
    """Decode line_bytes as utf-8 and strips excess whitespace."""
    if line_bytes is not None:
        if isinstance(line_bytes, bytes):
            return line_bytes.decode("utf-8").rstrip()
        else:
            return line_bytes.rstrip()
    else:
        return ""


def podman(
    command: str = "build",
    context_dir: Path | None = None,
    args: list[Any] = [],
) -> Generator[str, Any, Any]:
    argv = [
        "podman",
        command,
    ]
    if command == "build":
        argv.append(".")

    argv.extend(args)
    kwargs: dict[str, Any] = dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    if context_dir is not None:
        kwargs["cwd"] = context_dir

    logger.debug(f"Executing: {argv}")
    proc = subprocess.Popen(
        argv,
        **kwargs,
    )
    assert proc.stdout is not None
    for line in map(_utf8ify, iter(proc.stdout.readline, b"")):
        yield line

    ret = proc.wait()
    if ret != 0:
        raise RuntimeError(f"{command} failed with code {ret}")


def _image(model: str, repo: str) -> str:
    tag = normalize(model) + "-modelcar"
    return f"{repo}:{tag}"


def build(model: str, repo: str, model_dir: Path) -> None:
    for line in podman(
        "build",
        args=[
            "-t",
            _image(model, repo),
        ],
        context_dir=model_dir,
    ):
        print(line)


def push(model: str, repo: str, authfile: Path | None) -> None:
    args = list()
    if authfile is not None:
        args.extend(["--authfile", str(authfile)])
    args.append(_image(model, repo))
    for line in podman("push", args=args):
        print(line)
