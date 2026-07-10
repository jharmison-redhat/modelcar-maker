import base64
import hashlib
import json
import shutil
from pathlib import Path

from oras.layout.layout import NewLayout
from oras.provider import Registry
from rich import print as rprint

from ..util import logger
from ..util import normalize
from .common import _image
from .common import list_model_files
from .types import BuildArgs
from .types import BuildResult
from .types import PushArgs
from .types import RmArgs


def _base_image_cache_key(base_image: str, architectures: list[str]) -> str:
    """Return a short, safe directory name for caching a base image OCI layout."""
    key = base_image + "|" + ",".join(sorted(architectures))
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _copy_cached_layout(cache_dir: Path, dest_dir: Path) -> None:
    """Copy the contents of a cached OCI layout directory into a fresh model layout directory."""
    for item in cache_dir.iterdir():
        dest = dest_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)


def _remote_manifest_digest(base_image: str) -> str:
    """Fetch the manifest digest from the remote registry via an oras-py HEAD request."""
    from oras.container import Container
    from oras.defaults import default_manifest_accepted_media_types

    registry = Registry()
    container = Container(base_image)
    headers = {"Accept": ", ".join(default_manifest_accepted_media_types)}
    url = f"{registry.prefix}://{container.manifest_url()}"
    response = registry.do_request(url, "HEAD", headers=headers)
    registry._check_200_response(response)
    digest = response.headers.get("Docker-Content-Digest")
    if not digest:
        raise RuntimeError("Expected Docker-Content-Digest header in HEAD response.")
    return digest


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


