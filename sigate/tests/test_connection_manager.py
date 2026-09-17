"""
Tests for sigate.ui.connection_manager. QFileDialog and QMessageBox
static methods are monkeypatched to canned responses, since either would
otherwise block waiting for a real click in a headless test.
"""

import json

import pytest

from sigate.sources.store import AuthConfig, GatewayConfig, SourceConfig


@pytest.fixture
def no_block_dialogs(monkeypatch):
    from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", ""))
    )
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", ""))
    )


def _make_manager(qgis_app, tmp_path, gateway_type="atom"):
    from sigate.ui.connection_manager import ConnectionManager

    return ConnectionManager(gateway_type, data_dir_provider=lambda: tmp_path)


def test_reload_shows_seed_connection(qgis_app, tmp_path):
    manager = _make_manager(qgis_app, tmp_path, gateway_type="atom")
    assert manager.combo.count() == 1  # IGN, the only seeded atom source
    assert manager.current_connection().key == "ign_fr"


def test_new_connection_appears_and_is_selected(qgis_app, tmp_path, no_block_dialogs):
    from sigate.ui.connection_manager import ConnectionEditDialog

    manager = _make_manager(qgis_app, tmp_path)

    dialog = ConnectionEditDialog("atom", parent=manager)
    dialog.key_edit.setText("test_source")
    dialog.name_edit.setText("Test Source")
    dialog.country_edit.setText("Testland")
    dialog.url_edit.setText("https://example.test/atom")

    new_source = dialog.get_source_config()
    from sigate.sources.store import add_or_replace_source

    add_or_replace_source(tmp_path, new_source)
    manager.reload_connections(select_key="test_source")

    assert manager.combo.count() == 2
    assert manager.current_connection().key == "test_source"
    assert (
        manager.current_connection().gateway("atom").base_url
        == "https://example.test/atom"
    )


def test_new_connection_via_ui_flow_rejects_duplicate_key(
    qgis_app, tmp_path, no_block_dialogs, monkeypatch
):
    from qgis.PyQt.QtWidgets import QDialog

    from sigate.ui.connection_manager import ConnectionEditDialog

    manager = _make_manager(qgis_app, tmp_path)

    # Simulate the New dialog being filled with the already-existing
    # seed key "ign_fr" and accepted.
    def fake_exec(self):
        self.key_edit.setText("ign_fr")
        self.name_edit.setText("Duplicate")
        self.url_edit.setText("https://example.test")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ConnectionEditDialog, "exec", fake_exec)
    manager._on_new()

    # rejected - still just the one original seed connection
    assert manager.combo.count() == 1


def test_edit_updates_user_added_connection(qgis_app, tmp_path, no_block_dialogs):
    from sigate.sources.store import add_or_replace_source

    custom = SourceConfig(
        key="custom",
        display_name="Custom",
        country="X",
        gateways=[GatewayConfig(gateway_type="atom", base_url="https://old.test")],
    )
    add_or_replace_source(tmp_path, custom)

    manager = _make_manager(qgis_app, tmp_path)
    manager.reload_connections(select_key="custom")
    assert manager.current_connection().gateway("atom").base_url == "https://old.test"

    updated = SourceConfig(
        key="custom",
        display_name="Custom Renamed",
        country="X",
        gateways=[GatewayConfig(gateway_type="atom", base_url="https://new.test")],
    )
    add_or_replace_source(tmp_path, updated)
    manager.reload_connections(select_key="custom")

    assert manager.current_connection().display_name == "Custom Renamed"
    assert manager.current_connection().gateway("atom").base_url == "https://new.test"


def test_edit_button_disabled_state_reflects_seed_vs_user_added(qgis_app, tmp_path):
    from sigate.sources.store import add_or_replace_source

    add_or_replace_source(
        tmp_path,
        SourceConfig(
            key="custom",
            display_name="Custom",
            country="X",
            gateways=[GatewayConfig(gateway_type="atom", base_url="https://x")],
        ),
    )
    manager = _make_manager(qgis_app, tmp_path)

    # select the seed (built-in) connection
    seed_index = next(
        i
        for i in range(manager.combo.count())
        if manager.combo.itemData(i)[0].key == "ign_fr"
    )
    manager.combo.setCurrentIndex(seed_index)
    assert not manager.delete_button.isEnabled()

    # select the user-added one
    custom_index = next(
        i
        for i in range(manager.combo.count())
        if manager.combo.itemData(i)[0].key == "custom"
    )
    manager.combo.setCurrentIndex(custom_index)
    assert manager.delete_button.isEnabled()


