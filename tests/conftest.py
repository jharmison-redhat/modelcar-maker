import json
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest


@pytest.fixture
def tmp_model_dir(tmp_path):
    """Create a temporary model directory with representative files."""
    d = tmp_path / "models" / "test-org--test-model"
    d.mkdir(parents=True)
    (d / "config.json").write_text("{}")
    (d / "model.safetensors").write_text("")
    (d / "README.md").write_text("# Test Model")
    (d / ".hidden").write_text("")
    (d / "Containerfile").write_text("FROM test")
    (d / "original").write_text("")
    (d / "consolidated.safetensors").write_text("")
    return d


@pytest.fixture
def mock_popen():
    """Patch subprocess.Popen and return the mock class."""
    with patch("modelcar_maker.image.podman.subprocess.Popen") as m:
        mock_proc = MagicMock()
        mock_proc.stdout.readline.return_value = b""
        mock_proc.wait.return_value = 0
        m.return_value = mock_proc
        yield m, mock_proc


@pytest.fixture
def image_search_json():
    """Return the podman search JSON that includes our tag."""
    return json.dumps(
        [
            {
                "Name": "quay.io/repo",
                "Tags": ["testorg--testmodel-modelcar", "other"],
            }
        ]
    )


@pytest.fixture
def image_search_json_missing():
    """Return the podman search JSON that does NOT include our tag."""
    return json.dumps(
        [
            {
                "Name": "quay.io/repo",
                "Tags": ["other"],
            }
        ]
    )
