"""
Tests for sigate.ui.settings. QgsSettings persists to a real, shared
backing store (unlike everything else tested in this project), so each
test that changes a value restores it afterward to avoid leaking state
into other tests or a real user's QGIS profile.
"""

import pytest

from sigate.ui import settings as sigate_settings


@pytest.fixture
def restore_settings_after(qgis_app):
    """Snapshots every setting this module manages before a test runs,
    and restores those exact values afterward, regardless of what the
    test changed."""
    original_threshold = sigate_settings.get_size_warning_threshold_bytes()
    original_sevenzip = sigate_settings.get_sevenzip_path_override()
    original_locale = sigate_settings.get_locale()
    yield
    sigate_settings.set_size_warning_threshold_bytes(original_threshold)
    sigate_settings.set_sevenzip_path_override(original_sevenzip)
    sigate_settings.set_locale(original_locale)


def test_size_warning_threshold_round_trip(qgis_app, restore_settings_after):
    sigate_settings.set_size_warning_threshold_bytes(2 * 1024**3)
    assert sigate_settings.get_size_warning_threshold_bytes() == 2 * 1024**3


def test_sevenzip_path_override_round_trip(qgis_app, restore_settings_after):
    sigate_settings.set_sevenzip_path_override("/custom/path/7z")
    assert sigate_settings.get_sevenzip_path_override() == "/custom/path/7z"


def test_sevenzip_path_override_none_when_cleared(qgis_app, restore_settings_after):
    sigate_settings.set_sevenzip_path_override("/some/path")
    sigate_settings.set_sevenzip_path_override(None)
    assert sigate_settings.get_sevenzip_path_override() is None


def test_locale_round_trip(qgis_app, restore_settings_after):
    sigate_settings.set_locale("fr")
    assert sigate_settings.get_locale() == "fr"


def test_set_size_warning_threshold_bytes_rejects_negative_value(
    qgis_app, restore_settings_after
):
    with pytest.raises(ValueError):
        sigate_settings.set_size_warning_threshold_bytes(-1)
