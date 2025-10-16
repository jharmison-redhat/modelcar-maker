from pathlib import Path

from jinja2 import Environment
from jinja2 import PackageLoader
from jinja2 import select_autoescape

from ..util import normalize

env = Environment(loader=PackageLoader("modelcar_maker"), autoescape=select_autoescape())

template = env.get_template("Containerfile.j2")


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


def render(repo_id: str, model_dir: Path, commit: str) -> None:
    """Renders out the Containerfile.j2 template with the files in model_dir."""
    model_files = [file.name for file in model_dir.iterdir() if should_include(file)]
    modelcard_source = ""
    for file in model_files:
        if file.startswith("README"):
            modelcard_source = file
            break

    with open(model_dir.joinpath("Containerfile"), "w") as f:
        f.write(
            template.render(
                model=repo_id,
                normalized=normalize(repo_id),
                model_files=model_files,
                modelcard_source=modelcard_source,
                commit=commit,
            )
        )
