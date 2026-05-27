from pathlib import Path
from unittest.mock import patch

import pytest

from modelcar_maker import ProcessResult
from modelcar_maker import cleanup
from modelcar_maker import process
from modelcar_maker.image.types import BuildArgs
from modelcar_maker.image.types import BuildResult
from modelcar_maker.image.types import PushArgs
from modelcar_maker.image.types import RmArgs


@pytest.fixture
def mock_deps():
    """Return mocks for podman backend used in process tests."""
    mod = "modelcar_maker.image.podman"
    with (
        patch("modelcar_maker.hf_download") as mock_hf,
        patch(f"{mod}.do_build") as mock_build,
        patch(f"{mod}.do_push") as mock_push,
        patch(f"{mod}.do_image_rm") as mock_rm,
        patch(f"{mod}.image_exists") as mock_exists,
        patch("modelcar_maker.cleanup") as mock_cleanup,
    ):
        download_dir = Path("models/myorg--mymodel")
        mock_hf.return_value = (download_dir, "commit123")
        mock_build.return_value = BuildResult(image="quay.io/repo:myorg--mymodel-modelcar")
        mock_exists.return_value = False
        mock_rm.return_value = True
        mock_cleanup.return_value = True

        yield {
            "hf_download": mock_hf,
            "do_build": mock_build,
            "do_push": mock_push,
            "do_image_rm": mock_rm,
            "image_exists": mock_exists,
            "cleanup": mock_cleanup,
            "download_dir": download_dir,
        }


class TestProcessSkipPodman:
    def test_skip_when_exists(self, mock_deps):
        mock_deps["image_exists"].return_value = True
        result = process("MyOrg/MyModel", "quay.io/repo", backend="podman", skip_if_exists=True)

        assert result.skipped is True
        assert result.image_built is False
        mock_deps["hf_download"].assert_not_called()

    def test_skip_with_image_cleanup(self, mock_deps):
        mock_deps["image_exists"].return_value = True
        result = process(
            "MyOrg/MyModel",
            "quay.io/repo",
            backend="podman",
            skip_if_exists=True,
            image_cleanup=True,
        )
        assert result.skipped is True
        assert result.image_cleaned_up is True
        call_args = mock_deps["do_image_rm"].call_args[0][0]
        assert isinstance(call_args, RmArgs)
        assert call_args.model == "MyOrg/MyModel"
        assert call_args.repo == "quay.io/repo"
        assert call_args.oci_layout_dir is None

    def test_skip_with_model_cleanup(self, mock_deps):
        mock_deps["image_exists"].return_value = True
        result = process(
            "MyOrg/MyModel",
            "quay.io/repo",
            backend="podman",
            skip_if_exists=True,
            model_cleanup=True,
        )
        assert result.skipped is True
        assert result.model_cleaned_up is True
        mock_deps["cleanup"].assert_called_once()

    def test_skip_no_cleanup(self, mock_deps):
        mock_deps["image_exists"].return_value = True
        result = process(
            "MyOrg/MyModel",
            "quay.io/repo",
            backend="podman",
            skip_if_exists=True,
            image_cleanup=False,
            model_cleanup=False,
        )
        assert result.image_cleaned_up is False
        assert result.model_cleaned_up is False


