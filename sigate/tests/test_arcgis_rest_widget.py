"""
Tests for the ArcGIS REST tab, run against a real headless QGIS
application (see conftest.py's qgis_app fixture). Network access is
replaced with an injected fetch function returning canned Esri JSON.
"""

import json
from urllib.parse import parse_qs, urlparse

SERVICE = {
    "capabilities": "Map,Query,Data",
    "maxRecordCount": 2000,
    "spatialReference": {"wkid": 32632},
    "layers": [
        {"id": 4, "name": "VALANGHE", "parentLayerId": -1, "subLayerIds": [6]},
        {"id": 6, "name": "Valanghe documentate ", "parentLayerId": 4},
        {"id": 3, "name": "OPERE di DIFESA", "parentLayerId": -1},
    ],
}

LAYER = {
    "id": 6,
    "name": "Valanghe documentate ",
    "geometryType": "esriGeometryPolygon",
    "capabilities": "Map,Query,Data",
    "maxRecordCount": 2000,
    "objectIdField": "objectid",
    "extent": {"spatialReference": {"wkid": 32632}},
    "fields": [
        {"name": "objectid", "type": "esriFieldTypeOID", "alias": "OBJECTID"},
        {"name": "comune", "type": "esriFieldTypeString", "alias": "Comune"},
    ],
}


def _rows(n, start=1):
    return [
        {"attributes": {"objectid": i, "comune": f"Comune {i}"}}
        for i in range(start, start + n)
    ]


def _make_fetch(rows=None, more=False, log=None):
    rows = _rows(3) if rows is None else rows

    def fetch(url):
        if log is not None:
            log.append(url)
        path = urlparse(url).path
        params = parse_qs(urlparse(url).query)
        if path.endswith("/query"):
            if "returnCountOnly" in params:
                return json.dumps({"count": len(rows)}).encode()
            return json.dumps(
                {"features": rows, "exceededTransferLimit": more}
            ).encode()
        if path.endswith("/6"):
            return json.dumps(LAYER).encode()
        return json.dumps(SERVICE).encode()

    return fetch


def _make_widget(qgis_app, fetch):
    from qgis.PyQt.QtCore import Qt

    from sigate.ui.arcgis_rest_widget import ArcGisRestSourceSelectWidget

    widget = ArcGisRestSourceSelectWidget(None, Qt.WindowType(0), fetch=fetch)
    # The default connection is the seeded Piemonte source; load it
    # explicitly in case the manager did not emit on construction.
    widget._on_connection_changed(widget.connection_manager.current_connection())
    return widget


def _select_layer(widget, layer_id=6):
    for i in range(widget.list_widget.count()):
        if widget.list_widget.item(i).data(0x0100) == layer_id:
            widget.list_widget.setCurrentRow(i)
            return
    raise AssertionError(f"layer {layer_id} not listed")


def test_build_afs_uri_puts_the_filter_in_sql_and_states_the_crs(qgis_app):
    from qgis.core import QgsDataSourceUri

    from sigate.ui.arcgis_rest_widget import build_afs_uri

    uri = QgsDataSourceUri(
        build_afs_uri("https://x.test/MapServer/6", "EPSG:32632", "comune = 'A'")
    )
    assert uri.param("url") == "https://x.test/MapServer/6"
    assert uri.param("crs") == "EPSG:32632"
    assert uri.sql() == "comune = 'A'"


def test_provider_identity(qgis_app):
    from sigate.ui.arcgis_rest_provider import ArcGisRestSourceSelectProvider

    provider = ArcGisRestSourceSelectProvider()
    assert provider.providerKey() == "sigate_arcgis_rest"
    assert not provider.icon().isNull()
    assert provider.ordering() == 30003


def test_only_queryable_layers_are_listed_with_their_group_path(qgis_app):
    widget = _make_widget(qgis_app, _make_fetch())
    labels = [
        widget.list_widget.item(i).text() for i in range(widget.list_widget.count())
    ]
    assert labels == ["VALANGHE › Valanghe documentate", "OPERE di DIFESA"]