def _pull_base_image(base_image: str, oci_layout_dir: Path, architectures: list[str]) -> None:
    """Pull a base image into an OCI layout, filtering by architecture(s).

    Uses oras-py primitives directly to selectively pull only the manifests
    matching the requested architectures from a multi-arch base image.
    """
    from olot.backend.oras_py import _normalize_docker_hub
    from olot.backend.oras_py import _setup_auth
    from oras.container import Container
    from oras.defaults import default_index_media_type
    from oras.defaults import default_manifest_accepted_media_types
    from oras.defaults import default_manifest_media_type
    from oras.defaults import oci_blobs_dir
    from oras.defaults import oci_image_index_file
    from oras.defaults import oci_layout_file
    from oras.defaults import oci_layout_version_pin
    from oras.defaults import oci_ref_name_annotation
    from oras.layout.layout import Layout
    from oras.provider import Registry
    from oras.schemas import index as index_schema
    from oras.utils.fileio import write_json

    base_image = _normalize_docker_hub(base_image)
    registry = Registry()
    _setup_auth(registry, base_image)
    container = registry.get_container(base_image)

    # Ensure clean layout directory
    if oci_layout_dir.exists():
        shutil.rmtree(oci_layout_dir)
    oci_layout_dir.mkdir(parents=True, exist_ok=True)

    blobs_dir = oci_layout_dir / oci_blobs_dir / "sha256"
    blobs_dir.mkdir(parents=True, exist_ok=True)

    headers = {"Accept": ", ".join(default_manifest_accepted_media_types)}
    manifest_url = f"{registry.prefix}://{container.manifest_url()}"
    response = registry.do_request(manifest_url, "GET", headers=headers)
    registry._check_200_response(response)

    manifest_bytes = response.content
    manifest_digest = response.headers.get("Docker-Content-Digest", container.digest)
    if not manifest_digest:
        raise RuntimeError("Expected Docker-Content-Digest header in manifest response.")

    manifest_data = json.loads(manifest_bytes)
    media_type = manifest_data.get("mediaType", "")

    layout = Layout(path=str(oci_layout_dir), validate=False)

    if media_type == default_manifest_media_type:
        # Single manifest image
        logger.info(
            f"Base image is a single manifest (digest {manifest_digest[:19]}...), " f"no architecture filtering needed"
        )
        layout._pull_manifest_blobs(registry, container, manifest_data, manifest_digest, manifest_bytes)
        index_entry = {
            "mediaType": media_type,
            "digest": manifest_digest,
            "size": len(manifest_bytes),
            "annotations": {oci_ref_name_annotation: "latest"},
        }
    elif media_type == default_index_media_type:
        # Multi-arch index: filter by requested architectures
        available_manifests = manifest_data.get("manifests", [])
        logger.info(
            f"Base image is a multi-arch index with {len(available_manifests)} manifest(s), "
            f"filtering for architectures: {', '.join(architectures)}"
        )
        selected_manifests = []
        for sub_ref in available_manifests:
            platform = sub_ref.get("platform", {})
            arch = platform.get("architecture", "")
            if arch in architectures:
                selected_manifests.append(sub_ref)
                logger.debug(f"Selected manifest for architecture {arch} (digest: {sub_ref['digest'][:19]}...)")

        if not selected_manifests:
            available = [ref.get("platform", {}).get("architecture", "unknown") for ref in available_manifests]
            raise RuntimeError(
                f"No manifest found for requested architectures {architectures}. "
                f"Available architectures: {available}"
            )

        logger.info(f"Building filtered OCI index with {len(selected_manifests)} selected manifest(s)")

        # Build a filtered index
        filtered_index = {
            "schemaVersion": 2,
            "mediaType": default_index_media_type,
            "manifests": selected_manifests,
        }
        index_bytes = json.dumps(filtered_index).encode()
        index_digest = "sha256:" + hashlib.sha256(index_bytes).hexdigest()
        index_path = blobs_dir / index_digest.removeprefix("sha256:")
        index_path.write_bytes(index_bytes)

        # Pull selected manifests
        for sub_ref in selected_manifests:
            sub_digest = sub_ref["digest"]
            sub_media_type = sub_ref.get("mediaType", default_manifest_media_type)
            sub_headers = {"Accept": sub_media_type}
            sub_url = f"{registry.prefix}://{container.registry}/v2/{container.api_prefix}/manifests/{sub_digest}"
            sub_response = registry.do_request(sub_url, "GET", headers=sub_headers)
            registry._check_200_response(sub_response)
            sub_bytes = sub_response.content
            sub_data = json.loads(sub_bytes)
            sub_data_media_type = sub_data.get("mediaType", "")

            arch_label = sub_ref.get("platform", {}).get("architecture", "unknown")
            logger.info(f"Pulling blobs for architecture {arch_label} (digest: {sub_digest[:19]}...)")

            if sub_data_media_type == default_manifest_media_type:
                layout._pull_manifest_blobs(registry, container, sub_data, sub_digest, sub_bytes)
            elif sub_data_media_type == default_index_media_type:
                layout._pull_index_blobs(registry, container, sub_data, sub_digest, sub_bytes)
            else:
                raise ValueError(f"Unsupported manifest mediaType: {sub_data_media_type}")

        index_entry = {
            "mediaType": default_index_media_type,
            "digest": index_digest,
            "size": len(index_bytes),
            "annotations": {oci_ref_name_annotation: "latest"},
        }
    else:
        raise ValueError(f"Unsupported manifest mediaType: {media_type}")

    # Write oci-layout
    write_json(
        {"imageLayoutVersion": oci_layout_version_pin},
        str(oci_layout_dir / oci_layout_file),
    )

    # Write index.json
    index_content = {
        "schemaVersion": 2,
        "manifests": [index_entry],
    }
    import jsonschema

    jsonschema.validate(index_content, schema=index_schema)
    write_json(index_content, str(oci_layout_dir / oci_image_index_file))


