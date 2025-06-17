from pathlib import Path

from huggingface_hub import snapshot_download

from ..util import logger
from ..util import normalize


def hf_download(repo_id: str) -> Path:
    normalized = normalize(repo_id)
    download_dir = Path(f"models/{normalized}")
    logger.info(f"Downloading {repo_id} to {download_dir}")
    if not download_dir.is_dir():
        download_dir.mkdir(parents=True, exist_ok=True)

    snapshot_download(repo_id, local_dir=download_dir)

    return download_dir
