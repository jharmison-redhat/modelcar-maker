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
    normalized = normalize(repo_id)
    download_dir = Path(f"models/{normalized}")
    logger.info(f"Downloading {repo_id} to {download_dir}")
    if not download_dir.is_dir():
        download_dir.mkdir(parents=True, exist_ok=True)

    rprint(f"Downloading {repo_id} to {download_dir}")
    snapshot_download(repo_id, local_dir=download_dir, tqdm_class=tqdm_rich)

    cache_dir = download_dir.joinpath(".cache").joinpath("huggingface").joinpath("download")
    commit = None
    for _, _, files in cache_dir.walk():
        for file in files:
            if file.endswith(".metadata"):
                metadata_file = cache_dir.joinpath(file)
                with open(metadata_file) as f:
                    commit = list(f.readlines())[0].strip()
            if commit is not None:
                break

    assert commit is not None
    return (download_dir, commit,)
