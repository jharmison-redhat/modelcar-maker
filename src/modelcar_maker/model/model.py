import os
import re
import tempfile
import warnings
from functools import cached_property
from pathlib import Path
from typing import Any
from typing import Optional

from huggingface_hub import HfApi
from huggingface_hub import hf_hub_download
from huggingface_hub import snapshot_download
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from tqdm import TqdmExperimentalWarning
from tqdm import tqdm
from tqdm.rich import tqdm_rich

from ..util import atomic_replace_directory
from ..util import cleanup
from ..util import logger
from ..util import normalize
from ..util import remove_path
from ..util import seed_directory
from ..util import walk

warnings.filterwarnings("ignore", category=TqdmExperimentalWarning)

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_LOCAL_METADATA_ROOT = Path(".cache/huggingface/download")


class _DisabledTqdm(tqdm):
    """A tqdm implementation that cannot be enabled by Hugging Face defaults."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["disable"] = True
        super().__init__(*args, **kwargs)


def _relative_file(filename: str) -> Path:
    path = Path(filename)
    if path.is_absolute() or path == Path(".") or ".." in path.parts:
        raise ValueError(f"Model file must be relative to the model root: {filename}")
    return path


def _metadata_file(filename: Path) -> Path:
    return _LOCAL_METADATA_ROOT / filename.parent / f"{filename.name}.metadata"


def _validate_stage_revision(stage: Path, expected_files: set[Path], sha: str) -> None:
    for filename in expected_files:
        metadata = stage / _metadata_file(filename)
        try:
            recorded_sha = metadata.read_text().splitlines()[0]
        except (OSError, IndexError) as error:
            raise RuntimeError(f"Missing download metadata for {filename} at {sha}") from error
        if recorded_sha != sha:
            raise RuntimeError(f"Downloaded file {filename} was not verified at pinned revision {sha}")


def reconcile_model_files(stage: Path, expected_files: set[Path]) -> None:
    """Prune payload and local metadata not selected for the pinned revision."""
    allowed = expected_files | {_metadata_file(filename) for filename in expected_files}
    for existing in walk(stage):
        relative = existing.relative_to(stage)
        if relative not in allowed:
            remove_path(existing)
    for root, directories, _ in os.walk(stage, topdown=False, followlinks=False):
        root_path = Path(root)
        for name in directories:
            directory = root_path / name
            if directory.is_symlink():
                directory.unlink()
            elif not any(directory.iterdir()):
                directory.rmdir()


class Model(BaseModel):
    """Represents a model and all its files"""

    model_config = ConfigDict(extra="forbid")

    repo_id: str
    files: list[str] = Field(default_factory=list)
    commit: Optional[str] = None

    @field_validator("repo_id")
    @classmethod
    def repo_id_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("repo_id must not be empty")
        return value

    @cached_property
    def path(self) -> Path:
        """The local path to the model directory"""
        root = Path("models").resolve()
        path = root / normalize(self.repo_id)
        if not path.resolve().is_relative_to(root):
            raise ValueError("Generated model path is outside the models root")
        return path

    def download(self, progress: bool = True) -> dict[str, int]:
        """Download a model and return totals for its resulting local payload."""
        sha = HfApi().model_info(self.repo_id).sha
        logger.debug(f"Found commit hash from repo: {sha}")
        if sha is None or _FULL_SHA.fullmatch(sha) is None:
            raise RuntimeError(f"Unable to recover full commit hash for {self.repo_id}")

        repo_files = {_relative_file(filename) for filename in HfApi().list_repo_files(self.repo_id, revision=sha)}
        if self.files:
            expected_files = {_relative_file(filename) for filename in self.files}
            missing = expected_files - repo_files
            if missing:
                names = ", ".join(sorted(str(path) for path in missing))
                raise RuntimeError(f"Selected files are not present in {self.repo_id} at {sha}: {names}")
        else:
            expected_files = repo_files

        self.path.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=f".{self.path.name}.stage-", dir=self.path.parent))
        cache: Optional[Path] = None
        try:
            cache = Path(tempfile.mkdtemp(prefix=f".{self.path.name}.hf-cache-", dir=self.path.parent))
            seed_directory(self.path, stage)
            if not self.files:
                logger.info(f"Downloading snapshot of {self.repo_id} at {sha} to {stage}")
                snapshot_download(  # pyright: ignore[reportCallIssue]
                    self.repo_id,
                    revision=sha,
                    local_dir=stage,
                    cache_dir=cache,
                    tqdm_class=tqdm_rich if progress else _DisabledTqdm,  # pyright: ignore[reportArgumentType]
                )
            else:
                for filename in self.files:
                    logger.info(f"Downloading {filename} from {self.repo_id} at {sha} to {stage / filename}")
                    hf_hub_download(  # pyright: ignore[reportCallIssue]
                        repo_id=self.repo_id,
                        revision=sha,
                        local_dir=stage,
                        filename=filename,
                        cache_dir=cache,
                        tqdm_class=tqdm_rich if progress else _DisabledTqdm,  # pyright: ignore[reportArgumentType]
                    )
            _validate_stage_revision(stage, expected_files, sha)
            reconcile_model_files(stage, expected_files)
            atomic_replace_directory(stage, self.path)
        finally:
            remove_path(stage)
            if cache is not None:
                remove_path(cache)
        self.commit = sha
        payload_files = [file for file in self.walk() if not file.is_relative_to(self.path / _LOCAL_METADATA_ROOT)]
        return {
            "payload_files": len(payload_files),
            "payload_bytes": sum(file.stat().st_size for file in payload_files),
        }

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
