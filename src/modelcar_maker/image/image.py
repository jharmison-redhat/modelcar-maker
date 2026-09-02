import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from functools import cached_property
from pathlib import Path
from typing import Optional

from olot.backend.skopeo import skopeo_inspect
from olot.backend.skopeo import skopeo_pull
from olot.backend.skopeo import skopeo_push
from olot.basics import oci_layers_on_top
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import field_validator

from ..__version__ import version as modelcar_maker_version
from ..model import Model
from ..util import atomic_replace_directory
from ..util import cleanup
from ..util import logger
from ..util import normalize
from ..util import remove_path
from ..util import settings

_OCI_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


class Skopeo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authfile: Optional[Path] = None

    @property
    def _extra_args(self) -> dict[str, list[str]]:
        extra_args = dict()
        if self.authfile is not None:
            extra_args["params"] = ["--authfile", str(self.authfile)]
        return extra_args

    def pull(self, base_image: str, dest: Path) -> subprocess.CompletedProcess:
        dest.parent.mkdir(parents=True, exist_ok=True)
        return skopeo_pull(base_image=base_image, dest=dest, **self._extra_args)

    def push(self, src: Path, dest: str) -> subprocess.CompletedProcess:
        return skopeo_push(src=src, oci_ref=dest, **self._extra_args)

    def inspect(self, reference: str) -> Optional[str]:
        try:
            return skopeo_inspect(skopeo_ref=reference, **self._extra_args)
        except subprocess.CalledProcessError:
            logger.warning(f"Unable to inspect: {reference}")
            return None


class BaseCacheMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_reference: str
    resolved_digest: str


class BaseImage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tagged_image: str
    skopeo: Skopeo
    update: bool
    path: Path = Path("tmp/base")

    @property
    def metadata_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.metadata.json")

    @staticmethod
    def _layout_is_usable(path: Path) -> bool:
        index = path / "index.json"
        if not index.is_file():
            return False
        try:
            data = json.loads(index.read_text())
        except (OSError, json.JSONDecodeError):
            return False
        return isinstance(data, dict) and isinstance(data.get("manifests"), list) and bool(data["manifests"])

    @property
    def exists(self) -> bool:
        return self._layout_is_usable(self.path)

    def _read_metadata(self) -> Optional[BaseCacheMetadata]:
        try:
            return BaseCacheMetadata.model_validate_json(self.metadata_path.read_text())
        except (OSError, ValueError):
            return None

    def _resolve_digest(self) -> str:
        manifest = self.skopeo.inspect(f"docker://{self.tagged_image}")
        if manifest is None:
            raise RuntimeError(f"Unable to resolve base image digest for {self.tagged_image}")
        return f"sha256:{hashlib.sha256(manifest.encode('utf-8')).hexdigest()}"

    def _cache_matches(self, digest: str) -> bool:
        metadata = self._read_metadata()
        return (
            self.exists
            and metadata is not None
            and metadata.requested_reference == self.tagged_image
            and metadata.resolved_digest == digest
        )

    def pull(self) -> None:
        """Validate or atomically populate the reusable base-image cache."""
        if not self.update:
            if self.exists:
                logger.debug("Using existing base cache without identity validation because pulling is disabled")
            else:
                logger.debug("No base cache exists and pulling is disabled")
            return

        digest = self._resolve_digest()
        logger.info(f"Resolved {self.tagged_image} to {digest}")
        if self._cache_matches(digest):
            logger.debug("Base image cache identity and digest match")
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=f".{self.path.name}.stage-", dir=self.path.parent))
        metadata_fd, metadata_name = tempfile.mkstemp(
            prefix=f".{self.metadata_path.name}.stage-", dir=self.metadata_path.parent
        )
        os.close(metadata_fd)
        staged_metadata = Path(metadata_name)
        try:
            pinned_reference = f"{self.tagged_image.split('@', 1)[0]}@{digest}"
            logger.info(f"Pulling base image {pinned_reference}")
            result = self.skopeo.pull(base_image=pinned_reference, dest=stage)
            if result.returncode != 0:
                raise RuntimeError(f"Pull of {pinned_reference} to {stage} failed: {result}")
            if not self._layout_is_usable(stage):
                raise RuntimeError(f"Pulled base image did not produce a usable OCI layout: {stage}")
            metadata = BaseCacheMetadata(requested_reference=self.tagged_image, resolved_digest=digest)
            staged_metadata.write_text(metadata.model_dump_json(indent=2) + "\n")
            atomic_replace_directory(
                stage,
                self.path,
                companion_replacements=((staged_metadata, self.metadata_path),),
            )
        finally:
            remove_path(stage)
            remove_path(staged_metadata)

    def copy_to(self, dest: Path) -> None:
        if not self.exists:
            raise RuntimeError(f"No usable base image cache exists at {self.path}")
        logger.debug(f"Copying base image {self.tagged_image} from {self.path} to {dest}")
        dest.mkdir(parents=True, exist_ok=True)
        for item in self.path.iterdir():
            dest_item = dest / item.name
            logger.debug(f"{item} -> {dest_item}")
            if item.is_dir():
                shutil.copytree(item, dest_item)
            else:
                shutil.copy2(item, dest_item)


