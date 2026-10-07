"""Guards for metadata.txt - pure Python. QGIS's plugin manager reads it to
decide which QGIS versions list the plugin at all."""

import configparser
from pathlib import Path

_METADATA = Path(__file__).parent.parent / "metadata.txt"


def _general():
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(_METADATA, encoding="utf-8")
    return parser["general"]


def test_plugin_is_declared_compatible_with_qgis_4():
    """Without supportsQt6=True QGIS 4 (Qt6) does not offer the plugin, and
    an unset qgisMaximumVersion defaults to 3.99, which excludes 4.x too."""
    general = _general()
    assert general.get("supportsQt6", "").strip().lower() == "true"
    maximum = general.get("qgisMaximumVersion", "3.99").strip()
    assert int(maximum.split(".")[0]) >= 4


def test_metadata_version_matches_pyproject():
    import re

    pyproject = (_METADATA.parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    assert _general()["version"].strip() == declared


def test_minimum_qgis_version_is_3_40_or_later():
    """The code relies on scoped enums, point-cloud layers and the copc
    provider; the long-term release 3.40 is the oldest line declared."""
    parts = _general()["qgisMinimumVersion"].strip().split(".")
    assert (int(parts[0]), int(parts[1])) >= (3, 40)
