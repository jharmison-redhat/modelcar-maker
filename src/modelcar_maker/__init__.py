from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .download import hf_download
from .image import do_build
from .image import do_image_rm
from .image import do_push
from .image import image_exists
from .image import render
from .util import settings


@dataclass
class ProcessResult:
    skipped: bool = False
    downloaded_to: Optional[Path] = None
    image: Optional[str] = None
    image_built: bool = False
    image_pushed: bool = False
    image_cleaned_up: bool = False


def process(
    model: str,
    image_repo: str = f"{settings.image.registry}/{settings.image.repository}",
    authfile: Path | None = settings.image.get("authfile"),
    push: bool = settings.image.push,
    cleanup: bool = settings.image.cleanup,
    skip_if_exists: bool = settings.image.skip_if_exists,
) -> ProcessResult:
    result = ProcessResult()

    if skip_if_exists:
        if image_exists(model, image_repo):
            result.skipped = True
            return result

    download_dir = hf_download(model)
    result.downloaded_to = download_dir

    render(model, download_dir)
    result.image = do_build(model, image_repo, download_dir)
    result.image_built = True

    if push:
        do_push(model, image_repo, authfile)
        result.image_pushed = True

    if cleanup and push:
        do_image_rm(model, image_repo)
        result.image_cleaned_up = True

    return result
