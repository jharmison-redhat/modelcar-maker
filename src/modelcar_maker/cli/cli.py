from pathlib import Path
from typing import Optional

import typer
from pydantic import BaseModel
from rich import print as rprint
from typing_extensions import Annotated

from ..image import BaseImage
from ..image import ModelcarImage
from ..image import Skopeo
from ..model import Model
from ..util import make_logger
from ..util import settings


def version_callback(value: bool):
    if value:
        from ..__version__ import version

        rprint(version)
        raise typer.Exit()


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
        ),
    ] = settings.image.get("authfile"),
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

    logger = make_logger(verbose)
    image_repo = f"{registry}/{repository}"
    logger.debug(f"Push {image_repo}: {push}")
    logger.debug(f"Specified repo_id: {repo_id}")
    if repo_id is None:
        if not isinstance(settings.models.default, list):
            ctx.fail(f"Default models should be a list, not {type(settings.models.default)}")
        if len(settings.models.default) < 1:
            ctx.fail("No repo_id provided, default models list is empty")
        models = settings.models.default
        if tag is not None and len(models) > 1:
            ctx.fail(f"Specifying a single tag ({tag}) with multiple models ({models}) is invalid")
    else:
        models = [repo_id]

    logger.info(f"Processing the following model repo_id list: {models}")
    for repo_id in models:
        rprint(f"Processing {repo_id}")
        if len(files) == 0:
            files = settings.models.get(repo_id, {}).get("files", [])
            logger.debug(f"No files specified on command line, using {files} from config")
        model = Model(repo_id=repo_id, files=files)
        if tag is None:
            tag = settings.models.get(repo_id, {}).get("tag")
            if tag is not None:
                logger.debug(f"No tag specified on command line, using {tag} from config")
            else:
                logger.debug("Using normalized repo_id for tag")
        skopeo = Skopeo(
            authfile=authfile,
        )
        base = BaseImage(
            skopeo=skopeo,
            tagged_image=base_image,
            update=pull,
        )
        image = ModelcarImage(
            base_image=base,
            skopeo=skopeo,
            model=model,
            registry=registry,
            repository=repository,
            tag=tag,
        )

        if skip_if_exists:
            if image.exists_remote():
                rprint(f"Skipping build of {image.tagged_image} as it exists in the registry")
                if image_cleanup and image.exists_local():
                    rprint(f"Cleaning up image directory: {image.layout_dir}")
                    image.cleanup()
                if model_cleanup and model.exists():
                    rprint(f"Cleaning up model directory: {model.path}")
                    model.cleanup()
                exit(0)

        rprint(f"Downloading {repo_id} to {model.path}")
        model.download()
        rprint(f"Building {image.tagged_image} in {image.layout_dir}")
        image.build()
        if push:
            rprint(f"Pushing {image.tagged_image}")
            image.push()
        if image_cleanup:
            rprint(f"Cleaning up image directory: {image.layout_dir}")
            image.cleanup()
        if model_cleanup:
            rprint(f"Cleaning up model directory: {model.path}")
            model.cleanup()
    rprint("Done! 🎉")
