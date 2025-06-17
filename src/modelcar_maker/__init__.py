from pathlib import Path

from .download import hf_download
from .image import build
from .image import push
from .image import render


def process(model: str, image_repo: str, authfile: Path | None = None) -> None:
    download_dir = hf_download(model)
    render(model, download_dir)
    build(model, image_repo, download_dir)
    push(model, image_repo, authfile)
