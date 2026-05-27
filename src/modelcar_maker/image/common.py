from pathlib import Path

from ..util import normalize


def _image(model: str, repo: str) -> str:
    """Given a model repo id and container image repository, render the full image name."""
    tag = normalize(model) + "-modelcar"
    return f"{repo}:{tag}"


def should_include(file: str | Path) -> bool:
    """Simple filter function to determine if a file should be included in an image definition."""
    if isinstance(file, Path):
        file = file.name

    if file.startswith("."):
        return False
    if file == "Containerfile":
        return False
    if file == "original":
        return False
    if file == "consolidated.safetensors":
        return False
    return True


def list_model_files(model_dir: Path) -> tuple[list[str], str | None]:
    """List files to include in the modelcar and identify the modelcard source.

    Returns:
        A tuple of (sorted list of included filenames, modelcard_source or None).
    """
    model_files = [file.name for file in model_dir.iterdir() if should_include(file)]
    model_files.sort()
    modelcard_source = None
    for file in model_files:
        if file.startswith("README"):
            modelcard_source = file
            break
    return model_files, modelcard_source
