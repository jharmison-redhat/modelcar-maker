from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from modelcar_maker.image.olot import _authfile_env
from modelcar_maker.image.olot import do_build
from modelcar_maker.image.olot import do_image_rm
from modelcar_maker.image.olot import do_push
from modelcar_maker.image.olot import image_exists
from modelcar_maker.image.types import BuildArgs
from modelcar_maker.image.types import PushArgs
from modelcar_maker.image.types import RmArgs


class TestAuthfileEnv:
    def test_returns_none_without_authfile(self):
        assert _authfile_env(None) is None

    def test_returns_docker_config_with_authfile(self):
        auth = Path("/secrets/auth.json")
        result = _authfile_env(auth)
        assert result == {"DOCKER_CONFIG": "/secrets"}

    def test_warns_when_authfile_missing(self, caplog):
        auth = Path("/nonexistent/auth.json")
        assert _authfile_env(auth) == {"DOCKER_CONFIG": "/nonexistent"}


BASE_IMAGE = "registry.access.redhat.com/ubi9/ubi-minimal:latest"


@pytest.fixture
def mock_olot_deps():
    with (
        patch("olot.backend.oras_py.oras_py_pull") as mock_pull,
        patch("olot.basics.oci_layers_on_top") as mock_layers,
        patch("modelcar_maker.image.olot.shutil.rmtree") as mock_rmtree,
    ):
        yield {
            "oras_py_pull": mock_pull,
            "oci_layers_on_top": mock_layers,
            "shutil_rmtree": mock_rmtree,
        }


class TestDoBuild:
    def test_calls_pull_and_layers(self, mock_olot_deps, tmp_path, tmp_model_dir):
        args = BuildArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            model_dir=tmp_model_dir,
            base_image=BASE_IMAGE,
            commit="abc123",
        )
        result = do_build(args)

        expected_layout = Path("tmp/myorg--model")
        mock_olot_deps["oras_py_pull"].assert_called_once_with(BASE_IMAGE, expected_layout)
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

    def test_raises_when_no_model_files(self, mock_olot_deps, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        args = BuildArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            model_dir=empty_dir,
            base_image=BASE_IMAGE,
            commit="abc123",
        )
        with pytest.raises(RuntimeError, match="No model files found"):
            do_build(args)


@pytest.fixture
def mock_olot_push():
    with patch("olot.backend.oras_py.oras_py_push") as m:
        yield m


class TestDoPush:
    def test_push_with_oci_layout(self, mock_olot_push):
        layout = Path("tmp/myorg--model")
        args = PushArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            authfile=None,
            oci_layout_dir=layout,
        )
        do_push(args)
        mock_olot_push.assert_called_once_with(layout, "quay.io/repo:myorg--model-modelcar")

    def test_push_with_authfile(self, mock_olot_push, monkeypatch):
        layout = Path("tmp/myorg--model")
        auth = Path("/secrets/auth.json")
        monkeypatch.setenv("DOCKER_CONFIG", "/existing")
        args = PushArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            authfile=auth,
            oci_layout_dir=layout,
        )
        do_push(args)

        mock_olot_push.assert_called_once()
        assert Path("/secrets/auth.json").parent == Path("/secrets")

    def test_raises_without_layout_dir(self):
        args = PushArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            authfile=None,
            oci_layout_dir=None,
        )
        with pytest.raises(RuntimeError, match="oci_layout_dir is required"):
            do_push(args)


class TestImageExists:
    @patch("oras.provider.Registry")
    def test_tag_present(self, mock_registry_cls):
        mock_registry = MagicMock()
        mock_registry.hostname = "quay.io"
        mock_registry.get_tags.return_value = ["myorg--model-modelcar", "other"]
        mock_registry_cls.return_value = mock_registry

        assert image_exists("MyOrg/Model", "quay.io/repo") is True
        mock_registry.get_tags.assert_called_once()

    @patch("oras.provider.Registry")
    def test_tag_missing(self, mock_registry_cls):
        mock_registry = MagicMock()
        mock_registry.hostname = "quay.io"
        mock_registry.get_tags.return_value = ["other"]
        mock_registry_cls.return_value = mock_registry

        assert image_exists("MyOrg/Model", "quay.io/repo") is False

    @patch("oras.provider.Registry")
    def test_returns_false_on_exception(self, mock_registry_cls):
        mock_registry_cls.side_effect = Exception("network error")

        assert image_exists("MyOrg/Model", "quay.io/repo") is False


class TestDoImageRm:
    def test_removes_layout_dir(self, mock_olot_deps):
        layout = Path("tmp/myorg--model")
        args = RmArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            oci_layout_dir=layout,
        )
        result = do_image_rm(args)
        assert result is True
        mock_olot_deps["shutil_rmtree"].assert_called_once_with(layout)

    def test_returns_false_on_failure(self, mock_olot_deps):
        mock_olot_deps["shutil_rmtree"].side_effect = OSError("permission denied")
        args = RmArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            oci_layout_dir=Path("tmp/myorg--model"),
        )
        result = do_image_rm(args)
        assert result is False

    def test_raises_without_layout_dir(self):
        args = RmArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            oci_layout_dir=None,
        )
        with pytest.raises(RuntimeError, match="oci_layout_dir is required"):
            do_image_rm(args)
