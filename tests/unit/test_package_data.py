import tomllib
from importlib.resources import files


def test_defaults_toml_is_loadable_as_package_data():
    defaults = files("modelcar_maker.util").joinpath("defaults.toml")

    assert defaults.is_file()
    assert tomllib.loads(defaults.read_text())["meta"]["verbosity"] == 0
