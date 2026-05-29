import warnings
from pathlib import Path

from huggingface_hub import snapshot_download
from rich import print as rprint
from tqdm import TqdmExperimentalWarning
from tqdm.rich import tqdm_rich

from ..util import logger
from ..util import normalize

warnings.filterwarnings("ignore", category=TqdmExperimentalWarning)


def hf_download(repo_id: str) -> tuple[Path, str]:
    """Download the given model repo_id into the models/ directory, returning the path."""
    from huggingface_hub import HfApi

    normalized = normalize(repo_id)
    download_dir = Path(f"models/{normalized}")
    logger.info(f"Downloading {repo_id} to {download_dir}")
    if not download_dir.is_dir():
        download_dir.mkdir(parents=True, exist_ok=True)

    rprint(f"Downloading {repo_id} to {download_dir}")
    snapshot_download(repo_id, local_dir=download_dir, tqdm_class=tqdm_rich)  # type: ignore[arg-type]

    # Get the commit hash from the public API instead of internal cache metadata.
    sha = HfApi().model_info(repo_id).sha
    assert sha is not None

    return (
        download_dir,
        sha,
    )
