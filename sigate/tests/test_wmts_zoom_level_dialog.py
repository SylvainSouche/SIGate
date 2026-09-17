"""
Tests for sigate.ui.wmts_zoom_level_dialog - exercises the dialog's own
internal logic directly (combo population/ordering, label content,
default selection, chosen_identifier()) rather than only through the
widget-level integration tests in test_wmts_wms_widget.py, which
monkeypatch choose_zoom_level as a black box and never touch this
module's own sorting/formatting code at all.
"""

from sigate.gateways.wmts_wms import WmtsTileMatrixLevel
from sigate.ui.wmts_zoom_level_dialog import ZoomLevelChoiceDialog


def _levels():
    # deliberately out of order and including a middle value, so sorting
    # behavior is actually exercised rather than coincidentally correct
    return [
        WmtsTileMatrixLevel(identifier="12", resolution=100.0),
        WmtsTileMatrixLevel(identifier="18", resolution=0.15),
        WmtsTileMatrixLevel(identifier="15", resolution=5.0),
    ]


def test_combo_lists_every_level(qgis_app):
    dialog = ZoomLevelChoiceDialog(None, _levels(), bbox=(0.0, 0.0, 100.0, 100.0))
    assert dialog._combo.count() == 3


def test_combo_orders_coarsest_first_finest_last(qgis_app):
    """Coarsest first, finest last - so the default selection (finest,
    matching this feature's own behavior before a level could be chosen
    at all) lands on the last entry, and scanning top-to-bottom shows
    size growing in the expected direction."""
    dialog = ZoomLevelChoiceDialog(None, _levels(), bbox=(0.0, 0.0, 100.0, 100.0))
    identifiers_in_order = [
        dialog._combo.itemData(i) for i in range(dialog._combo.count())
    ]
    assert identifiers_in_order == ["12", "15", "18"]  # 100.0, 5.0, 0.15 resolution


def test_default_selection_is_the_finest_level(qgis_app):
    dialog = ZoomLevelChoiceDialog(None, _levels(), bbox=(0.0, 0.0, 100.0, 100.0))
    assert dialog._combo.currentData() == "18"


def test_chosen_identifier_reflects_the_current_combo_selection(qgis_app):
    dialog = ZoomLevelChoiceDialog(None, _levels(), bbox=(0.0, 0.0, 100.0, 100.0))
    dialog._combo.setCurrentIndex(0)  # coarsest
    assert dialog.chosen_identifier() == "12"


def test_item_label_includes_identifier_resolution_and_size_estimate(qgis_app):
    """The whole point of this dialog - requested directly ("give the
    choice between different zoom levels if they exist with the
    different sizes") - each entry's label must actually show the size
    difference, not just an opaque identifier."""
    dialog = ZoomLevelChoiceDialog(
        None,
        [WmtsTileMatrixLevel(identifier="18", resolution=0.2)],
        bbox=(0.0, 0.0, 10_000.0, 20_000.0),
    )
    label = dialog._combo.itemText(0)
    assert "18" in label
    assert "0.2" in label
    assert "50000" in label.replace(",", "").replace(" ", "") or "50,000" in label
    assert "GB" in label


def test_handles_a_single_level_without_error(qgis_app):
    """Not the normal path (the widget only shows this dialog when more
    than one level exists), but the dialog itself shouldn't misbehave if
    constructed with just one."""
    dialog = ZoomLevelChoiceDialog(
        None,
        [WmtsTileMatrixLevel(identifier="18", resolution=1.0)],
        bbox=(0.0, 0.0, 100.0, 100.0),
    )
    assert dialog._combo.count() == 1
    assert dialog.chosen_identifier() == "18"