def test_delete_removes_user_added_connection(qgis_app, tmp_path, no_block_dialogs):
    from sigate.sources.store import add_or_replace_source, load_user_overrides

    add_or_replace_source(
        tmp_path,
        SourceConfig(
            key="custom",
            display_name="Custom",
            country="X",
            gateways=[GatewayConfig(gateway_type="atom", base_url="https://x")],
        ),
    )
    manager = _make_manager(qgis_app, tmp_path)
    manager.reload_connections(select_key="custom")
    assert manager.current_connection().key == "custom"

    manager._on_delete()

    assert load_user_overrides(tmp_path) == []
    assert all(s.key != "custom" for s in manager._all_connections())


def test_delete_is_a_no_op_for_seed_connection(qgis_app, tmp_path, no_block_dialogs):
    manager = _make_manager(qgis_app, tmp_path)
    manager.reload_connections(select_key="ign_fr")
    manager._on_delete()
    # nothing removed - ign_fr is still there, since it's seed-only, not user-added
    assert any(s.key == "ign_fr" for s in manager._all_connections())


def test_save_writes_json_and_load_reads_it_back(qgis_app, tmp_path, monkeypatch):
    from qgis.PyQt.QtWidgets import QFileDialog

    from sigate.sources.store import add_or_replace_source

    add_or_replace_source(
        tmp_path,
        SourceConfig(
            key="custom",
            display_name="Custom",
            country="X",
            gateways=[
                GatewayConfig(
                    gateway_type="atom",
                    base_url="https://x",
                    auth=AuthConfig(
                        kind="apikey_header", header_name="X-Key", value="secret"
                    ),
                )
            ],
        ),
    )
    manager = _make_manager(qgis_app, tmp_path)
    manager.reload_connections(select_key="custom")

    save_path = tmp_path / "exported.json"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: (str(save_path), "")),
    )
    manager._on_save()

    assert save_path.exists()
    with open(save_path) as f:
        data = json.load(f)
    assert data["key"] == "custom"
    assert data["gateways"][0]["auth"]["value"] == "secret"

    # now load it into a second, independent data directory - confirms
    # round-trip correctness, not just that a file got written
    other_dir = tmp_path / "other"
    manager2 = _make_manager(qgis_app, other_dir)
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(save_path), "")),
    )
    manager2._on_load()

    assert manager2.current_connection().key == "custom"
    assert manager2.current_connection().gateway("atom").auth.value == "secret"


def test_connection_changed_signal_fires_on_combo_change(qgis_app, tmp_path):
    from sigate.sources.store import add_or_replace_source

    add_or_replace_source(
        tmp_path,
        SourceConfig(
            key="custom",
            display_name="Custom",
            country="X",
            gateways=[GatewayConfig(gateway_type="atom", base_url="https://x")],
        ),
    )
    manager = _make_manager(qgis_app, tmp_path)

    captured = []
    manager.connectionChanged.connect(
        lambda source: captured.append(source.key if source else None)
    )

    custom_index = next(
        i
        for i in range(manager.combo.count())
        if manager.combo.itemData(i)[0].key == "custom"
    )
    manager.combo.setCurrentIndex(custom_index)

    assert "custom" in captured


def test_source_with_one_gateway_of_a_type_gets_one_plain_labelled_entry(
    qgis_app, tmp_path
):
    """The common case, and the only case for every seeded WFS/Bulk
    Listing source today - must stay exactly as it was before gateway
    instances existed at all: one entry, plain display name, no role
    suffix."""
    manager = _make_manager(qgis_app, tmp_path, gateway_type="atom")
    assert manager.combo.count() == 1
    assert manager.combo.itemText(0) == "IGN (France)"


