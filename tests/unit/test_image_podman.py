import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from modelcar_maker.image.podman import do_build
from modelcar_maker.image.podman import do_image_rm
from modelcar_maker.image.podman import do_push
from modelcar_maker.image.podman import image_exists
from modelcar_maker.image.podman import podman
from modelcar_maker.image.types import BuildArgs
from modelcar_maker.image.types import PushArgs
from modelcar_maker.image.types import RmArgs


class TestPodmanGeneric:
    def test_build_appends_dot(self, mock_popen):
        m, _ = mock_popen
        podman("build")
        assert m.call_args[0][0][2] == "."

    def test_other_commands_no_dot(self, mock_popen):
        m, _ = mock_popen
        podman("push", args=["img:tag"])
        assert "." not in m.call_args[0][0]

    def test_context_dir_sets_cwd(self, mock_popen, tmp_path):
        m, _ = mock_popen
        podman("build", context_dir=tmp_path)
        assert m.call_args[1]["cwd"] == tmp_path

    def test_failure_raises(self, mock_popen):
        m, proc = mock_popen
        proc.wait.return_value = 1
        with pytest.raises(RuntimeError, match="failed with code 1"):
            podman("build")

    def test_output_joined(self, mock_popen):
        m, proc = mock_popen
        proc.stdout.readline.side_effect = [b"line1\n", b"line2\n", b""]
        out = podman("build")
        assert out == "line1\nline2"


BASE_IMAGE = "registry.access.redhat.com/ubi9/ubi-minimal:latest"


class TestDoBuild:
    def test_calls_podman_build_with_tag(self, mock_popen, tmp_path):
        args = BuildArgs(
            model="MyOrg/Model",
            repo="quay.io/repo",
            model_dir=tmp_path,
            base_image=BASE_IMAGE,
            commit="abc123",
        )
        result = do_build(args)
        m, _ = mock_popen
        argv = m.call_args[0][0]
        assert argv == ["podman", "build", ".", "-t", "quay.io/repo:myorg--model-modelcar"]
        assert m.call_args[1]["cwd"] == tmp_path
        assert result.image == "quay.io/repo:myorg--model-modelcar"
        assert result.oci_layout_dir is None


class TestDoPush:
    def test_push_without_authfile(self, mock_popen):
        args = PushArgs(model="MyOrg/Model", repo="quay.io/repo", authfile=None)
        do_push(args)
        m, _ = mock_popen
        argv = m.call_args[0][0]
        assert argv == ["podman", "push", "quay.io/repo:myorg--model-modelcar"]

    def test_push_with_authfile(self, mock_popen):
        auth = Path("/secrets/auth.json")
        args = PushArgs(model="MyOrg/Model", repo="quay.io/repo", authfile=auth)
        do_push(args)
        m, _ = mock_popen
        argv = m.call_args[0][0]
        assert "--authfile" in argv
        assert str(auth) in argv


class TestDoImageRm:
    def test_removes_image(self, mock_popen):
        args = RmArgs(model="MyOrg/Model", repo="quay.io/repo")
        result = do_image_rm(args)
        m, _ = mock_popen
        argv = m.call_args[0][0]
        assert argv == ["podman", "image", "rm", "quay.io/repo:myorg--model-modelcar"]
        assert result is True

    def test_returns_false_on_failure(self, mock_popen):
        _, proc = mock_popen
        proc.wait.return_value = 1
        args = RmArgs(model="MyOrg/Model", repo="quay.io/repo")
        result = do_image_rm(args)
        assert result is False


class TestImageExists:
    def test_tag_present(self, mock_popen, image_search_json):
        m, proc = mock_popen
        proc.stdout.readline.side_effect = [image_search_json.encode(), b""]
        assert image_exists("TestOrg/TestModel", "quay.io/repo") is True

    def test_tag_missing(self, mock_popen, image_search_json_missing):
        m, proc = mock_popen
        proc.stdout.readline.side_effect = [image_search_json_missing.encode(), b""]
        assert image_exists("TestOrg/TestModel", "quay.io/repo") is False

    def test_search_command_args(self, mock_popen, image_search_json):
        m, proc = mock_popen
        proc.stdout.readline.side_effect = [image_search_json.encode(), b""]
        image_exists("TestOrg/TestModel", "quay.io/repo")
        argv = m.call_args[0][0]
        assert argv == ["podman", "search", "quay.io/repo", "--list-tags", "--format", "json", "--limit", "1000"]
