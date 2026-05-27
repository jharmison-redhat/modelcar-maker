from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from modelcar_maker.cli.cli import cli
from modelcar_maker.image.types import Backend

runner = CliRunner()


class TestBuildCommand:
    @patch("modelcar_maker.cli.cli.process")
    def test_single_model(self, mock_process):
        mock_process.return_value.skipped = False
        mock_process.return_value.image_built = True
        mock_process.return_value.image_pushed = True
        mock_process.return_value.downloaded_to = "models/a--b"
        mock_process.return_value.image = "quay.io/repo:a--b-modelcar"
        mock_process.return_value.image_cleaned_up = False
        mock_process.return_value.model_cleaned_up = False

        result = runner.invoke(cli, ["MyOrg/MyModel"])
        assert result.exit_code == 0
        mock_process.assert_called_once_with(
            "MyOrg/MyModel",
            "quay.io/jharmison/models",
            authfile=None,
            push=True,
            image_cleanup=False,
            model_cleanup=False,
            skip_if_exists=True,
            backend=Backend.PODMAN,
            base_image="registry.access.redhat.com/ubi10/ubi-micro:10.2",
        )

    @patch("modelcar_maker.cli.cli.process")
    def test_no_push(self, mock_process):
        mock_process.return_value.skipped = False
        mock_process.return_value.image_built = True
        mock_process.return_value.image_pushed = False
        mock_process.return_value.downloaded_to = "models/a--b"
        mock_process.return_value.image = "quay.io/repo:a--b-modelcar"
        mock_process.return_value.image_cleaned_up = False
        mock_process.return_value.model_cleaned_up = False

        result = runner.invoke(cli, ["MyOrg/MyModel", "--no-push"])
        assert result.exit_code == 0
        mock_process.assert_called_once()
        _, kwargs = mock_process.call_args
        assert kwargs["push"] is False

    @patch("modelcar_maker.cli.cli.process")
    def test_image_cleanup(self, mock_process):
        mock_process.return_value.skipped = False
        mock_process.return_value.image_built = True
        mock_process.return_value.image_pushed = True
        mock_process.return_value.downloaded_to = "models/a--b"
        mock_process.return_value.image = "quay.io/repo:a--b-modelcar"
        mock_process.return_value.image_cleaned_up = True
        mock_process.return_value.model_cleaned_up = True

        result = runner.invoke(cli, ["MyOrg/MyModel", "--image-clean-up", "--model-clean-up"])
        assert result.exit_code == 0
        _, kwargs = mock_process.call_args
        assert kwargs["image_cleanup"] is True
        assert kwargs["model_cleanup"] is True

    @patch("modelcar_maker.cli.cli.process")
    def test_authfile(self, mock_process):
        mock_process.return_value.skipped = False
        mock_process.return_value.image_built = True
        mock_process.return_value.image_pushed = True
        mock_process.return_value.downloaded_to = "models/a--b"
        mock_process.return_value.image = "quay.io/repo:a--b-modelcar"
        mock_process.return_value.image_cleaned_up = False
        mock_process.return_value.model_cleaned_up = False

        result = runner.invoke(cli, ["MyOrg/MyModel", "-a", "/auth.json"])
        assert result.exit_code == 0
        _, kwargs = mock_process.call_args
        assert str(kwargs["authfile"]) == "/auth.json"

    @patch("modelcar_maker.cli.cli.process")
    def test_invalid_backend(self, mock_process):
        result = runner.invoke(cli, ["MyOrg/MyModel", "--backend", "docker"])
        assert result.exit_code != 0
        mock_process.assert_not_called()

    @patch("modelcar_maker.cli.cli.process")
    def test_skip_if_exists(self, mock_process):
        mock_process.return_value.skipped = True
        mock_process.return_value.image_cleaned_up = False
        mock_process.return_value.model_cleaned_up = False

        result = runner.invoke(cli, ["MyOrg/MyModel", "--no-skip-if-exists"])
        assert result.exit_code == 0
        _, kwargs = mock_process.call_args
        assert kwargs["skip_if_exists"] is False