def test_layer_filter_narrows_the_list(qgis_app):
    widget = _make_widget(qgis_app, _make_fetch())
    widget.layer_filter_edit.setText("opere")
    assert widget.list_widget.count() == 1


def test_query_populates_results_and_count(qgis_app):
    widget = _make_widget(qgis_app, _make_fetch())
    _select_layer(widget)
    widget._on_query_clicked()
    assert widget.query_fieldnames == ["objectid", "comune"]
    assert len(widget.query_rows) == 3
    assert "of 3" in widget.page_label.text()
    assert not widget.next_page_button.isEnabled()


def test_next_page_enabled_when_server_says_more_and_uses_offset(qgis_app):
    log = []
    widget = _make_widget(qgis_app, _make_fetch(more=True, log=log))
    _select_layer(widget)
    widget._on_query_clicked()
    assert widget.next_page_button.isEnabled()
    widget._next_page()
    last_query = [u for u in log if "/query" in u and "returnCountOnly" not in u][-1]
    assert parse_qs(urlparse(last_query).query)["resultOffset"] == ["200"]


def test_switching_layer_clears_the_previous_results_and_filter(qgis_app):
    widget = _make_widget(qgis_app, _make_fetch())
    _select_layer(widget)
    widget._on_query_clicked()
    widget._active_filter_text = "comune = 'x'"
    widget.list_widget.setCurrentRow(1)
    assert widget.query_rows == []
    assert widget._active_filter_text == ""


def test_failed_query_leaves_the_grid_empty_and_reports(qgis_app, monkeypatch):
    from qgis.PyQt.QtWidgets import QMessageBox

    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a))

    def fetch(url):
        if "/query" in url:
            return json.dumps({"error": {"code": 400, "message": "bad where"}}).encode()
        return _make_fetch()(url)

    widget = _make_widget(qgis_app, fetch)
    _select_layer(widget)
    widget._on_query_clicked()
    assert widget.query_rows == []
    assert "failed" in widget.status_label.text().lower()
    assert shown


def test_result_filter_narrows_rows_without_a_new_request(qgis_app):
    log = []
    widget = _make_widget(qgis_app, _make_fetch(log=log))
    _select_layer(widget)
    widget._on_query_clicked()
    before = len(log)
    widget.result_filter_edit.setText("comune 2")
    assert len(widget.query_rows) == 1
    assert len(log) == before


def _capture_adds(widget):
    added = []
    widget.addVectorLayer.connect(lambda *a: added.append(a))
    return added


def test_add_to_map_without_selection_passes_the_active_filter(qgis_app):
    from qgis.core import QgsDataSourceUri

    widget = _make_widget(qgis_app, _make_fetch())
    _select_layer(widget)
    widget._active_filter_text = "comune = 'A'"
    added = _capture_adds(widget)
    widget._on_add_clicked()
    assert len(added) == 1
    uri_text, name, provider = added[0]
    assert provider == "arcgisfeatureserver"
    assert name == "Valanghe documentate"
    uri = QgsDataSourceUri(uri_text)
    assert uri.sql() == "comune = 'A'"
    assert uri.param("url").endswith("/MapServer/6")
    assert uri.param("crs") == "EPSG:32632"


def test_add_to_map_with_selected_rows_uses_object_ids(qgis_app):
    from qgis.core import QgsDataSourceUri

    widget = _make_widget(qgis_app, _make_fetch())
    _select_layer(widget)
    widget._on_query_clicked()
    widget.results_tree.topLevelItem(0).setSelected(True)
    widget.results_tree.topLevelItem(2).setSelected(True)
    added = _capture_adds(widget)
    widget._on_add_clicked()
    uri_text, name, _provider = added[0]
    assert QgsDataSourceUri(uri_text).sql() == "objectid IN (1, 3)"
    assert name.endswith("(2 selected)")


def test_add_to_map_without_a_layer_does_not_emit(qgis_app, monkeypatch):
    from qgis.PyQt.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    widget = _make_widget(qgis_app, _make_fetch())
    added = _capture_adds(widget)
    widget._on_add_clicked()
    assert added == []
