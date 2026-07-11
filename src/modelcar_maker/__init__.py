from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .download import hf_download
from .image.types import Backend
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
    backend: Backend | str = settings.image.backend,
    base_image: str = settings.image.base_image,
    architectures: list[str] = settings.image.architectures,
    authfile: Path | None = settings.image.get("authfile"),
    push: bool = settings.image.push,
    image_cleanup: bool = settings.image.cleanup,
    model_cleanup: bool = settings.models.cleanup,
    skip_if_exists: bool = settings.image.skip_if_exists,
    pull: bool = settings.image.pull,
    files: list[str] = list(),
    tag: str | None = None,
) -> ProcessResult:
    """Run through the entire process of downloading, packaging, and publishing a Model Car image."""
    result = ProcessResult()
    model_settings = settings.models.get(model, dict())

    if isinstance(backend, str):
        try:
            backend = Backend(backend)
        except ValueError:
            raise NotImplementedError(f"Backend {backend!r} is not supported") from None

    from .image.types import BuildArgs
    from .image.types import PushArgs
    from .image.types import RmArgs

    if backend is Backend.OLOT:
        from .image.olot import do_build
        from .image.olot import do_image_rm
        from .image.olot import do_push
        from .image.olot import image_exists

    if len(files) == 0:
        model_files = model_settings.get("files")
        if model_files is not None:
            files = model_files

    if tag is None:
        model_tag: str | None = model_settings.get("tag")
        if model_tag is not None:
            tag = model_tag
        else:
            tag = f"{normalize(model)}-modelcar"

    if skip_if_exists:
        if image_exists(model, image_repo, tag, authfile):
            # It was requested that we skip the build, and the image exists.
            # We should still check if a cleanup is called for.
            result.skipped = True
            if image_cleanup:
                oci_layout_dir = Path("tmp").joinpath(tag) if backend is Backend.OLOT else None
                if do_image_rm(
                    RmArgs(model=model, repo=image_repo, oci_layout_dir=oci_layout_dir, architectures=architectures)
                ):
                    result.image_cleaned_up = True
            if model_cleanup:
                download_dir = Path("models").joinpath(normalize(model))
                result.model_cleaned_up = cleanup(download_dir)
            return result

    # Ensure that the model is downloaded before the build
    download_dir, commit = hf_download(model, files=files)
    result.downloaded_to = download_dir

    # Build the image
    build_result = do_build(
        BuildArgs(
            model=model,
            repo=image_repo,
            model_dir=download_dir,
            base_image=base_image,
            commit=commit,
            pull=pull,
            architectures=architectures,
            tag=tag,
        )
    )
    result.image = build_result.image
    result.image_built = True

    if push:
        do_push(
            PushArgs(
                model=model,
                repo=image_repo,
                authfile=authfile,
                oci_layout_dir=build_result.oci_layout_dir,
                architectures=architectures,
                manifest_list=build_result.manifest_list,
                tag=tag,
            )
        )
        result.image_pushed = True

    if image_cleanup:
        if backend is Backend.OLOT:
            if do_image_rm(
                RmArgs(
                    model=model,
                    repo=image_repo,
                    oci_layout_dir=build_result.oci_layout_dir,
                    architectures=architectures,
                    manifest_list=build_result.manifest_list,
                )
            ):
                result.image_cleaned_up = True

    if model_cleanup and result.image_built:
        # Only clean up the files if it was skipped (above) or an image definitely got built
        result.model_cleaned_up = cleanup(download_dir)

    return result
