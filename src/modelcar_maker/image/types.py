from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Optional


class Backend(StrEnum):
    """Supported build backends."""

    PODMAN = "podman"
    OLOT = "olot"


@dataclass
class BuildArgs:
    """Arguments for building a modelcar image."""

    model: str
    repo: str
    model_dir: Path
    base_image: str
    commit: str
    pull: bool = True


@dataclass
class BuildResult:
    """Result of a modelcar image build."""

    image: str
    oci_layout_dir: Optional[Path] = None


@dataclass
class PushArgs:
    """Arguments for pushing a modelcar image."""

    model: str
    repo: str
    authfile: Optional[Path] = None
    oci_layout_dir: Optional[Path] = None


@dataclass
class RmArgs:
    """Arguments for removing a modelcar image."""

    model: str
    repo: str
    oci_layout_dir: Optional[Path] = None
