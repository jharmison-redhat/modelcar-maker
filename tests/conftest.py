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


def pytest_collection_modifyitems(config, items):
    """Skip slow tests by default unless a -m marker expression is explicitly given."""
    option_markexpr = getattr(config.option, "markexpr", None)
    if option_markexpr:
        return

    skip_slow = pytest.mark.skip(reason="slow test: use -m 'slow or not slow' to run")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
