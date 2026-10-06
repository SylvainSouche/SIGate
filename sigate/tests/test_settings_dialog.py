"""
Tests for sigate.ui.settings_dialog, and confirmation that the Bulk
Listing widget actually consults these settings rather than only
displaying them decoratively. Uses the same settings-restoration pattern
as test_ui_settings.py, since QgsSettings persists to a real shared
backing store.
"""

import pytest

from sigate.ui import settings as sigate_settings


@pytest.fixture
def restore_settings_after(qgis_app):
    original_threshold = sigate_settings.get_size_warning_threshold_bytes()
    original_sevenzip = sigate_settings.get_sevenzip_path_override()
    yield
    sigate_settings.set_size_warning_threshold_bytes(original_threshold)
    sigate_settings.set_sevenzip_path_override(original_sevenzip)


def test_dialog_loads_current_values(qgis_app, restore_settings_after):
    from sigate.ui.settings_dialog import SettingsDialog

    sigate_settings.set_size_warning_threshold_bytes(3 * 1024**3)
    sigate_settings.set_sevenzip_path_override("/opt/7z/7z")

    dialog = SettingsDialog()
    assert dialog.threshold_spin.value() == 3
    assert dialog.sevenzip_edit.text() == "/opt/7z/7z"


def test_dialog_accept_persists_new_values(qgis_app, restore_settings_after):
    from sigate.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    dialog.threshold_spin.setValue(5)
    dialog.sevenzip_edit.setText("/custom/7z")
    dialog._on_accept()

    assert sigate_settings.get_size_warning_threshold_bytes() == 5 * 1024**3
    assert sigate_settings.get_sevenzip_path_override() == "/custom/7z"


def test_dialog_accept_with_empty_sevenzip_field_clears_override(
    qgis_app, restore_settings_after
):
    from sigate.ui.settings_dialog import SettingsDialog

    sigate_settings.set_sevenzip_path_override("/some/path")
    dialog = SettingsDialog()
    dialog.sevenzip_edit.setText("")
    dialog._on_accept()

    assert sigate_settings.get_sevenzip_path_override() is None


def test_bulk_listing_widget_uses_sevenzip_override_instead_of_auto_detect(
    qgis_app, restore_settings_after
):
    from qgis.PyQt.QtCore import Qt

    from sigate.ui.bulk_listing_widget import BulkListingSourceSelectWidget

    sigate_settings.set_sevenzip_path_override("/fake/overridden/7z")
    feed = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:gpf_dl="http://x"
        gpf_dl:page="1" gpf_dl:pagesize="50" gpf_dl:pagecount="1" gpf_dl:totalentries="0"></feed>"""
    widget = BulkListingSourceSelectWidget(
        None, Qt.WindowType(0), fetch=lambda url: feed, download_fn=None
    )

    assert widget.sevenzip_exe == "/fake/overridden/7z"
