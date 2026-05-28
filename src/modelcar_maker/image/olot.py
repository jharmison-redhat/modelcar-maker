import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from rich import print as rprint

from ..util import logger
from ..util import normalize
from .common import _image
from .common import list_model_files
from .types import BuildArgs
from .types import BuildResult
from .types import PushArgs
from .types import RmArgs


def _base_image_cache_key(base_image: str) -> str:
    """Return a short, safe directory name for caching a base image OCI layout."""
    return hashlib.sha256(base_image.encode()).hexdigest()[:16]


def _copy_cached_layout(cache_dir: Path, dest_dir: Path) -> None:
    """Copy the contents of a cached OCI layout directory into a fresh model layout directory."""
    for item in cache_dir.iterdir():
        dest = dest_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)


def _remote_manifest_digest(base_image: str) -> str:
    """Fetch the raw manifest from the remote registry and return its sha256 digest."""
    raw = subprocess.run(
        ["skopeo", "inspect", "--raw", f"docker://{base_image}"],
        capture_output=True,
        check=True,
    ).stdout
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _cached_manifest_digest(cache_dir: Path) -> str | None:
    """Read the digest of the first manifest in a cached OCI layout's index.json."""
    idx = cache_dir / "index.json"
    if not idx.exists():
        return None
    data = json.loads(idx.read_text())
    manifests = data.get("manifests", [])
    if manifests:
        return manifests[0].get("digest")
    return None


def _authfile_env(authfile: Path | None) -> dict[str, str] | None:
    """Return an env dict with DOCKER_CONFIG set to the parent dir of authfile, if provided."""
    if authfile is None:
        return None
    authfile = authfile.expanduser().resolve()
    if not authfile.exists():
        logger.warning(f"Authfile {authfile} does not exist, auth may fail")
    return {"DOCKER_CONFIG": str(authfile.parent)}


def do_build(args: BuildArgs) -> BuildResult:
    """Build an OCI image for the given model using olot.

    Reuses a cached base image OCI layout when available and up-to-date
    to avoid re-downloading the same base image for every model build.
    """
    from olot.backend.oras_py import oras_py_pull
    from olot.basics import oci_layers_on_top

    normalized = normalize(args.model)
    oci_layout_dir = Path("tmp").joinpath(normalized)

    # Ensure a clean OCI layout directory for this model build
    if oci_layout_dir.exists():
        shutil.rmtree(oci_layout_dir)
    oci_layout_dir.mkdir(parents=True, exist_ok=True)

    # Cache directory for the base image, keyed by the exact image ref
    cache_dir = Path("tmp").joinpath(".base-image-cache", _base_image_cache_key(args.base_image))

    need_pull = True
    if cache_dir.exists() and not args.pull:
        # User explicitly disabled pulling – reuse cache unconditionally
        need_pull = False
    elif cache_dir.exists():
        # Pull is enabled: compare cached digest to remote digest
        try:
            remote_digest = _remote_manifest_digest(args.base_image)
            cached_digest = _cached_manifest_digest(cache_dir)
            if remote_digest == cached_digest:
                need_pull = False
            else:
                logger.info(f"Base image {args.base_image} changed, invalidating cache")
                shutil.rmtree(cache_dir)
        except Exception:
            # If we can't inspect the remote, nuke cache and re-pull to be safe
            shutil.rmtree(cache_dir)

    if need_pull:
        logger.info(f"Pulling base image {args.base_image} into {oci_layout_dir}")
        rprint(f"Pulling base image {args.base_image} into {oci_layout_dir}")
        oras_py_pull(args.base_image, oci_layout_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        _copy_cached_layout(oci_layout_dir, cache_dir)
    else:
        logger.info(f"Reusing cached base image {args.base_image} from {cache_dir}")
        rprint(f"Reusing cached base image {args.base_image}")
        _copy_cached_layout(cache_dir, oci_layout_dir)

    model_files, modelcard_source = list_model_files(args.model_dir)
    if not model_files:
        raise RuntimeError(f"No model files found in {args.model_dir}")

    resolved_model_files = [args.model_dir.joinpath(f) for f in model_files]
    resolved_modelcard = args.model_dir.joinpath(modelcard_source) if modelcard_source else None

    labels = {
        "name": f"{normalized}-modelcar",
        "io.k8s.display-name": f"{normalized}-modelcar",
        "description": (
            f"A very small RHEL image which contains the contents of huggingface.co/{args.model} downloaded to /models"
        ),
        "io.k8s.description": (
            f"A very small RHEL image which contains the contents of huggingface.co/{args.model} downloaded to /models"
        ),
        "maintainer": "James Harmison <jharmiso@redhat.com>",
        "release": "1",
        "summary": f"{args.model} model car image",
        "url": "https://github.com/jharmison-redhat/modelcar-maker",
        "model.name": args.model,
        "model.commit": args.commit,
    }
    annotations = {
        "org.opencontainers.image.source": "https://github.com/jharmison-redhat/modelcar-maker",
    }

    logger.info(f"Adding {len(resolved_model_files)} model file(s) as OCI layers")
    rprint(f"Adding {len(resolved_model_files)} model file(s) as OCI layers to {oci_layout_dir}")
    oci_layers_on_top(
        oci_layout_dir,
        resolved_model_files,
        modelcard=resolved_modelcard,
        labels=labels,
        annotations=annotations,
        root_dir=args.model_dir,
    )

    image = _image(args.model, args.repo)
    return BuildResult(image=image, oci_layout_dir=oci_layout_dir)


def do_push(args: PushArgs) -> None:
    """Push the OCI layout for the given model to the registry using olot/oras-py."""
    from olot.backend.oras_py import oras_py_push

    image = _image(args.model, args.repo)
    env_overrides = _authfile_env(args.authfile)
    logger.info(f"Pushing {image} from {args.oci_layout_dir}")
    rprint(f"Pushing {image}")

    if args.oci_layout_dir is None:
        raise RuntimeError("oci_layout_dir is required for olot push")

    if env_overrides:
        old_docker_config = os.environ.get("DOCKER_CONFIG")
        os.environ.update(env_overrides)
        try:
            oras_py_push(args.oci_layout_dir, image)
        finally:
            if old_docker_config is not None:
                os.environ["DOCKER_CONFIG"] = old_docker_config
            else:
                os.environ.pop("DOCKER_CONFIG", None)
    else:
        oras_py_push(args.oci_layout_dir, image)


def image_exists(model: str, repo: str) -> bool:
    """Return whether the image for the given model and
    image registry repo exists at the remote repository."""
    try:
        from oras.container import Container
        from oras.provider import Registry

        image = _image(model, repo)
        registry = Registry()
        container = Container(image, registry=registry.hostname)
        tags = registry.get_tags(container)
        model_tag = normalize(model) + "-modelcar"
        return model_tag in tags
    except Exception as e:
        logger.debug(f"image_exists check failed: {e}")
        return False


def do_image_rm(args: RmArgs) -> bool:
    """Remove the OCI layout directory to free up space.
    Returns True when successful, False when failed."""
    if args.oci_layout_dir is None:
        raise RuntimeError("oci_layout_dir is required for olot image removal")
    try:
        shutil.rmtree(args.oci_layout_dir)
        return True
    except Exception:
        return False
