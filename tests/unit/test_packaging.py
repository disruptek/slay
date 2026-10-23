"""
Unit tests for package metadata.
"""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"


def _source_packages() -> set[str]:
    """Dotted names of every package directory under src/"""
    return {
        ".".join(init.parent.relative_to(SRC).parts)
        for init in SRC.rglob("__init__.py")
        if "__pycache__" not in init.parts
    }


class TestPackageList:
    """Tests for the setuptools package list"""

    def test_every_source_package_is_installed(self):
        """A wheel must contain every package that the source imports"""
        with open(ROOT / "pyproject.toml", "rb") as f:
            config = tomllib.load(f)

        installed = set(config["tool"]["setuptools"]["packages"])
        missing = _source_packages() - installed

        assert not missing, f"packages missing from pyproject.toml: {sorted(missing)}"
