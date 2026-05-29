from pathlib import Path
from unittest.mock import patch

import pytest

from modelcar_maker.download.hf_download import hf_download


class TestHfDownload:
    @patch("huggingface_hub.HfApi")
    @patch("modelcar_maker.download.hf_download.snapshot_download")
    def test_downloads_to_models_dir(self, mock_snapshot, mock_api_cls, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        mock_api = mock_api_cls.return_value
        mock_api.model_info.return_value.sha = "deadbeef"

        def fake_snapshot(repo_id, local_dir, tqdm_class):
            Path(local_dir).mkdir(parents=True, exist_ok=True)

        mock_snapshot.side_effect = fake_snapshot

        download_dir, commit = hf_download("MyOrg/MyModel")
        assert "models" in str(download_dir)
        assert "myorg--mymodel" in str(download_dir)
        assert commit == "deadbeef"
        mock_snapshot.assert_called_once()

    @patch("huggingface_hub.HfApi")
    @patch("modelcar_maker.download.hf_download.snapshot_download")
    def test_extracts_commit_from_api(self, mock_snapshot, mock_api_cls, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        mock_api = mock_api_cls.return_value
        mock_api.model_info.return_value.sha = "abc1234"

        def fake_snapshot(repo_id, local_dir, tqdm_class):
            Path(local_dir).mkdir(parents=True, exist_ok=True)

        mock_snapshot.side_effect = fake_snapshot

        _, commit = hf_download("Org/Model")
        assert commit == "abc1234"

    @patch("huggingface_hub.HfApi")
    @patch("modelcar_maker.download.hf_download.snapshot_download")
    def test_asserts_when_api_returns_none(self, mock_snapshot, mock_api_cls, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        mock_api = mock_api_cls.return_value
        mock_api.model_info.return_value.sha = None

        def fake_snapshot(repo_id, local_dir, tqdm_class):
            Path(local_dir).mkdir(parents=True, exist_ok=True)

        mock_snapshot.side_effect = fake_snapshot

        with pytest.raises(AssertionError):
            hf_download("Org/Model")
