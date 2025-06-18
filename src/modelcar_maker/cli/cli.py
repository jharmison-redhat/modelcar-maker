from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint
from typing_extensions import Annotated

from .. import process
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
    model: Annotated[
        Optional[str],
        typer.Argument(help="The model to download (otherwise ensure all defaults are downloaded)"),
    ] = None,
    registry: Annotated[
        str,
        typer.Option(
            "--registry",
            help="The registry to include in the image reference",
        ),
    ] = settings.image.registry,
    repository: Annotated[
        str,
        typer.Option(
            "-r",
            "--repository",
            help="The repository, within the registry, to include in the image reference",
        ),
    ] = settings.image.repository,
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
            help="The authfile to use for the podman push",
        ),
    ] = settings.image.get("authfile"),
    cleanup: Annotated[
        bool,
        typer.Option(
            "--clean-up/--no-clean-up",
            help="Clean up the container images after push to free up space",
        ),
    ] = settings.image.cleanup,
    skip_if_exists: Annotated[
        bool,
        typer.Option(
            "--skip-if-exists/--no-skip-if-exists",
            help="Skip downloading and publishing the image if it already exists",
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
    ] = 0,
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
    """Modelcar Maker model download, build, and (optionally) push"""

    logger = make_logger(verbose)
    image_repo = f"{registry}/{repository}"
    logger.debug(f"Push {image_repo}: {push}")
    if model is None:
        if not isinstance(settings.models.default, list):
            raise RuntimeError(f"Default models should be a list, not {type(settings.models.default)}")
        models = settings.models.default
    else:
        models = [model]

    for model in models:
        result = process(
            str(model),
            image_repo,
            authfile=authfile,
            push=push,
            cleanup=cleanup,
            skip_if_exists=skip_if_exists,
        )
        if result.skipped:
            if result.image_cleaned_up:
                rprint(f"{model} was skipped as it already exists at {image_repo} (but it was cleaned up locally).")
            else:
                rprint(f"{model} was skipped as it already exists at {image_repo}.")
        if result.image_cleaned_up:
            rprint(
                f"{model} was downloaded to {result.downloaded_to}, built as {result.image}, pushed, and then cleaned up."
            )
        elif result.image_pushed:
            rprint(f"{model} was downloaded to {result.downloaded_to}, built as {result.image}, and pushed.")
        elif result.image_built:
            rprint(f"{model} was downloaded to {result.downloaded_to} and built as {result.image}, but not pushed.")
