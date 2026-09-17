"""
Tests for sigate.ui.target_picker, specifically the last-used path
persistence (the gap this was built for: fields used to reset to empty
every time the tab was reopened). Uses the same settings-restoration
pattern as test_ui_settings.py, since QgsSettings persists globally.
"""

import pytest

from sigate.ui import settings as sigate_settings


@pytest.fixture
def restore_settings_after(qgis_app):
    original_repo = sigate_settings.get_last_central_repo()
    original_dest = sigate_settings.get_last_destination()
    yield
    sigate_settings.set_last_central_repo(original_repo)
    sigate_settings.set_last_destination(original_dest)


def test_paths_persist_across_a_new_widget_instance(qgis_app, restore_settings_after):
    """Simulates closing and reopening the tab (a new TargetPicker
    instance, as would happen on a fresh Data Source Manager session) and
    confirms the previously-entered paths are still there."""
    from sigate.ui.target_picker import TargetPicker

    first = TargetPicker()
    first.repo_edit.setText("/data/my_repo")
    first.dest_edit.setText("/data/my_dest")

    second = TargetPicker()  # a fresh instance, as a new session would create
    assert second.repo_edit.text() == "/data/my_repo"
    assert second.dest_edit.text() == "/data/my_dest"


def test_fresh_install_has_empty_fields(qgis_app, restore_settings_after):
    sigate_settings.set_last_central_repo(None)
    sigate_settings.set_last_destination(None)

    from sigate.ui.target_picker import TargetPicker

    picker = TargetPicker()
    assert picker.repo_edit.text() == ""
    assert picker.dest_edit.text() == ""
