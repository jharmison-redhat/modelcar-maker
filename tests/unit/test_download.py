import os
from pathlib import Path
from unittest.mock import patch

import pytest

from modelcar_maker.download.hf_download import hf_download


class TestHfDownload:
    @patch("modelcar_maker.download.hf_download.snapshot_download")
    def test_downloads_to_models_dir(self, mock_snapshot, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        def fake_snapshot(repo_id, local_dir, tqdm_class):
            local_dir = Path(local_dir)
            local_dir.mkdir(parents=True, exist_ok=True)
            cache = local_dir / ".cache" / "huggingface" / "download" / "sub"
            cache.mkdir(parents=True)
            (cache / "file.metadata").write_text("deadbeef\n")

        mock_snapshot.side_effect = fake_snapshot

        download_dir, commit = hf_download("MyOrg/MyModel")

        assert "models" in str(download_dir)
        assert "myorg--mymodel" in str(download_dir)
        assert commit == "deadbeef"
        mock_snapshot.assert_called_once()

    @patch("modelcar_maker.download.hf_download.snapshot_download")
    def test_extracts_commit_from_metadata(self, mock_snapshot, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        def fake_snapshot(repo_id, local_dir, tqdm_class):
            local_dir = Path(local_dir)
            local_dir.mkdir(parents=True, exist_ok=True)
            cache = local_dir / ".cache" / "huggingface" / "download"
            cache.mkdir(parents=True)
            (cache / "blob.metadata").write_text("abc1234\n")

        mock_snapshot.side_effect = fake_snapshot
        _, commit = hf_download("Org/Model")
        assert commit == "abc1234"

    @patch("modelcar_maker.download.hf_download.snapshot_download")
    def test_asserts_when_no_metadata(self, mock_snapshot, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        def fake_snapshot(repo_id, local_dir, tqdm_class):
            Path(local_dir).mkdir(parents=True, exist_ok=True)

        mock_snapshot.side_effect = fake_snapshot
        with pytest.raises(AssertionError):
            hf_download("Org/Model")
