import json
import shlex
import subprocess
from pathlib import Path
from typing import Any
from typing import Callable

from rich import print as rprint

from ..util import logger
from ..util import normalize
from .common import _image
from .template import render
from .types import BuildArgs
from .types import BuildResult
from .types import PushArgs
from .types import RmArgs


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
    printer: Callable = print,
) -> str:
    """Performs generic podman commands with flexible handling of the output."""
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

    printer(f"+ {shlex.join(argv)}")
    proc = subprocess.Popen(
        argv,
        **kwargs,
    )
    assert proc.stdout is not None
    output = []
    for line in map(_utf8ify, iter(proc.stdout.readline, b"")):
        printer(line)
        output.append(line)

    ret = proc.wait()
    if ret != 0:
        raise RuntimeError(f"{shlex.join(argv)} -- failed with code {ret}")
    return "\n".join(output)


def do_build(args: BuildArgs) -> BuildResult:
    """Perform a podman build of the given model, for the given
    image registry repo, from the files in the model_dir."""
    image = _image(args.model, args.repo)
    render(args.model, args.model_dir, args.commit, args.base_image)
    podman(
        "build",
        args=[
            "-t",
            image,
        ],
        context_dir=args.model_dir,
        printer=rprint,
    )
    return BuildResult(image=image)


def do_push(args: PushArgs) -> None:
    """Perform a podman push of the image that matches the given model,
    image registry repo, and optionally use the specified authfile."""
    cmd_args = list()
    if args.authfile is not None:
        cmd_args.extend(["--authfile", str(args.authfile)])
    cmd_args.append(_image(args.model, args.repo))
    podman("push", args=cmd_args)


def do_image_rm(args: RmArgs) -> bool:
    """Remove the image for the given model and image registry repo from the local podman image
    cache. Returns True when successful, False when failed - often because the image doesn't exist."""
    try:
        podman(
            "image",
            args=[
                "rm",
                _image(args.model, args.repo),
            ],
            printer=logger.info,
        )
        return True
    except Exception:
        return False


def image_exists(model: str, repo: str) -> bool:
    """Return whether the image for the given model and
    image registry repo exists at the remote repository."""
    raw_json = podman(
        "search",
        args=[repo, "--list-tags", "--format", "json", "--limit", "1000"],
        printer=logger.debug,
    )
    model_tag = normalize(model) + "-modelcar"
    tags = json.loads(raw_json)[0].get("Tags", [])
    return model_tag in tags
