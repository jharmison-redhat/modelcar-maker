import datetime
import hashlib
import shutil
import subprocess
from functools import cached_property
from pathlib import Path
from typing import Optional

from olot.backend.skopeo import skopeo_inspect
from olot.backend.skopeo import skopeo_pull
from olot.backend.skopeo import skopeo_push
from olot.basics import oci_layers_on_top
from pydantic import BaseModel

from ..__version__ import version as modelcar_maker_version
from ..model import Model
from ..util import cleanup
from ..util import logger
from ..util import normalize
from ..util import settings


class Skopeo(BaseModel):
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
        except subprocess.CalledProcessError as e:
            logger.warning(f"Unable to inspect: {reference}")
            return None


class BaseImage(BaseModel):
    tagged_image: str
    skopeo: Skopeo
    update: bool
    path: Path = Path("tmp/base")

    @property
    def exists(self) -> bool:
        return self.path.joinpath("index.json").exists()

    def pull(self) -> None:
        """Pulls the base image into a cache directory to reuse for image builds"""
        result = None
        if not self.exists:
            logger.info(f"Pulling base image {self.tagged_image}")
            result = self.skopeo.pull(base_image=self.tagged_image, dest=self.path)
        elif self.needs_update:
            logger.warning(f"Cleaning up stale base image from cache: {self.path}")
            cleanup(self.path)
            logger.info(f"Pulling fresh base image {self.tagged_image}")
            result = self.skopeo.pull(base_image=self.tagged_image, dest=self.path)
        if result is not None and result.returncode != 0:
            raise RuntimeError(f"Pull of {self.tagged_image} to {self.path} failed: {result}")

    def copy_to(self, dest: Path) -> None:
        logger.debug(f"Copying base image {self.tagged_image} from {self.path} to {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        for item in self.path.iterdir():
            dest_item = dest / item.name
            logger.debug(f"{item} -> {dest_item}")
            if item.is_dir():
                shutil.copytree(item, dest_item)
            else:
                shutil.copy2(item, dest_item)

    @property
    def needs_update(self) -> bool:
        """Determines if the locally cached base image manifest differs from the remote"""
        if not self.update and self.exists:
            logger.debug("Base image is not set to update, already exists")
            return False
        local_manifest = self.skopeo.inspect(f"oci:{self.path}:latest")
        if local_manifest is None:
            logger.debug(f"No valid manifest found in {self.path}")
            return True
        local_digest = hashlib.sha256(local_manifest.encode("utf-8")).hexdigest()
        logger.info(f"Locally cached base image manifest digest: {local_digest}")

        remote_manifest = self.skopeo.inspect(f"docker://{self.tagged_image}")
        if remote_manifest is None:
            logger.error("Unable to read remote manifest, attempting pull but expecting failure")
            return True
        remote_digest = hashlib.sha256(remote_manifest.encode("utf-8")).hexdigest()
        logger.info(f"Remote base image manifest digest: {remote_digest}")

        needs_update = local_digest != remote_digest
        if needs_update:
            logger.debug("Not matching! Base image cache update required...")
        return needs_update


class ModelcarImage(BaseModel):
    """Represents a complete Modelcar Image, including methods to build, push, and clean them up."""

    base_image: BaseImage
    model: Model
    skopeo: Skopeo
    registry: str
    repository: str
    tag: Optional[str] = None

    @cached_property
    def normalized(self) -> str:
        """Either the specified tag, or one built from the model repo_id"""
        if self.tag is None:
            return f"{normalize(self.model.repo_id)}-modelcar"
        else:
            return self.tag

    @cached_property
    def tagged_image(self) -> str:
        """The complete tagged image name"""
        return f"{self.registry}/{self.repository}:{self.normalized}"

    @cached_property
    def layout_dir(self) -> Path:
        """The directory where the OCI layout is constructed on disk"""
        return Path(f"tmp/{self.normalized}")

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

    def build(self) -> None:
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

    def push(self) -> None:
        """Pushes an image from the local layout directory to the remote registry"""
        result = self.skopeo.push(src=self.layout_dir, dest=self.tagged_image)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to push {self.tagged_image}: {result}")

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
