from pathlib import Path

from .download import hf_download
from .image import do_build
from .image import do_image_rm
from .image import do_push
from .image import image_exists
from .image import render
from .util import settings


def process(
    model: str,
    image_repo: str = f"{settings.image.registry}/{settings.image.repository}",
    authfile: Path | None = settings.image.get("authfile"),
    push: bool = settings.image.push,
    cleanup: bool = settings.image.cleanup,
    skip_if_exists: bool = settings.image.skip_if_exists,
) -> None:
    if skip_if_exists:
        if image_exists(model, image_repo):
            return None
    download_dir = hf_download(model)
    render(model, download_dir)
    do_build(model, image_repo, download_dir)
    if push:
        do_push(model, image_repo, authfile)
    if cleanup:
        do_image_rm(model, image_repo)