class ModelcarImage(BaseModel):
    """Represents a complete Modelcar Image, including methods to build, push, and clean them up."""

    model_config = ConfigDict(extra="forbid")

    base_image: BaseImage
    model: Model
    skopeo: Skopeo
    registry: str
    repository: str
    tag: Optional[str] = None

    @field_validator("registry", "repository")
    @classmethod
    def image_name_parts_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("registry and repository must not be empty")
        return value

    @field_validator("tag")
    @classmethod
    def tag_must_be_valid(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and _OCI_TAG.fullmatch(value) is None:
            raise ValueError("tag must be a valid OCI tag of at most 128 characters")
        return value

    @cached_property
    def normalized(self) -> str:
        """Either the specified tag, or one built from the model repo_id"""
        value = f"{normalize(self.model.repo_id)}-modelcar" if self.tag is None else self.tag
        if _OCI_TAG.fullmatch(value) is None:
            raise ValueError("generated tag is not a valid OCI tag of at most 128 characters")
        return value

    @cached_property
    def tagged_image(self) -> str:
        """The complete tagged image name"""
        return f"{self.registry}/{self.repository}:{self.normalized}"

    @cached_property
    def layout_dir(self) -> Path:
        """The directory where the OCI layout is constructed on disk"""
        root = Path("tmp").resolve()
        path = root / self.normalized
        if not path.resolve().is_relative_to(root):
            raise ValueError("Generated OCI layout path is outside the tmp root")
        return path

    @property
    def _rendered_labels(self) -> dict[str, str]:
        """The mapping of variable replacements in labels from configuration"""
        if self.model.commit is None:
            raise RuntimeError(f"Model from {self.model.repo_id} was not downloaded (or updated), no commit available")

        datetime_utc = datetime.datetime.now(datetime.UTC).isoformat()
        return dict(
            tag=self.normalized,
            repo_id=self.model.repo_id,
            commit=self.model.commit,
            datetime_utc=datetime_utc,
            modelcar_maker_version=modelcar_maker_version,
        )

    def exists_local(self) -> bool:
        """Whether the OCI layout exists and is populated at all (does not guarantee models in it)"""
        return self.layout_dir.joinpath("index.json").exists()

    def exists_remote(self) -> bool:
        """Whether the OCI image is available at the remote registry with this tag
        (does not guarantee up to date, or models in it)"""
        manifest = self.skopeo.inspect(reference=f"docker://{self.tagged_image}")
        logger.debug(f"Found manifest: {manifest}")
        if manifest is None:
            logger.warning(f"Remote image {self.tagged_image} could not be inspected, appears not to exist")
            return False
        return True

    def cleanup(self) -> bool:
        """Cleans up a local OCI layout directory"""
        return cleanup(self.layout_dir)

    def build(self) -> dict[str, int | None]:
        """Downloads a base image, prepares a layout directory, adds model files, and configures image metadata"""
        if self.exists_local():
            logger.debug(f"Before build, cleaning out existing layout at {self.layout_dir}")
            self.cleanup()
        self.base_image.copy_to(self.layout_dir)

        labels = {k: v.format(**self._rendered_labels) for k, v in settings.image.labels.items()}
        logger.debug(f"Rendered labels: {labels}")
        annotations = {k: v.format(**self._rendered_labels) for k, v in settings.image.annotations.items()}
        logger.debug(f"Rendered annotations: {annotations}")
        model_card, model_files = self.model.model_files()
        filtered_model_files = [file for file in model_files if self._should_include(file)]

        logger.info(f"Adding model files from {self.model.path} to {self.layout_dir} now")
        logger.debug(f"model_files={filtered_model_files}")
        oci_layers_on_top(
            ocilayout=self.layout_dir,
            model_files=filtered_model_files,
            modelcard=model_card,
            labels=labels,
            annotations=annotations,
            root_dir=self.model.path,
        )
        return {
            "files": len(filtered_model_files) + (1 if model_card is not None else 0),
            "layers": len(filtered_model_files) + (1 if model_card is not None else 0),
        }

    def digest(self) -> Optional[str]:
        """Return the OCI manifest digest recorded by this layout, when available."""
        try:
            manifests = json.loads(self.layout_dir.joinpath("index.json").read_text()).get("manifests", [])
            digest = manifests[0].get("digest") if manifests else None
            return digest if isinstance(digest, str) else None
        except (OSError, json.JSONDecodeError, AttributeError):
            return None

    def push(self) -> Optional[str]:
        """Pushes an image from the local layout directory to the remote registry"""
        result = self.skopeo.push(src=self.layout_dir, dest=self.tagged_image)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to push {self.tagged_image}: {result}")
        manifest = self.skopeo.inspect(reference=f"docker://{self.tagged_image}")
        return f"sha256:{hashlib.sha256(manifest.encode('utf-8')).hexdigest()}" if manifest is not None else None

    @staticmethod
    def _should_include(file: Path) -> bool:
        """Simple filter function to determine if a file should be included in an image definition."""
        for part in file.parts:
            if part.startswith("."):
                return False
        if file.name == "Containerfile":
            return False
        if file.name == "original":
            return False
        if file.name == "consolidated.safetensors":
            return False
        return True
