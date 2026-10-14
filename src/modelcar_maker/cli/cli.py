import json
import signal
import sys
import time
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
from typing import Optional

import typer
from rich import print as rprint

from ..image import BaseImage
from ..image import ModelcarImage
from ..image import SaCredentials
from ..image import Skopeo
from ..model import Model
from ..util import make_logger
from ..util import settings


def version_callback(value: bool):
    if value:
        from ..__version__ import version

        rprint(version)
        raise typer.Exit()


@dataclass
class ModelResult:
    repo_id: str
    status: str
    duration_seconds: float
    error: str | None = None
    destination_image: str | None = None
    destination_digest: str | None = None
    download: dict[str, int] | None = None
    build: dict[str, int | None] | None = None
    push: dict[str, str | None] | None = None


def _requested_cleanup(
    image: Optional[ModelcarImage],
    model: Optional[Model],
    image_cleanup: bool,
    model_cleanup: bool,
) -> list[str]:
    """Attempt requested cleanups independently and return their errors."""
    errors: list[str] = []
    if image_cleanup and image is not None:
        try:
            image.cleanup()
        except Exception as error:
            errors.append(f"image cleanup failed: {error}")
    if model_cleanup and model is not None:
        try:
            model.cleanup()
        except Exception as error:
            errors.append(f"model cleanup failed: {error}")
    return errors


def _emit(message: str, enabled: bool) -> None:
    if enabled:
        rprint(message)


cli = typer.Typer(
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_show_locals=False,
    add_completion=False,
)


