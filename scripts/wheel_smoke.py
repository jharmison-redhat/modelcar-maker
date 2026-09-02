"""Verify that the built wheel includes loadable package resources."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    dist = Path(sys.argv[1]).resolve()
    wheels = list(dist.glob("modelcar_maker-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected one modelcar-maker wheel in {dist}, found {wheels}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        site_packages = root / "site-packages"
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--target", str(site_packages), str(wheels[0])],
            check=True,
        )
        environment = os.environ | {"PYTHONPATH": str(site_packages)}
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from importlib.resources import files; import tomllib; "
                "resource = files('modelcar_maker.util').joinpath('defaults.toml'); "
                "assert resource.is_file(); assert tomllib.loads(resource.read_text())['meta']['verbosity'] == 0",
            ],
            cwd=root,
            env=environment,
            check=True,
        )


if __name__ == "__main__":
    main()
