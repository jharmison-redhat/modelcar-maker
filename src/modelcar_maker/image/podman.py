import json
import shlex
import subprocess
from pathlib import Path
from typing import Any
from typing import Callable

from rich import print as rprint

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
    printer: Callable = print,
) -> str:
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
        raise RuntimeError(f"{command} failed with code {ret}")
    return "\n".join(output)


def _image(model: str, repo: str) -> str:
    tag = normalize(model) + "-modelcar"
    return f"{repo}:{tag}"


def do_build(model: str, repo: str, model_dir: Path) -> str:
    image = _image(model, repo)
    podman(
        "build",
        args=[
            "-t",
            image,
        ],
        context_dir=model_dir,
        printer=rprint,
    )
    return image


def do_push(model: str, repo: str, authfile: Path | None) -> None:
    args = list()
    if authfile is not None:
        args.extend(["--authfile", str(authfile)])
    args.append(_image(model, repo))
    podman("push", args=args)


def do_image_rm(model: str, repo: str) -> None:
    podman(
        "image",
        args=[
            "rm",
            _image(model, repo),
        ],
    )


def image_exists(model: str, repo: str) -> bool:
    raw_json = podman(
        "search",
        args=[repo, "--list-tags", "--format", "json", "--limit", "1000"],
        printer=logger.debug,
    )
    model_tag = normalize(model) + "-modelcar"
    tags = json.loads(raw_json)[0].get("Tags", [])
    return model_tag in tags
