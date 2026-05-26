import pytest

from modelcar_maker.util.helpers import Truthy
from modelcar_maker.util.helpers import normalize


class TestNormalize:
    def test_lowercase_and_replace_slash(self):
        assert normalize("MyOrg/MyModel") == "myorg--mymodel"

    def test_replace_dot(self):
        assert normalize("org/model.v2") == "org--model_v2"

    def test_already_normalized(self):
        assert normalize("already--normalized") == "already--normalized"


class TestTruthy:
    @pytest.mark.parametrize("value", ["true", "True", "TRUE"])
    def test_true(self, value):
        assert bool(Truthy(value)) is True

    @pytest.mark.parametrize("value", ["yes", "Yes", "YES"])
    def test_yes(self, value):
        assert bool(Truthy(value)) is True

    @pytest.mark.parametrize("value", ["1"])
    def test_one(self, value):
        assert bool(Truthy(value)) is True

    @pytest.mark.parametrize("value", ["false", "no", "0", "", "maybe", "2"])
    def test_false(self, value):
        assert bool(Truthy(value)) is False