class TestProcessBuildPodman:
    def test_full_build_and_push(self, mock_deps):
        result = process("MyOrg/MyModel", "quay.io/repo", backend="podman", push=True)

        assert result.skipped is False
        assert result.image_built is True
        assert result.image_pushed is True
        assert result.image == "quay.io/repo:myorg--mymodel-modelcar"
        assert result.downloaded_to == mock_deps["download_dir"]

        mock_deps["hf_download"].assert_called_once_with("MyOrg/MyModel")
        build_call = mock_deps["do_build"].call_args[0][0]
        assert isinstance(build_call, BuildArgs)
        assert build_call.model == "MyOrg/MyModel"
        assert build_call.repo == "quay.io/repo"
        assert build_call.model_dir == mock_deps["download_dir"]
        assert build_call.commit == "commit123"

        push_call = mock_deps["do_push"].call_args[0][0]
        assert isinstance(push_call, PushArgs)
        assert push_call.model == "MyOrg/MyModel"
        assert push_call.repo == "quay.io/repo"
        assert push_call.authfile is None
        assert push_call.oci_layout_dir is None

    def test_build_no_push(self, mock_deps):
        result = process("MyOrg/MyModel", "quay.io/repo", backend="podman", push=False)

        assert result.image_built is True
        assert result.image_pushed is False
        mock_deps["do_push"].assert_not_called()

    def test_build_with_authfile(self, mock_deps):
        auth = Path("/auth.json")
        process("MyOrg/MyModel", "quay.io/repo", backend="podman", push=True, authfile=auth)
        push_call = mock_deps["do_push"].call_args[0][0]
        assert isinstance(push_call, PushArgs)
        assert push_call.authfile == auth

    def test_image_cleanup_after_push(self, mock_deps):
        # image_exists must be True for cleanup to trigger
        mock_deps["image_exists"].side_effect = [False, True]
        result = process(
            "MyOrg/MyModel",
            "quay.io/repo",
            backend="podman",
            push=True,
            image_cleanup=True,
        )
        assert result.image_cleaned_up is True
        mock_deps["do_image_rm"].assert_called_once()

    def test_image_cleanup_skipped_if_not_exists(self, mock_deps):
        mock_deps["image_exists"].side_effect = [False, False]
        result = process(
            "MyOrg/MyModel",
            "quay.io/repo",
            backend="podman",
            push=True,
            image_cleanup=True,
        )
        assert result.image_cleaned_up is False

    def test_model_cleanup(self, mock_deps):
        result = process(
            "MyOrg/MyModel",
            "quay.io/repo",
            backend="podman",
            push=False,
            model_cleanup=True,
        )
        assert result.model_cleaned_up is True
        mock_deps["cleanup"].assert_called_once_with(mock_deps["download_dir"])

    def test_no_model_cleanup(self, mock_deps):
        result = process(
            "MyOrg/MyModel",
            "quay.io/repo",
            backend="podman",
            push=False,
            model_cleanup=False,
        )
        assert result.model_cleaned_up is False
        mock_deps["cleanup"].assert_not_called()

    def test_skip_if_exists_false(self, mock_deps):
        mock_deps["image_exists"].return_value = True
        result = process("MyOrg/MyModel", "quay.io/repo", backend="podman", skip_if_exists=False)
        assert result.skipped is False
        assert result.image_built is True


@pytest.fixture
def mock_olot_deps():
    """Return mocks for olot backend used in process tests."""
    with (
        patch("modelcar_maker.image.olot.do_build") as mock_build,
        patch("modelcar_maker.image.olot.do_push") as mock_push,
        patch("modelcar_maker.image.olot.do_image_rm") as mock_rm,
        patch("modelcar_maker.image.olot.image_exists") as mock_exists,
        patch("modelcar_maker.hf_download") as mock_hf,
        patch("modelcar_maker.cleanup") as mock_cleanup,
    ):
        download_dir = Path("models/myorg--mymodel")
        mock_hf.return_value = (download_dir, "commit123")
        mock_build.return_value = BuildResult(
            image="quay.io/repo:myorg--mymodel-modelcar",
            oci_layout_dir=Path("tmp/myorg--mymodel"),
        )
        mock_exists.return_value = False
        mock_rm.return_value = True
        mock_cleanup.return_value = True

        yield {
            "hf_download": mock_hf,
            "do_build": mock_build,
            "do_push": mock_push,
            "do_image_rm": mock_rm,
            "image_exists": mock_exists,
            "cleanup": mock_cleanup,
            "download_dir": download_dir,
        }


