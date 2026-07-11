import os
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from modelcar_maker.image.olot import do_build
from modelcar_maker.image.olot import do_image_rm
from modelcar_maker.image.olot import do_push
from modelcar_maker.image.olot import image_exists
from modelcar_maker.image.types import BuildArgs
from modelcar_maker.image.types import PushArgs
from modelcar_maker.image.types import RmArgs

BASE_IMAGE = "registry.access.redhat.com/ubi9/ubi-minimal:latest"


@pytest.fixture
def mock_olot_deps():
    with (
        patch("modelcar_maker.image.olot._pull_base_image") as mock_pull,
        patch("modelcar_maker.image.olot._remote_manifest_digest") as mock_digest,
        patch("modelcar_maker.image.olot._cached_manifest_digest") as mock_cached,
        patch("modelcar_maker.image.olot._copy_cached_layout") as mock_copy,
        patch("olot.basics.oci_layers_on_top") as mock_layers,
    ):
        mock_digest.return_value = "sha256:remote123"
        mock_cached.return_value = None
        yield {
            "pull_base_image": mock_pull,
            "remote_digest": mock_digest,
            "cached_digest": mock_cached,
            "copy_cached_layout": mock_copy,
            "oci_layers_on_top": mock_layers,
        }


class TestDoBuild:
    def test_calls_pull_and_layers(self, mock_olot_deps, tmp_path, tmp_model_dir):
        args = BuildArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            model_dir=tmp_model_dir,
            base_image=BASE_IMAGE,
            commit="abc123",
            architectures=["amd64"],
        )
        result = do_build(args)

        expected_layout = Path("tmp/myorg--model-modelcar")
        mock_olot_deps["pull_base_image"].assert_called_once_with(BASE_IMAGE, expected_layout, ["amd64"])
        mock_olot_deps["oci_layers_on_top"].assert_called_once()

        call_kwargs = mock_olot_deps["oci_layers_on_top"].call_args[1]
        assert call_kwargs["labels"]["name"] == "myorg--model-modelcar"
        assert call_kwargs["labels"]["model.name"] == "MyOrg/Model"
        assert call_kwargs["labels"]["model.commit"] == "abc123"
        assert (
            call_kwargs["annotations"]["org.opencontainers.image.source"]
            == "https://github.com/jharmison-redhat/modelcar-maker"
        )
        assert call_kwargs["root_dir"] == tmp_model_dir

        assert result.image == "quay.io/repo:myorg--model-modelcar"
        assert result.oci_layout_dir == expected_layout

    def test_calls_pull_with_multiple_architectures(self, mock_olot_deps, tmp_model_dir):
        args = BuildArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            model_dir=tmp_model_dir,
            base_image=BASE_IMAGE,
            commit="abc123",
            architectures=["amd64", "arm64"],
        )
        result = do_build(args)

        expected_layout = Path("tmp/myorg--model-modelcar")
        mock_olot_deps["pull_base_image"].assert_called_once_with(BASE_IMAGE, expected_layout, ["amd64", "arm64"])
        mock_olot_deps["oci_layers_on_top"].assert_called_once()
        assert result.image == "quay.io/repo:myorg--model-modelcar"
        assert result.oci_layout_dir == expected_layout

    def test_raises_when_no_model_files(self, mock_olot_deps, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        args = BuildArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            model_dir=empty_dir,
            base_image=BASE_IMAGE,
            commit="abc123",
            architectures=["amd64"],
        )
        with pytest.raises(RuntimeError, match="No model files found"):
            do_build(args)

    def test_uses_cache_when_available(self, mock_olot_deps, tmp_model_dir):
        mock_olot_deps["cached_digest"].return_value = "sha256:remote123"
        args = BuildArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            model_dir=tmp_model_dir,
            base_image=BASE_IMAGE,
            commit="abc123",
            architectures=["amd64"],
        )
        result = do_build(args)

        mock_olot_deps["pull_base_image"].assert_not_called()
        mock_olot_deps["oci_layers_on_top"].assert_called_once()
        assert result.oci_layout_dir == Path("tmp/myorg--model-modelcar")


@pytest.fixture
def mock_oras_push():
    """Mock Registry and NewLayout used by do_push."""
    with (
        patch("modelcar_maker.image.olot.Registry") as mock_registry_cls,
        patch("modelcar_maker.image.olot.NewLayout") as mock_layout_cls,
    ):
        mock_registry = MagicMock()
        mock_registry_cls.return_value = mock_registry
        mock_layout_instance = MagicMock()
        mock_layout_cls.return_value = mock_layout_instance
        yield {
            "registry": mock_registry,
            "layout_cls": mock_layout_cls,
            "layout_instance": mock_layout_instance,
        }


