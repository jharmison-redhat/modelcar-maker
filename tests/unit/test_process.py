from pathlib import Path
from unittest.mock import patch

import pytest

from modelcar_maker import ProcessResult
from modelcar_maker import cleanup
from modelcar_maker import process


@pytest.fixture
def mock_deps():
    """Return a context manager that mocks all process dependencies."""
    with (
        patch("modelcar_maker.hf_download") as mock_hf,
        patch("modelcar_maker.render") as mock_render,
        patch("modelcar_maker.do_build") as mock_build,
        patch("modelcar_maker.do_push") as mock_push,
        patch("modelcar_maker.do_image_rm") as mock_rm,
        patch("modelcar_maker.image_exists") as mock_exists,
        patch("modelcar_maker.cleanup") as mock_cleanup,
    ):
        download_dir = Path("models/myorg--mymodel")
        mock_hf.return_value = (download_dir, "commit123")
        mock_build.return_value = "quay.io/repo:myorg--mymodel-modelcar"
        mock_exists.return_value = False
        mock_rm.return_value = True
        mock_cleanup.return_value = True

        yield {
            "hf_download": mock_hf,
            "render": mock_render,
            "do_build": mock_build,
            "do_push": mock_push,
            "do_image_rm": mock_rm,
            "image_exists": mock_exists,
            "cleanup": mock_cleanup,
            "download_dir": download_dir,
        }


class TestProcessSkip:
    def test_skip_when_exists(self, mock_deps):
        mock_deps["image_exists"].return_value = True
        result = process("MyOrg/MyModel", "quay.io/repo", skip_if_exists=True)

        assert result.skipped is True
        assert result.image_built is False
        mock_deps["hf_download"].assert_not_called()

    def test_skip_with_image_cleanup(self, mock_deps):
        mock_deps["image_exists"].return_value = True
        result = process(
            "MyOrg/MyModel",
            "quay.io/repo",
            skip_if_exists=True,
            image_cleanup=True,
        )
        assert result.skipped is True
        assert result.image_cleaned_up is True
        mock_deps["do_image_rm"].assert_called_once_with("MyOrg/MyModel", "quay.io/repo")

    def test_skip_with_model_cleanup(self, mock_deps):
        mock_deps["image_exists"].return_value = True
        result = process(
            "MyOrg/MyModel",
            "quay.io/repo",
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
            skip_if_exists=True,
            image_cleanup=False,
            model_cleanup=False,
        )
        assert result.image_cleaned_up is False
        assert result.model_cleaned_up is False


class TestProcessBuild:
    def test_full_build_and_push(self, mock_deps):
        result = process("MyOrg/MyModel", "quay.io/repo", push=True)

        assert result.skipped is False
        assert result.image_built is True
        assert result.image_pushed is True
        assert result.image == "quay.io/repo:myorg--mymodel-modelcar"
        assert result.downloaded_to == mock_deps["download_dir"]

        mock_deps["hf_download"].assert_called_once_with("MyOrg/MyModel")
        mock_deps["render"].assert_called_once_with("MyOrg/MyModel", mock_deps["download_dir"], "commit123")
        mock_deps["do_build"].assert_called_once_with("MyOrg/MyModel", "quay.io/repo", mock_deps["download_dir"])
        mock_deps["do_push"].assert_called_once_with("MyOrg/MyModel", "quay.io/repo", None)

    def test_build_no_push(self, mock_deps):
        result = process("MyOrg/MyModel", "quay.io/repo", push=False)

        assert result.image_built is True
        assert result.image_pushed is False
        mock_deps["do_push"].assert_not_called()

    def test_build_with_authfile(self, mock_deps):
        auth = Path("/auth.json")
        process("MyOrg/MyModel", "quay.io/repo", push=True, authfile=auth)
        mock_deps["do_push"].assert_called_once_with("MyOrg/MyModel", "quay.io/repo", auth)

    def test_image_cleanup_after_push(self, mock_deps):
        # image_exists must be True for cleanup to trigger
        mock_deps["image_exists"].side_effect = [False, True]
        result = process(
            "MyOrg/MyModel",
            "quay.io/repo",
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
            push=True,
            image_cleanup=True,
        )
        assert result.image_cleaned_up is False

    def test_model_cleanup(self, mock_deps):
        result = process(
            "MyOrg/MyModel",
            "quay.io/repo",
            push=False,
            model_cleanup=True,
        )
        assert result.model_cleaned_up is True
        mock_deps["cleanup"].assert_called_once_with(mock_deps["download_dir"])

    def test_no_model_cleanup(self, mock_deps):
        result = process(
            "MyOrg/MyModel",
            "quay.io/repo",
            push=False,
            model_cleanup=False,
        )
        assert result.model_cleaned_up is False
        mock_deps["cleanup"].assert_not_called()

    def test_skip_if_exists_false(self, mock_deps):
        mock_deps["image_exists"].return_value = True
        result = process("MyOrg/MyModel", "quay.io/repo", skip_if_exists=False)
        assert result.skipped is False
        assert result.image_built is True
