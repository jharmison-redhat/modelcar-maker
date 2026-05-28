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
    build_args = ["-t", image]
    if args.pull:
        build_args.insert(0, "--pull=newer")

    if len(args.architectures) == 1:
        # Single-arch build
        arch = args.architectures[0]
        logger.info(f"Podman building single-arch image {image} for linux/{arch}")
        build_args = ["--platform", f"linux/{arch}"] + build_args
        podman(
            "build",
            args=build_args,
            context_dir=args.model_dir,
            printer=rprint,
        )
        return BuildResult(image=image)
    else:
        # Multi-arch build using manifest list
        manifest_list_name = f"{normalize(args.model)}-modelcar-manifest"
        logger.info(
            f"Podman building multi-arch manifest list {manifest_list_name} "
            f"for platforms: {', '.join(args.architectures)}"
        )
        for arch in args.architectures:
            logger.debug(f"Podman building arch {arch} with --manifest {manifest_list_name}")
            arch_args = [
                "--platform",
                f"linux/{arch}",
                "--manifest",
                manifest_list_name,
            ] + build_args
            podman(
                "build",
                args=arch_args,
                context_dir=args.model_dir,
                printer=rprint,
            )
        return BuildResult(image=image, manifest_list=manifest_list_name)


def do_push(args: PushArgs) -> None:
    """Perform a podman push of the image that matches the given model,
    image registry repo, and optionally use the specified authfile."""
    image = _image(args.model, args.repo)
    cmd_args = list()
    if args.authfile is not None:
        cmd_args.extend(["--authfile", str(args.authfile)])

    if args.manifest_list is not None:
        logger.info(f"Podman pushing manifest list {args.manifest_list} to docker://{image}")
        cmd_args.extend([args.manifest_list, f"docker://{image}"])
        podman("manifest", args=["push"] + cmd_args)
    else:
        logger.info(f"Podman pushing single-arch image {image}")
        cmd_args.append(image)
        podman("push", args=cmd_args)


def do_image_rm(args: RmArgs) -> bool:
    """Remove the image for the given model and image registry repo from the local podman image
    cache. Returns True when successful, False when failed - often because the image doesn't exist."""
    removed = False
    # Remove the main image tag
    try:
        podman(
            "image",
            args=[
                "rm",
                _image(args.model, args.repo),
            ],
            printer=logger.info,
        )
        removed = True
    except Exception:
        pass

    # For multi-arch builds, also remove per-arch images and the manifest list
    if args.manifest_list is not None:
        try:
            podman(
                "manifest",
                args=["rm", args.manifest_list],
                printer=logger.info,
            )
            removed = True
        except Exception:
            pass
        # Remove per-arch images
        for arch in args.architectures:
            try:
                podman(
                    "image",
                    args=["rm", f"{_image(args.model, args.repo)}-{arch}"],
                    printer=logger.info,
                )
                removed = True
            except Exception:
                pass

    return removed


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