class TestDoPush:
    def test_push_without_authfile(self, mock_oras_push):
        layout = Path("tmp/myorg--model")
        args = PushArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            authfile=None,
            oci_layout_dir=layout,
            architectures=["amd64"],
        )
        do_push(args)

        # NewLayout class was instantiated once with the layout dir
        mock_oras_push["layout_cls"].assert_called_once_with("tmp/myorg--model")
        # And push_to_registry was called on the instance
        mock_oras_push["layout_instance"].push_to_registry.assert_called_once_with(
            provider=mock_oras_push["registry"],
            target="quay.io/repo:myorg--model-modelcar",
            tag="latest",
        )
        # load_configs without configs arg
        mock_oras_push["registry"].auth.load_configs.assert_called_once()
        call_args = mock_oras_push["registry"].auth.load_configs.call_args
        assert call_args[1].get("configs") is None

    def test_push_with_authfile(self, mock_oras_push, tmp_path):
        layout = Path("tmp/myorg--model")
        auth = tmp_path / "my-auth.json"
        auth.write_text('{"auths":{"quay.io":{"auth":"YWNtZTpzZWNyZXQ="}}}')

        args = PushArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            authfile=auth,
            oci_layout_dir=layout,
            architectures=["amd64"],
        )
        do_push(args)

        mock_oras_push["layout_cls"].assert_called_once_with("tmp/myorg--model")
        mock_oras_push["layout_instance"].push_to_registry.assert_called_once_with(
            provider=mock_oras_push["registry"],
            target="quay.io/repo:myorg--model-modelcar",
            tag="latest",
        )
        # load_configs should NOT be called when authfile is provided
        mock_oras_push["registry"].auth.load_configs.assert_not_called()
        # Instead, set_basic_auth should be called directly with decoded creds
        mock_oras_push["registry"].auth.set_basic_auth.assert_called_once_with("acme", "secret")

    def test_push_with_authfile_missing_registry_entry(self, mock_oras_push, tmp_path):
        """When the authfile lacks an entry for the target registry, fall back to load_configs."""
        layout = Path("tmp/myorg--model")
        auth = tmp_path / "my-auth.json"
        auth.write_text('{"auths":{"docker.io":{"auth":"YWNtZTpzZWNyZXQ="}}}')

        args = PushArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            authfile=auth,
            oci_layout_dir=layout,
            architectures=["amd64"],
        )
        do_push(args)

        # No matching registry entry → falls back to load_configs
        mock_oras_push["registry"].auth.load_configs.assert_called_once()
        mock_oras_push["registry"].auth.set_basic_auth.assert_not_called()

    def test_raises_without_layout_dir(self):
        args = PushArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            authfile=None,
            oci_layout_dir=None,
            architectures=["amd64"],
        )
        with pytest.raises(RuntimeError, match="oci_layout_dir is required"):
            do_push(args)

    def test_auth_failure_raises_clear_error(self, mock_oras_push, tmp_path):
        mock_oras_push["layout_instance"].push_to_registry.side_effect = Exception(
            "401 Unauthorized: access to the requested resource is not authorized"
        )

        layout = Path("tmp/myorg--model")
        auth = tmp_path / "my-auth.json"
        auth.write_text('{"auths":{"quay.io":{"auth":"YWNtZTpzZWNyZXQ="}}}')
        args = PushArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            authfile=auth,
            oci_layout_dir=layout,
            architectures=["amd64"],
        )
        with pytest.raises(RuntimeError, match="Registry rejected authentication"):
            do_push(args)


class TestDoImageRm:
    def test_removes_layout_dir(self):
        layout = Path("tmp/myorg--model")
        args = RmArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            oci_layout_dir=layout,
            architectures=["amd64"],
        )
        with patch("modelcar_maker.image.olot.shutil.rmtree") as mock_rmtree:
            result = do_image_rm(args)
            assert result is True
            mock_rmtree.assert_called_once_with(layout)

    def test_returns_false_on_failure(self):
        args = RmArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            oci_layout_dir=Path("tmp/myorg--model"),
            architectures=["amd64"],
        )
        with patch("modelcar_maker.image.olot.shutil.rmtree", side_effect=OSError("permission denied")):
            result = do_image_rm(args)
            assert result is False

    def test_raises_without_layout_dir(self):
        args = RmArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            oci_layout_dir=None,
            architectures=["amd64"],
        )
        with pytest.raises(RuntimeError, match="oci_layout_dir is required"):
            do_image_rm(args)