class TestProcessSkipOlot:
    def test_skip_when_exists(self, mock_olot_deps):
        mock_olot_deps["image_exists"].return_value = True
        result = process("MyOrg/MyModel", "quay.io/repo", backend="olot", skip_if_exists=True)

        assert result.skipped is True
        assert result.image_built is False
        mock_olot_deps["hf_download"].assert_not_called()

    def test_skip_with_image_cleanup(self, mock_olot_deps):
        mock_olot_deps["image_exists"].return_value = True
        result = process(
            "MyOrg/MyModel",
            "quay.io/repo",
            backend="olot",
            skip_if_exists=True,
            image_cleanup=True,
        )
        assert result.skipped is True
        assert result.image_cleaned_up is True
        call_args = mock_olot_deps["do_image_rm"].call_args[0][0]
        assert isinstance(call_args, RmArgs)
        assert call_args.model == "MyOrg/MyModel"
        assert call_args.repo == "quay.io/repo"
        assert call_args.oci_layout_dir == Path("tmp/myorg--mymodel")

    def test_skip_with_model_cleanup(self, mock_olot_deps):
        mock_olot_deps["image_exists"].return_value = True
        result = process(
            "MyOrg/MyModel",
            "quay.io/repo",
            backend="olot",
            skip_if_exists=True,
            model_cleanup=True,
        )
        assert result.skipped is True
        assert result.model_cleaned_up is True
        mock_olot_deps["cleanup"].assert_called_once()

    def test_skip_no_cleanup(self, mock_olot_deps):
        mock_olot_deps["image_exists"].return_value = True
        result = process(
            "MyOrg/MyModel",
            "quay.io/repo",
            backend="olot",
            skip_if_exists=True,
            image_cleanup=False,
            model_cleanup=False,
        )
        assert result.image_cleaned_up is False
        assert result.model_cleaned_up is False


class TestProcessBuildOlot:
    def test_full_build_and_push(self, mock_olot_deps):
        result = process("MyOrg/MyModel", "quay.io/repo", backend="olot", push=True)

        assert result.skipped is False
        assert result.image_built is True
        assert result.image_pushed is True
        assert result.image == "quay.io/repo:myorg--mymodel-modelcar"
        assert result.downloaded_to == mock_olot_deps["download_dir"]

        mock_olot_deps["hf_download"].assert_called_once_with("MyOrg/MyModel")
        build_call = mock_olot_deps["do_build"].call_args[0][0]
        assert isinstance(build_call, BuildArgs)
        assert build_call.model == "MyOrg/MyModel"
        assert build_call.repo == "quay.io/repo"
        assert build_call.model_dir == mock_olot_deps["download_dir"]
        assert build_call.commit == "commit123"

        push_call = mock_olot_deps["do_push"].call_args[0][0]
        assert isinstance(push_call, PushArgs)
        assert push_call.model == "MyOrg/MyModel"
        assert push_call.repo == "quay.io/repo"
        assert push_call.authfile is None
        assert push_call.oci_layout_dir == Path("tmp/myorg--mymodel")

    def test_build_no_push(self, mock_olot_deps):
        result = process("MyOrg/MyModel", "quay.io/repo", backend="olot", push=False)

        assert result.image_built is True
        assert result.image_pushed is False
        mock_olot_deps["do_push"].assert_not_called()

    def test_build_with_authfile(self, mock_olot_deps):
        auth = Path("/auth.json")
        process("MyOrg/MyModel", "quay.io/repo", backend="olot", push=True, authfile=auth)
        push_call = mock_olot_deps["do_push"].call_args[0][0]
        assert isinstance(push_call, PushArgs)
        assert push_call.authfile == auth

    def test_image_cleanup(self, mock_olot_deps):
        result = process(
            "MyOrg/MyModel",
            "quay.io/repo",
            backend="olot",
            push=True,
            image_cleanup=True,
        )
        assert result.image_cleaned_up is True
        mock_olot_deps["do_image_rm"].assert_called_once()

    def test_image_cleanup_no_push(self, mock_olot_deps):
        result = process(
            "MyOrg/MyModel",
            "quay.io/repo",
            backend="olot",
            push=False,
            image_cleanup=True,
        )
        assert result.image_cleaned_up is True
        mock_olot_deps["do_image_rm"].assert_called_once()

    def test_model_cleanup(self, mock_olot_deps):
        result = process(
            "MyOrg/MyModel",
            "quay.io/repo",
            backend="olot",
            push=False,
            model_cleanup=True,
        )
        assert result.model_cleaned_up is True
        mock_olot_deps["cleanup"].assert_called_once_with(mock_olot_deps["download_dir"])

    def test_no_model_cleanup(self, mock_olot_deps):
        result = process(
            "MyOrg/MyModel",
            "quay.io/repo",
            backend="olot",
            push=False,
            model_cleanup=False,
        )
        assert result.model_cleaned_up is False
        mock_olot_deps["cleanup"].assert_not_called()

    def test_skip_if_exists_false(self, mock_olot_deps):
        mock_olot_deps["image_exists"].return_value = True
        result = process("MyOrg/MyModel", "quay.io/repo", backend="olot", skip_if_exists=False)
        assert result.skipped is False
        assert result.image_built is True
