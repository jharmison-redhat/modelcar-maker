from pathlib import Path

import pytest

from modelcar_maker.image.common import should_include
from modelcar_maker.image.template import render


class TestShouldInclude:
    def test_hides_hidden_files(self):
        assert should_include(".hidden") is False

    def test_hides_containerfile(self):
        assert should_include("Containerfile") is False

    def test_hides_original(self):
        assert should_include("original") is False

    def test_hides_consolidated_safetensors(self):
        assert should_include("consolidated.safetensors") is False

    def test_includes_regular_files(self):
        assert should_include("config.json") is True
        assert should_include("model.safetensors") is True
        assert should_include("README.md") is True

    def test_accepts_path(self):
        assert should_include(Path("config.json")) is True
        assert should_include(Path(".hidden")) is False


BASE_IMAGE = "registry.access.redhat.com/ubi9/ubi-minimal:latest"


class TestRender:
    def test_creates_containerfile(self, tmp_model_dir):
        render("TestOrg/TestModel", tmp_model_dir, "abc123", BASE_IMAGE)
        containerfile = tmp_model_dir / "Containerfile"
        assert containerfile.exists()

    def test_content_has_expected_elements(self, tmp_model_dir):
        render("TestOrg/TestModel", tmp_model_dir, "abc123", BASE_IMAGE)
        text = (tmp_model_dir / "Containerfile").read_text()

        assert f"FROM {BASE_IMAGE}" in text
        assert "COPY config.json /models/" in text
        assert "COPY model.safetensors /models/" in text
        assert "COPY README.md /modelcard.md" in text
        assert "testorg--testmodel-modelcar" in text
        assert 'model.name="TestOrg/TestModel"' in text
        assert 'model.commit="abc123"' in text

    def test_model_files_sorted(self, tmp_model_dir):
        # Add more files to verify lexicographic ordering
        (tmp_model_dir / "z_last.bin").write_text("")
        (tmp_model_dir / "a_first.bin").write_text("")
        render("TestOrg/TestModel", tmp_model_dir, "abc123", BASE_IMAGE)
        text = (tmp_model_dir / "Containerfile").read_text()
        # a_first should come before z_last
        assert text.index("a_first") < text.index("z_last")

    def test_no_extraneous_files_copied(self, tmp_model_dir):
        render("TestOrg/TestModel", tmp_model_dir, "abc123", BASE_IMAGE)
        text = (tmp_model_dir / "Containerfile").read_text()
        assert ".hidden" not in text
        assert "Containerfile" not in text
        assert "original" not in text
        assert "consolidated.safetensors" not in text

    def test_readme_modelcard_detection(self, tmp_model_dir):
        # Remove README and add a different one
        (tmp_model_dir / "README.md").unlink()
        (tmp_model_dir / "README.rst").write_text("")
        render("TestOrg/TestModel", tmp_model_dir, "abc123", BASE_IMAGE)
        text = (tmp_model_dir / "Containerfile").read_text()
        assert "COPY README.rst /modelcard.md" in text

    def test_no_modelcard_when_no_readme(self, tmp_model_dir):
        (tmp_model_dir / "README.md").unlink()
        render("TestOrg/TestModel", tmp_model_dir, "abc123", BASE_IMAGE)
        text = (tmp_model_dir / "Containerfile").read_text()
        assert "/modelcard.md" not in text
