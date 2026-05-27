from pathlib import Path

from jinja2 import Environment
from jinja2 import PackageLoader
from jinja2 import select_autoescape

from ..util import normalize
from .common import list_model_files

env = Environment(loader=PackageLoader("modelcar_maker"), autoescape=select_autoescape())

template = env.get_template("Containerfile.j2")


def render(repo_id: str, model_dir: Path, commit: str, base_image: str) -> None:
    """Renders out the Containerfile.j2 template with the files in model_dir."""
    model_files, modelcard_source = list_model_files(model_dir)

    with open(model_dir.joinpath("Containerfile"), "w") as f:
        f.write(
            template.render(
                base_image=base_image,
                model=repo_id,
                normalized=normalize(repo_id),
                model_files=model_files,
                modelcard_source=modelcard_source or "",
                commit=commit,
            )
        )