@cli.command()
def build(
    ctx: typer.Context,
    repo_id: Annotated[
        Optional[str],
        typer.Argument(
            help="The Hugging Face repository ID to download (otherwise ensure all defaults are downloaded)"
        ),
    ] = None,
    files: Annotated[
        list[str],
        typer.Option(
            "-f",
            "--files",
            help="Specific files from the Hugging Face repository to grab, instead of all",
        ),
    ] = list(),
    registry: Annotated[
        str,
        typer.Option(
            "--registry",
            help="The registry to include in the image reference (and push to)",
        ),
    ] = settings.image.registry,
    repository: Annotated[
        str,
        typer.Option(
            "-r",
            "--repository",
            help="The repository, within the registry, to include in the image reference (and push to)",
        ),
    ] = settings.image.repository,
    tag: Annotated[
        Optional[str],
        typer.Option(
            "-t",
            "--tag",
            help="The explicit tag to set, instead of auto-detecting per model",
        ),
    ] = None,
    base_image: Annotated[
        str,
        typer.Option(
            "--base-image",
            help="Base image to use for the modelcar (needs to have a shell for KServe)",
        ),
    ] = settings.image.base_image,
    pull: Annotated[
        bool,
        typer.Option(
            "--pull/--no-pull",
            help="Pull the base image before building if a newer version is available",
        ),
    ] = settings.image.pull,
    push: Annotated[
        bool,
        typer.Option(
            "--push/--no-push",
            help="Push the image(s) after building",
        ),
    ] = settings.image.push,
    authfile: Annotated[
        Optional[Path],
        typer.Option(
            "-a",
            "--authfile",
            help="The authfile to use for the base pull and modelcar push (if not provided, uses skopeo default behavior)",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
        ),
    ] = settings.image.get("authfile"),
    service_account_auth: Annotated[
        bool,
        typer.Option(
            "--service-account-auth/--no-service-account-auth",
            help=(
                "Construct a registry authfile from the projected ServiceAccount token before each registry "
                "operation (modelcar-maker owns the docker config at ~/.docker/config.json, overriding any "
                "pre-existing one). Mutually exclusive with --authfile."
            ),
        ),
    ] = settings.image.get("service_account_auth", False),
    image_cleanup: Annotated[
        bool,
        typer.Option(
            "--image-clean-up/--no-image-clean-up",
            help="Clean up the container image after push (in between models)",
        ),
    ] = settings.image.cleanup,
    model_cleanup: Annotated[
        bool,
        typer.Option(
            "--model-clean-up/--no-model-clean-up",
            help="Clean up the downloaded model images after build (in between models)",
        ),
    ] = settings.models.cleanup,
    skip_if_exists: Annotated[
        bool,
        typer.Option(
            "--skip-if-exists/--no-skip-if-exists",
            help="Skip downloading, building, and pushing the image if it already exists at the remote",
        ),
    ] = settings.image.skip_if_exists,
    output: Annotated[str, typer.Option("--output", help="Output format: text or json")] = "text",
    quiet: Annotated[bool, typer.Option("--quiet", help="Suppress text progress and success messages")] = False,
    fail_fast: Annotated[bool, typer.Option("--fail-fast", help="Stop after the first model failure")] = False,
    verbose: Annotated[
        int,
        typer.Option(
            "--verbose",
            "-v",
            count=True,
            help="Increase logging verbosity (repeat for more)",
            show_default=False,
            metavar="",
        ),
    ] = settings.meta.verbosity,
    _: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=version_callback,
            help="Print the version and exit",
            is_eager=True,
            show_default=False,
        ),
    ] = False,
):
    """Modelcar Maker: download models, build KServe Modelcar images,
    and push them with consistent names and metadata."""

    if output not in {"text", "json"}:
        raise typer.BadParameter("must be text or json", param_hint="--output")
    logger = make_logger(verbose)

    def _on_sigterm(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _on_sigterm)
    text_output = output == "text" and not quiet
    started = time.monotonic()
    results: list[ModelResult] = []
    image_repo = f"{registry}/{repository}"
    logger.debug(f"Push {image_repo}: {push}")
    logger.debug(f"Specified repo_id: {repo_id}")
    try:
        if repo_id is None:
            if not isinstance(settings.models.default, list):
                raise ValueError(f"Default models should be a list, not {type(settings.models.default)}")
            if len(settings.models.default) < 1:
                raise ValueError("No repo_id provided, default models list is empty")
            models = settings.models.default
            if tag is not None and len(models) > 1:
                raise ValueError(f"Specifying a single tag ({tag}) with multiple models ({models}) is invalid")
        else:
            models = [repo_id]
    except Exception as error:
        if output == "json":
            typer.echo(
                json.dumps(
                    {
                        "status": "failed",
                        "duration_seconds": time.monotonic() - started,
                        "results": [],
                        "error": str(error),
                    }
                )
            )
            raise typer.Exit(1)
        raise typer.BadParameter(str(error)) from error

    if service_account_auth and authfile is not None:
        raise typer.BadParameter(
            "--service-account-auth and --authfile are mutually exclusive", param_hint="--service-account-auth"
        )
    skopeo = Skopeo(
        authfile=authfile,
        sa=(
            SaCredentials(authfile=Path.home() / ".docker" / "config.json", registry=registry)
            if service_account_auth
            else None
        ),
    )
    base = BaseImage(
        skopeo=skopeo,
        tagged_image=base_image,
        update=pull,
    )
    base_prepared = False
    operation_error: str | None = None

    logger.info(f"Processing the following model repo_id list: {models}")
    for repo_id in models:
        model_started = time.monotonic()
        model: Optional[Model] = None
        image: Optional[ModelcarImage] = None
        result_status = "success"
        primary_error: Optional[Exception] = None
        download: dict[str, int] | None = None
        build_stats: dict[str, int | None] | None = None
        digest: str | None = None
        pushed_digest: str | None = None
        try:
            _emit(f"Processing {repo_id}", text_output)
            model_files = files if files else settings.models.get(repo_id, {}).get("files", [])
            model = Model(repo_id=repo_id, files=model_files)
            modelcar_tag = tag if tag is not None else settings.models.get(repo_id, {}).get("tag")
            image = ModelcarImage(
                base_image=base, skopeo=skopeo, model=model, registry=registry, repository=repository, tag=modelcar_tag
            )
            if skip_if_exists and image.exists_remote():
                _emit(f"Skipping build of {image.tagged_image} as it exists in the registry", text_output)
                result_status = "skipped"
            else:
                if not base_prepared:
                    _emit(f"Preparing base image cache for {base_image} at {base.path}", text_output)
                    try:
                        base.pull()
                    except Exception as error:
                        operation_error = f"Failed to prepare shared base image cache for {base_image}: {error}"
                        raise RuntimeError(operation_error) from error
                    base_prepared = True
                _emit(f"Downloading {repo_id} to {model.path}", text_output)
                download = model.download(progress=sys.stderr.isatty() and output == "text" and not quiet)
                _emit(f"Building {image.tagged_image} in {image.layout_dir}", text_output)
                build_stats = image.build()
                digest = image.digest()
                if push:
                    _emit(f"Pushing {image.tagged_image}", text_output)
                    pushed_digest = image.push()
        except Exception as error:
            primary_error = error
            result_status = "failed"
            logger.error("Failed processing %s: %s", repo_id, error)
        finally:
            cleanup_errors = _requested_cleanup(image, model, image_cleanup, model_cleanup)
        for cleanup_error in cleanup_errors:
            logger.error("Failed processing %s: %s", repo_id, cleanup_error)
        if primary_error is None and cleanup_errors:
            result_status = "failed"
        results.append(
            ModelResult(
                repo_id=repo_id,
                status=result_status,
                duration_seconds=time.monotonic() - model_started,
                error=str(primary_error) if primary_error is not None else "; ".join(cleanup_errors) or None,
                destination_image=image.tagged_image if image else None,
                destination_digest=digest,
                download=download,
                build=build_stats,
                push={"digest": pushed_digest} if push and pushed_digest is not None else None,
            )
        )
        if operation_error is not None or (result_status == "failed" and fail_fast):
            break
    failed = sum(result.status == "failed" for result in results)
    succeeded = sum(result.status == "success" for result in results)
    status = "partial" if failed and succeeded else "failed" if failed else "success"
    if output == "json":
        typer.echo(
            json.dumps(
                {
                    "status": status,
                    "duration_seconds": time.monotonic() - started,
                    "results": [asdict(result) for result in results],
                    "error": operation_error,
                }
            )
        )
    elif not quiet and not failed:
        rprint("Done! 🎉")
    if failed:
        raise typer.Exit(1)
