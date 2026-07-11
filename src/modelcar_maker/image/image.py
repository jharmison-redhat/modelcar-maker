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

from ..model import Model
from ..util import cleanup
from ..util import logger
from ..util import normalize
from ..util import settings


class ModelcarImage(BaseModel):
    """Represents a complete Modelcar Image, including methods to build, push, and clean them up."""

    base_image: str
    model: Model
    pull: bool
    registry: str
    repository: str
    base_path: Path = Path("tmp/base")
    tag: Optional[str] = None
    authfile: Optional[Path] = None

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

        return dict(tag=self.normalized, repo_id=self.model.repo_id, commit=self.model.commit)

    def exists_local(self) -> bool:
        """Whether the OCI layout exists and is populated at all (does not guarantee models in it)"""
        return self.layout_dir.joinpath("index.json").exists()

    def exists_remote(self) -> bool:
        """Whether the OCI image is available at the remote registry with this tag
        (does not guarantee up to date, or models in it)"""
        extra_args = dict()
        if self.authfile is not None:
            extra_args["params"] = ["--authfile", self.authfile]
        try:
            manifest = skopeo_inspect(skopeo_ref=f"docker://{self.tagged_image}", **extra_args)
            logger.debug(f"Foud manifest: {manifest}")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Remote image {self.tagged_image} could not be inspected, appears not to exist")
            logger.debug(e)
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
        self._pull_base_image()

        logger.debug(f"Copying base image {self.base_image} from {self.base_path} to {self.layout_dir}")
        for item in self.base_path.iterdir():
            dest = self.layout_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        labels = {k: v.format(**self._rendered_labels) for k, v in settings.image.labels.items()}
        logger.debug(f"Rendered labels: {labels}")
        annotations = settings.image.annotations
        model_card, model_files = self.model.model_files()
        filtered_model_files = [file for file in model_files if self._should_include(file)]

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
        extra_args = dict()
        if self.authfile is not None and self.authfile.exists():
            extra_args["params"] = ["--authfile", str(self.authfile)]

        skopeo_push(src=self.layout_dir, oci_ref=self.tagged_image, **extra_args)

    def _pull_base_image(self) -> None:
        """Pulls the base image into a cache directory to reuse for image builds"""
        ret = 0
        logger.info(f"Pulling base image {self.base_image}")
        if self.pull:
            logger.debug(f"Cleaning up {self.base_path} and pulling {self.base_image} to it")
            cleanup(self.base_path)
            ret = skopeo_pull(base_image=self.base_image, dest=self.base_path)
        elif not self.base_path.joinpath("index.json").exists():
            logger.debug(f"Pulling {self.base_image} to {self.base_path}")
            ret = skopeo_pull(base_image=self.base_image, dest=self.base_path)
        if ret != 0:
            raise RuntimeError(f"Pull of {self.base_image} to {self.base_path} failed (returned {ret})")

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