def test_source_with_two_gateways_of_a_type_gets_two_labelled_entries(
    qgis_app, tmp_path
):
    """The actual bug this fixes: IGN's WM(T)S gateway has a separate
    public and private/apikey-gated instance - the combo must offer both
    as distinct, individually selectable entries, not one merged entry."""
    manager = _make_manager(qgis_app, tmp_path, gateway_type="wmts_wms")
    ign_labels = [
        manager.combo.itemText(i)
        for i in range(manager.combo.count())
        if manager.combo.itemData(i)[0].key == "ign_fr"
    ]
    assert len(ign_labels) == 2
    assert any("public" in label for label in ign_labels)
    assert any("private" in label for label in ign_labels)
    assert ign_labels[0] != ign_labels[1]


def test_current_gateway_returns_the_specific_selected_instance(qgis_app, tmp_path):
    """current_gateway() must return the exact instance the selected
    combo entry corresponds to - not source.gateway(type), which would
    silently return only the first-declared instance regardless of which
    entry is actually selected."""
    manager = _make_manager(qgis_app, tmp_path, gateway_type="wmts_wms")

    private_index = next(
        i
        for i in range(manager.combo.count())
        if manager.combo.itemData(i)[0].key == "ign_fr"
        and manager.combo.itemData(i)[1].extra.get("role") == "private"
    )
    manager.combo.setCurrentIndex(private_index)
    assert manager.current_gateway().extra.get("role") == "private"
    assert manager.current_connection().key == "ign_fr"

    public_index = next(
        i
        for i in range(manager.combo.count())
        if manager.combo.itemData(i)[0].key == "ign_fr"
        and manager.combo.itemData(i)[1].extra.get("role") == "public"
    )
    manager.combo.setCurrentIndex(public_index)
    assert manager.current_gateway().extra.get("role") == "public"
    assert manager.current_connection().key == "ign_fr"


def test_a_new_single_gateway_source_still_gets_one_entry_alongside_ign(
    qgis_app, tmp_path, no_block_dialogs
):
    """Adding a plain, single-gateway source to the wmts_wms tab must not
    be affected by IGN's two-gateway entries living in the same combo -
    it still gets exactly one entry, plain label."""
    from sigate.sources.store import add_or_replace_source

    add_or_replace_source(
        tmp_path,
        SourceConfig(
            key="other",
            display_name="Other Country",
            country="Y",
            gateways=[
                GatewayConfig(gateway_type="wmts_wms", base_url="https://other.test")
            ],
        ),
    )
    manager = _make_manager(qgis_app, tmp_path, gateway_type="wmts_wms")
    other_labels = [
        manager.combo.itemText(i)
        for i in range(manager.combo.count())
        if manager.combo.itemData(i)[0].key == "other"
    ]
    assert other_labels == ["Other Country"]


def test_gateway_type_accepts_a_list_and_lists_both_types_together(qgis_app, tmp_path):
    """The real Bulk Listing use case: one connection combo spanning
    both atom (IGN) and stac (swisstopo) instances - a bare string
    still works (every existing WFS/WMTS-tab call site, unaffected),
    but a list is the shape Bulk Listing actually needs now."""
    manager = _make_manager(qgis_app, tmp_path, gateway_type=["atom", "stac"])
    types_seen = {
        manager.combo.itemData(i)[1].gateway_type for i in range(manager.combo.count())
    }
    assert types_seen == {"atom", "stac"}
    # 1 atom instance (IGN) + 12 stac instances (swisstopo) + 1 stac
    # instance (geodienste.ch's Gefahrenkarten) confirmed elsewhere in
    # test_sources.py
    assert manager.combo.count() == 14


def test_current_gateway_reflects_the_actual_selected_type_not_a_fixed_one(
    qgis_app, tmp_path
):
    """Regression test for the real bug this generalization fixed:
    before, ui.bulk_listing_widget._on_connection_changed looked up
    source.gateway("atom") unconditionally - harmless while every
    source had at most one relevant gateway, but silently wrong once a
    source (or the combo as a whole) could resolve to a stac instance
    instead. current_gateway() must track the actual combo selection."""
    manager = _make_manager(qgis_app, tmp_path, gateway_type=["atom", "stac"])
    stac_index = next(
        i
        for i in range(manager.combo.count())
        if manager.combo.itemData(i)[1].gateway_type == "stac"
    )
    manager.combo.setCurrentIndex(stac_index)
    assert manager.current_gateway().gateway_type == "stac"
    assert (
        manager.current_gateway()
        .extra.get("collection_id", "")
        .startswith("ch.swisstopo.")
    )
