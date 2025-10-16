from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .download import hf_download
from .image import do_build
from .image import do_image_rm
from .image import do_push
from .image import image_exists
from .image import render
from .util import normalize
from .util import settings


@dataclass
class ProcessResult:
    """Represents the state of the process workflow."""

    skipped: bool = False
    downloaded_to: Optional[Path] = None
    image: Optional[str] = None
    image_built: bool = False
    image_pushed: bool = False
    image_cleaned_up: bool = False
    model_cleaned_up: bool = False


def cleanup(path: Path, skip: list[str] = ["Containerfile"]) -> bool:
    """Remove all children of the provided path, except for top-level files provided in skip."""
    changed = False
    for subpath in path.iterdir():
        if subpath.name in skip:
            continue
        if subpath.is_dir():
            _ = cleanup(subpath, skip=[])
            subpath.rmdir()
            changed = True
        else:
            subpath.unlink()
            changed = True
    return changed


def process(
    model: str,
    image_repo: str = f"{settings.image.registry}/{settings.image.repository}",
    authfile: Path | None = settings.image.get("authfile"),
    push: bool = settings.image.push,
    image_cleanup: bool = settings.image.cleanup,
    model_cleanup: bool = settings.models.cleanup,
    skip_if_exists: bool = settings.image.skip_if_exists,
) -> ProcessResult:
    """Run through the entire process of downloading, packaging, and publishing a Model Car image."""
    result = ProcessResult()

    if skip_if_exists:
        if image_exists(model, image_repo):
            # It was requested that we skip the build, and the image exists.
            # We should still check if a cleanup is called for.
            result.skipped = True
            if image_cleanup:
                if do_image_rm(model, image_repo):
                    result.image_cleaned_up = True
            if model_cleanup:
                download_dir = Path("models").joinpath(normalize(model))
                result.model_cleaned_up = cleanup(download_dir)
            return result

    # Ensure that the model is downloaded before the build
    download_dir, commit = hf_download(model)
    result.downloaded_to = download_dir

    # Ensure that the rendered Containerfile is up to date
    render(model, download_dir, commit)
    # Rerun the podman build
    result.image = do_build(model, image_repo, download_dir)
    result.image_built = True

    if push:
        do_push(model, image_repo, authfile)
        result.image_pushed = True

    if image_cleanup and image_exists(model, image_repo):
        # Only clean up the image if it was pushed
        if do_image_rm(model, image_repo):
            result.image_cleaned_up = True

    if model_cleanup and result.image_built:
        # Only clean up the files if it was skipped (above) or an image definitely got built
        result.model_cleaned_up = cleanup(download_dir)

    return result
