import warnings
from functools import cached_property
from pathlib import Path
from typing import Optional

from huggingface_hub import HfApi
from huggingface_hub import hf_hub_download
from huggingface_hub import snapshot_download
from pydantic import BaseModel
from tqdm import TqdmExperimentalWarning
from tqdm.rich import tqdm_rich

from ..util import cleanup
from ..util import logger
from ..util import normalize
from ..util import walk

warnings.filterwarnings("ignore", category=TqdmExperimentalWarning)


class Model(BaseModel):
    """Represents a model and all its files"""

    repo_id: str
    files: list[str] = list()
    commit: Optional[str] = None

    @cached_property
    def path(self) -> Path:
        """The local path to the model directory"""
        return Path(f"models/{normalize(self.repo_id)}")

    def download(self) -> None:
        """Download a model from huggingface, either using a repo snapshot or a list of specific files."""
        self.path.mkdir(parents=True, exist_ok=True)

        if len(self.files) == 0:
            logger.info(f"Downloading snapshot of {self.repo_id} to {self.path}")
            snapshot_download(self.repo_id, local_dir=self.path, tqdm_class=tqdm_rich)  # type: ignore[arg-type]
        else:
            for file in self.files:
                logger.info(f"Downloading {file} from {self.repo_id} to {self.path.joinpath(file)}")
                hf_hub_download(repo_id=self.repo_id, local_dir=self.path, filename=file, tqdm_class=tqdm_rich)  # type: ignore[arg-type]

        sha = HfApi().model_info(self.repo_id).sha
        logger.debug(f"Found commit hash from repo: {sha}")
        if sha is None:
            raise RuntimeError(f"Unable to recover commit hash for {self.repo_id}")

        self.commit = sha

    def cleanup(self) -> bool:
        """Clean up a downloaded model and all files"""
        return cleanup(self.path)

    def walk(self) -> list[Path]:
        """Walks through every file in a model directory"""
        return walk(self.path)

    def exists(self) -> bool:
        """Returns whether any files exist in the model directory at all"""
        return len(self.walk()) > 0

    def model_files(self) -> tuple[Optional[Path], list[Path]]:
        """Returns the files for a given model, split with the model card (if identified) separately"""
        model_card = None
        model_files = list()
        for file in self.walk():
            if file == self.path.joinpath("README.md"):
                model_card = file
            else:
                model_files.append(file)
        return (
            model_card,
            model_files,
        )