def do_build(args: BuildArgs) -> BuildResult:
    """Build an OCI image for the given model using olot.

    Reuses a cached base image OCI layout when available and up-to-date
    to avoid re-downloading the same base image for every model build.
    """
    from olot.basics import oci_layers_on_top

    tag = f"{normalize(args.model)}-modelcar" if args.tag is None else args.tag
    oci_layout_dir = Path("tmp").joinpath(tag)

    logger.info(f"Starting olot build for {args.model} targeting architectures: {', '.join(args.architectures)}")

    # Cache directory for the base image, keyed by the exact image ref + architectures
    cache_dir = Path("tmp").joinpath(
        ".base-image-cache",
        _base_image_cache_key(args.base_image, args.architectures),
    )

    need_pull = True
    if cache_dir.exists() and not args.pull:
        need_pull = False
        logger.info("Cache exists, pull disabled: skipping base image pull")
    elif cache_dir.exists():
        try:
            remote_digest = _remote_manifest_digest(args.base_image)
            cached_digest = _cached_manifest_digest(cache_dir)
            if remote_digest == cached_digest:
                need_pull = False
                logger.info("Base image digest matches cache, reusing cached layout")
            else:
                logger.info(f"Base image {args.base_image} changed, invalidating cache")
                shutil.rmtree(cache_dir)
        except Exception:
            logger.info("Could not verify base image digest, invalidating cache to be safe")
            shutil.rmtree(cache_dir)

    if need_pull:
        logger.info(f"Pulling base image {args.base_image} for architectures: {', '.join(args.architectures)}")
        rprint(f"Pulling base image {args.base_image} into {oci_layout_dir}")
        _pull_base_image(args.base_image, oci_layout_dir, args.architectures)
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
        "name": tag,
        "io.k8s.display-name": tag,
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

    logger.info(f"Adding {len(resolved_model_files)} model file(s) as OCI layers to {oci_layout_dir}")
    rprint(f"Adding {len(resolved_model_files)} model file(s) as OCI layers to {oci_layout_dir}")
    oci_layers_on_top(
        oci_layout_dir,
        resolved_model_files,
        modelcard=resolved_modelcard,
        labels=labels,
        annotations=annotations,
        root_dir=args.model_dir,
    )

    image = _image(args.model, args.repo, tag)
    logger.info(f"Olot build complete: {image} at {oci_layout_dir}")
    return BuildResult(image=image, oci_layout_dir=oci_layout_dir)


def do_push(args: PushArgs) -> None:
    """Push the OCI layout for the given model to the registry using oras-py."""
    tag = f"{normalize(args.model)}-modelcar" if args.tag is None else args.tag
    image = _image(args.model, args.repo, tag)
    logger.info(f"Pushing OCI layout {args.oci_layout_dir} to {image}")
    rprint(f"Pushing {image}")

    if args.oci_layout_dir is None:
        raise RuntimeError("oci_layout_dir is required for olot push")

    from oras.container import Container

    registry = Registry()
    container = Container(image)
    registry.auth.hostname = container.registry

    if args.authfile is not None:
        authfile = str(args.authfile.expanduser().resolve())
        logger.info(f"Loading auth config from {authfile}")

        # Parse the authfile directly—do NOT merge with ~/.docker/config.json.
        with open(authfile) as f:
            auth_data = json.load(f)
        auths = auth_data.get("auths", {})
        matched = False
        for host in (container.registry,):
            entry = auths.get(host)
            if entry:
                auth_b64 = entry.get("auth")
                if auth_b64:
                    user_pass = base64.b64decode(auth_b64).decode("utf-8")
                    username, _, password = user_pass.partition(":")
                    registry.auth.set_basic_auth(username, password)
                    logger.info(f"Loaded credentials for {host}")
                    matched = True
                    break
        if not matched:
            logger.info(f"No {container.registry} entry in {authfile}, relying on default Docker config")
            registry.auth.load_configs(container)
    else:
        logger.info("No authfile provided, relying on default Docker config")
        registry.auth.load_configs(container)

    try:
        layout = NewLayout(str(args.oci_layout_dir))
        layout.push_to_registry(provider=registry, target=image, tag="latest")
        logger.info(f"Push complete: {image}")
    except Exception as e:
        # Provide a clearer error message for auth failures
        error_str = str(e)
        if any(code in error_str for code in ("401", "403", "Unauthorized", "UNAUTHORIZED")):
            authfile_info = f"Provided authfile: {args.authfile}" if args.authfile else "No authfile provided"
            raise RuntimeError(
                f"Registry rejected authentication while pushing {image}. "
                f"{authfile_info}. "
                "Ensure your authfile is a Docker/podman-style config.json with "
                '{"auths":{"<registry>":{"auth":"<base64>"}}} format.'
            ) from e
        raise


def image_exists(model: str, repo: str, tag: str | None = None) -> bool:
    """Return whether the image for the given model and
    image registry repo exists at the remote repository."""
    try:
        from oras.container import Container
        from oras.provider import Registry

        image = _image(model, repo, tag)
        registry = Registry()
        container = Container(image, registry=registry.hostname)
        tags = registry.get_tags(container)
        if tag is None:
            tag = f"{normalize(model)}-modelcar"
        return tag in tags
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
