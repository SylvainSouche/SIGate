"""
Tests for sigate.ui.wmts_wms_widget.

Confirms the actual fix for a real, serious bug: earlier versions of this
widget only ever showed a single hardcoded example layer per configured
source, never the real list of layers a server actually serves. These
tests inject fake GetCapabilities responses (with multiple real-shaped
layers and tile matrix sets) to confirm the widget now genuinely
discovers and lists what a server offers, rather than reading a fixed
placeholder.
"""

from sigate.sources.store import AuthConfig, GatewayConfig, SourceConfig

import pytest

from pathlib import Path


def _multi_layer_capabilities_xml():
    return b"""<?xml version="1.0"?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0" xmlns:ows="http://www.opengis.net/ows/1.1">
<Contents>
<Layer>
<ows:Title>Photographies aeriennes</ows:Title>
<ows:Identifier>ORTHOIMAGERY.ORTHOPHOTOS</ows:Identifier>
<Style isDefault="true"><ows:Identifier>normal</ows:Identifier></Style>
<Format>image/jpeg</Format>
<TileMatrixSetLink><TileMatrixSet>PM</TileMatrixSet></TileMatrixSetLink>
</Layer>
<Layer>
<ows:Title>Photographies aeriennes Lambert 93</ows:Title>
<ows:Identifier>HR.ORTHOIMAGERY.ORTHOPHOTOS.L93</ows:Identifier>
<Style isDefault="true"><ows:Identifier>normal</ows:Identifier></Style>
<Format>image/jpeg</Format>
<TileMatrixSetLink><TileMatrixSet>2154_10cm_10_20</TileMatrixSet></TileMatrixSetLink>
</Layer>
<Layer>
<ows:Title>Cartes IGN</ows:Title>
<ows:Identifier>GEOGRAPHICALGRIDSYSTEMS.MAPS</ows:Identifier>
<Style isDefault="true"><ows:Identifier>normal</ows:Identifier></Style>
<Format>image/jpeg</Format>
<TileMatrixSetLink><TileMatrixSet>PM</TileMatrixSet></TileMatrixSetLink>
</Layer>
<TileMatrixSet>
<ows:Identifier>PM</ows:Identifier>
<ows:SupportedCRS>urn:ogc:def:crs:EPSG::3857</ows:SupportedCRS>
</TileMatrixSet>
<TileMatrixSet>
<ows:Identifier>2154_10cm_10_20</ows:Identifier>
<ows:SupportedCRS>urn:ogc:def:crs:EPSG::2154</ows:SupportedCRS>
</TileMatrixSet>
</Contents>
</Capabilities>"""


def _fake_authcfg_provisioner(source_key, gateway_role, auth_config):
    """Stands in for ui.authcfg.ensure_authcfg_for_gateway everywhere a
    widget-level test is built. Never touches the real QgsAuthManager -
    a headless/offscreen QgsApplication with no master password set will
    block indefinitely on that real path (this is exactly what caused
    tests to hang partway through this file before authcfg_provisioner
    was threaded through the widget as an injection seam, mirroring the
    existing `fetch` pattern)."""
    return f"test_authcfg_{gateway_role}" if auth_config.kind != "none" else None


def _make_widget(qgis_app, fetch, authcfg_provisioner=None):
    from qgis.PyQt.QtCore import Qt

    from sigate.ui.wmts_wms_widget import WmtsWmsSourceSelectWidget

    if authcfg_provisioner is None:
        authcfg_provisioner = _fake_authcfg_provisioner
    return WmtsWmsSourceSelectWidget(
        None,
        Qt.WindowType(0),
        0,
        fetch=fetch,
        authcfg_provisioner=authcfg_provisioner,
    )


def test_collect_layer_choices_for_gateway_returns_empty_for_none():
    from sigate.ui.wmts_wms_widget import collect_layer_choices_for_gateway

    assert collect_layer_choices_for_gateway(None, None) == []


def _two_gateway_source():
    return SourceConfig(
        key="test",
        display_name="Test",
        country="X",
        gateways=[
            GatewayConfig(
                gateway_type="wmts_wms",
                base_url="https://public.test/wmts",
                extra={"role": "public"},
            ),
            GatewayConfig(
                gateway_type="wmts_wms",
                base_url="https://private.test/wmts",
                auth=AuthConfig(
                    kind="apikey_header", header_name="apikey", value="secret"
                ),
                extra={"role": "private"},
            ),
        ],
    )


def test_collect_layer_choices_for_gateway_includes_auth_gated_gateway_via_authcfg():
    """Auth-gated gateways are no longer skipped outright - each gets an
    authcfg provisioned (here, a fake provisioner standing in for
    ui.authcfg.ensure_authcfg_for_gateway) and its layers are included
    exactly like an unauthenticated gateway's. Each gateway is now its
    own connection-manager entry, so this calls the collector once per
    gateway rather than once for the whole source."""
    from sigate.ui.wmts_wms_widget import collect_layer_choices_for_gateway

    source = _two_gateway_source()
    public_gateway, private_gateway = source.gateways_of_type("wmts_wms")

    def fake_provisioner(source_key, gateway_role, auth_config):
        return f"authcfg_{gateway_role}" if auth_config.kind != "none" else None

    public_choices = collect_layer_choices_for_gateway(
        source,
        public_gateway,
        fetch=lambda url: _multi_layer_capabilities_xml(),
        authcfg_provisioner=fake_provisioner,
    )
    private_choices = collect_layer_choices_for_gateway(
        source,
        private_gateway,
        fetch=lambda url: _multi_layer_capabilities_xml(),
        authcfg_provisioner=fake_provisioner,
    )
    assert len(public_choices) == 3
    assert len(private_choices) == 3
    assert all(c.authcfg_id is None for c in public_choices)
    assert all(c.authcfg_id == "authcfg_private" for c in private_choices)


def test_collect_layer_choices_for_gateway_empty_when_authcfg_provisioning_fails():
    from sigate.ui.wmts_wms_widget import collect_layer_choices_for_gateway

    source = _two_gateway_source()
    _public_gateway, private_gateway = source.gateways_of_type("wmts_wms")

    def failing_provisioner(source_key, gateway_role, auth_config):
        raise RuntimeError("auth database unavailable")

    def fetch(url):
        raise AssertionError(
            "a gateway whose authcfg provisioning failed must not be queried"
        )

    choices = collect_layer_choices_for_gateway(
        source, private_gateway, fetch=fetch, authcfg_provisioner=failing_provisioner
    )
    assert choices == []


def test_collect_layer_choices_attaches_apikey_header_for_private_gateway_listing(
    monkeypatch,
):
    """Regression test for the actual bug reported live: the private
    gateway's own layers never appeared, because listing its
    GetCapabilities is a request SIGate makes itself (separate from the
    authcfg-gated tile-serving connection QGIS's provider later makes),
    and that listing request was going out with no apikey header at all
    - silently rejected by the server, then silently swallowed by this
    function's own except/return-empty. Exercised here with no override
    fetch given (fetch=None), matching real production use, with the
    network-touching pieces (wmts_get_capabilities, fetch_with_header)
    replaced by recording fakes so no real request is made."""
    import sigate.ui.wmts_wms_widget as widget_module

    source = _two_gateway_source()
    public_gateway, private_gateway = source.gateways_of_type("wmts_wms")
    recorded_fetches = {}

    def fake_wmts_get_capabilities(base_url, fetch=None, lang=None):
        recorded_fetches[base_url] = fetch
        return [], {}

    def fake_fetch_with_header(header_name, header_value):
        def marker_fetch(url):
            return b""

        marker_fetch.header_name = header_name
        marker_fetch.header_value = header_value
        return marker_fetch

    monkeypatch.setattr(
        widget_module, "wmts_get_capabilities", fake_wmts_get_capabilities
    )
    monkeypatch.setattr(widget_module, "fetch_with_header", fake_fetch_with_header)

    widget_module.collect_layer_choices_for_gateway(
        source, public_gateway, fetch=None, authcfg_provisioner=lambda *a, **k: None
    )
    widget_module.collect_layer_choices_for_gateway(
        source,
        private_gateway,
        fetch=None,
        authcfg_provisioner=lambda *a, **k: "some_authcfg",
    )

    public_fetch = recorded_fetches["https://public.test/wmts"]
    private_fetch = recorded_fetches["https://private.test/wmts"]
    # Public gateway needs no header - real default transport is left to
    # kick in downstream, so no override fetch is passed for it.
    assert public_fetch is None
    # Private gateway's listing request now carries the real apikey
    # header, built from the gateway's own AuthConfig.
    assert private_fetch.header_name == "apikey"
    assert private_fetch.header_value == "secret"


def test_collect_layer_choices_for_gateway_finds_every_real_layer_not_just_one():
    """The actual bug this fix closes: only a single hardcoded layer was
    ever shown before, regardless of what a server's capabilities really
    declared."""
    from sigate.ui.wmts_wms_widget import collect_layer_choices_for_gateway

    source = SourceConfig(
        key="test",
        display_name="Test",
        country="X",
        gateways=[GatewayConfig(gateway_type="wmts_wms", base_url="https://x/wmts")],
    )
    choices = collect_layer_choices_for_gateway(
        source,
        source.gateway("wmts_wms"),
        fetch=lambda url: _multi_layer_capabilities_xml(),
    )

    identifiers = {c.layer_info.identifier for c in choices}
    assert identifiers == {
        "ORTHOIMAGERY.ORTHOPHOTOS",
        "HR.ORTHOIMAGERY.ORTHOPHOTOS.L93",
        "GEOGRAPHICALGRIDSYSTEMS.MAPS",
    }


def test_collect_layer_choices_empty_when_capabilities_fetch_fails():
    """A capabilities-fetch failure must not raise out of the tab - it
    should just contribute no layers."""
    from sigate.ui.wmts_wms_widget import collect_layer_choices_for_gateway

    source = SourceConfig(
        key="test",
        display_name="Test",
        country="X",
        gateways=[GatewayConfig(gateway_type="wmts_wms", base_url="https://x/wmts")],
    )

    def failing_fetch(url):
        raise ConnectionError("simulated network failure")

    choices = collect_layer_choices_for_gateway(
        source, source.gateway("wmts_wms"), fetch=failing_fetch
    )
    assert choices == []


def test_widget_loads_ign_by_default_with_every_real_layer(qgis_app):
    widget = _make_widget(qgis_app, fetch=lambda url: _multi_layer_capabilities_xml())
    assert widget.connection_manager.current_connection().key == "ign_fr"
    assert len(widget.choices) == 3
    assert widget.list_widget.count() == 3


def test_layer_filter_narrows_the_list(qgis_app):
    widget = _make_widget(qgis_app, fetch=lambda url: _multi_layer_capabilities_xml())
    widget.layer_filter_edit.setText("Lambert")
    assert widget.list_widget.count() == 1
    assert "HR.ORTHOIMAGERY.ORTHOPHOTOS.L93" in widget.list_widget.item(0).text()


def test_add_to_map_falls_back_to_identifier_when_layer_has_no_title(qgis_app):
    """The other half of the same fix: a layer with no <ows:Title> at
    all (uncommon, but real servers vary) must still get a real,
    specific name - its own identifier - never silently falling back
    to the connection's display name either."""
    no_title_xml = b"""<?xml version="1.0"?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0" xmlns:ows="http://www.opengis.net/ows/1.1">
<Contents>
<Layer>
<ows:Identifier>SOME_LAYER_WITH_NO_TITLE</ows:Identifier>
<Style isDefault="true"><ows:Identifier>normal</ows:Identifier></Style>
<Format>image/jpeg</Format>
<TileMatrixSetLink><TileMatrixSet>PM</TileMatrixSet></TileMatrixSetLink>
</Layer>
</Contents>
</Capabilities>"""
    widget = _make_widget(qgis_app, fetch=lambda url: no_title_xml)
    widget.list_widget.setCurrentRow(0)

    captured = []
    widget.addRasterLayer.connect(
        lambda uri, name, provider: captured.append((uri, name, provider))
    )
    widget._on_add_clicked()

    assert captured[0][1] == "SOME_LAYER_WITH_NO_TITLE"


def test_add_to_map_emits_correct_uri_with_correct_crs_derived_from_real_capabilities(
    qgis_app,
):
    """Regression check for a real bug found during earlier development:
    this specific layer requires EPSG:2154 (Lambert-93), not the generic
    EPSG:3857 default. Unlike the original fix (a hardcoded crs value in
    seed config), this is now confirmed to work because the CRS is
    genuinely cross-referenced from the server's own declared tile matrix
    set definitions - it would work correctly for any layer, not only
    this one specifically hardcoded example."""
    widget = _make_widget(qgis_app, fetch=lambda url: _multi_layer_capabilities_xml())

    target_row = next(
        i
        for i in range(widget.list_widget.count())
        if "HR.ORTHOIMAGERY.ORTHOPHOTOS.L93" in widget.list_widget.item(i).text()
    )
    widget.list_widget.setCurrentRow(target_row)

    captured = []
    widget.addRasterLayer.connect(
        lambda uri, name, provider: captured.append((uri, name, provider))
    )
    widget._on_add_clicked()

    assert len(captured) == 1
    uri, name, provider = captured[0]
    assert provider == "wms"
    # Regression test for the actual reported bug ("the layer is named
    # after the gateway and not the layer name"): must be this specific
    # layer's own real title (confirmed real, from the fixture's own
    # <ows:Title>) - not choice.source_display_name, which every layer
    # from this same connection would otherwise share.
    assert name == "Photographies aeriennes Lambert 93"
    assert "crs=EPSG:2154" in uri
    assert "tileMatrixSet=2154_10cm_10_20" in uri
    assert "styles=normal" in uri


def test_add_to_map_preserves_a_non_epsg_urn_authority_declared_by_the_server(
    qgis_app,
):
    """Regression test for the actual reported bug, corrected a second
    time: adding IGN's private WMTS SCAN25TOUR layer produced the
    invalid "crs=EPSG:LAMB93" in the resulting QGIS connection string.
    Confirmed, via QGIS's own native "Add Layer from WMS/WMTS" dialog
    connecting to this exact same layer, that the real, expected value
    is "IGNF:LAMB93" - IGN's own "IGNF" authority, not any EPSG code at
    all. The TileMatrixSet's real declared SupportedCRS is
    "urn:ogc:def:crs:IGNF::LAMB93" - the authority must be preserved
    from the URN as declared, not assumed to always be EPSG."""
    non_standard_crs_xml = b"""<?xml version="1.0"?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0" xmlns:ows="http://www.opengis.net/ows/1.1">
<Contents>
<Layer>
<ows:Title>SCAN25 Touristique</ows:Title>
<ows:Identifier>GEOGRAPHICALGRIDSYSTEMS.MAPS.SCAN25TOUR.L93</ows:Identifier>
<Style isDefault="true"><ows:Identifier>normal</ows:Identifier></Style>
<Format>image/png</Format>
<TileMatrixSetLink><TileMatrixSet>LAMB93_2.5m_3_16</TileMatrixSet></TileMatrixSetLink>
</Layer>
<TileMatrixSet>
<ows:Identifier>LAMB93_2.5m_3_16</ows:Identifier>
<ows:SupportedCRS>urn:ogc:def:crs:IGNF::LAMB93</ows:SupportedCRS>
</TileMatrixSet>
</Contents>
</Capabilities>"""
    widget = _make_widget(qgis_app, fetch=lambda url: non_standard_crs_xml)
    widget.list_widget.setCurrentRow(0)

    captured = []
    widget.addRasterLayer.connect(
        lambda uri, name, provider: captured.append((uri, name, provider))
    )
    widget._on_add_clicked()

    uri = captured[0][0]
    assert "crs=IGNF:LAMB93" in uri
    assert "crs=EPSG:LAMB93" not in uri  # the actual originally reported bug


def test_add_to_map_for_web_mercator_layer_uses_web_mercator_crs(qgis_app):
    """The other side of the same regression: a genuinely different
    layer, using a genuinely different tile matrix set, must resolve to
    its own correct CRS - not a value copied from another layer."""
    widget = _make_widget(qgis_app, fetch=lambda url: _multi_layer_capabilities_xml())

    target_row = next(
        i
        for i in range(widget.list_widget.count())
        if widget.list_widget.item(i).text().startswith("ORTHOIMAGERY.ORTHOPHOTOS -")
    )
    widget.list_widget.setCurrentRow(target_row)

    captured = []
    widget.addRasterLayer.connect(
        lambda uri, name, provider: captured.append((uri, name, provider))
    )
    widget._on_add_clicked()

    uri, _, _ = captured[0]
    assert "crs=EPSG:3857" in uri
    assert "tileMatrixSet=PM" in uri


def test_widget_shows_two_distinct_ign_entries_public_and_private(qgis_app):
    """The actual bug reported live, at the widget level: IGN declares
    two wmts_wms gateway instances (public opendata, private
    apikey-gated) - the combo must offer both as separate entries, not
    collapse to a single "IGN (France)" entry with layers silently
    merged behind the scenes."""
    widget = _make_widget(qgis_app, fetch=lambda url: _multi_layer_capabilities_xml())
    combo = widget.connection_manager.combo
    ign_entries = [
        combo.itemText(i) for i in range(combo.count()) if "IGN" in combo.itemText(i)
    ]
    assert len(ign_entries) == 2
    assert any("public" in label for label in ign_entries)
    assert any("private" in label for label in ign_entries)


def test_switching_between_ign_public_and_private_uses_the_right_gateway(qgis_app):
    """Selecting the private IGN entry must query the private URL only,
    and selecting the public entry must query the public URL only - not
    both merged together, which was the reported bug."""
    requested_urls = []

    def fetch(url):
        requested_urls.append(url)
        return _multi_layer_capabilities_xml()

    widget = _make_widget(qgis_app, fetch=fetch)
    combo = widget.connection_manager.combo

    private_index = next(
        i for i in range(combo.count()) if "private" in combo.itemText(i)
    )
    requested_urls.clear()
    combo.setCurrentIndex(private_index)
    assert all("private" in url for url in requested_urls)

    public_index = next(
        i for i in range(combo.count()) if "public" in combo.itemText(i)
    )
    requested_urls.clear()
    combo.setCurrentIndex(public_index)
    assert all("private" not in url for url in requested_urls)


def test_switching_connection_updates_the_layer_list(qgis_app, tmp_path):
    """Confirms the tab actually reacts to a different connection being
    selected, not just to whatever was loaded on construction."""
    from sigate.sources.store import add_or_replace_source

    other_source = SourceConfig(
        key="other",
        display_name="Other Country",
        country="Y",
        gateways=[
            GatewayConfig(gateway_type="wmts_wms", base_url="https://other.test/wmts")
        ],
    )
    add_or_replace_source(tmp_path, other_source)

    other_capabilities = b"""<?xml version="1.0"?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0" xmlns:ows="http://www.opengis.net/ows/1.1">
<Contents>
<Layer>
<ows:Title>Other Layer</ows:Title>
<ows:Identifier>OTHER_LAYER</ows:Identifier>
<Style isDefault="true"><ows:Identifier>normal</ows:Identifier></Style>
<Format>image/png</Format>
<TileMatrixSetLink><TileMatrixSet>PM</TileMatrixSet></TileMatrixSetLink>
</Layer>
</Contents>
</Capabilities>"""

    def fetch(url):
        return (
            other_capabilities
            if "other.test" in url
            else _multi_layer_capabilities_xml()
        )

    widget = _make_widget(qgis_app, fetch=fetch)
    widget.connection_manager.data_dir_provider = lambda: tmp_path
    widget.connection_manager.reload_connections(select_key="other")

    assert widget.connection_manager.current_connection().key == "other"
    assert len(widget.choices) == 1
    assert widget.choices[0].layer_info.identifier == "OTHER_LAYER"


class _FakeMapSettings:
    def __init__(self, crs):
        self._crs = crs

    def destinationCrs(self):
        return self._crs


class _FakeMapCanvas:
    def __init__(self, extent, crs):
        self._extent = extent
        self._crs = crs

    def extent(self):
        return self._extent

    def mapSettings(self):
        return _FakeMapSettings(self._crs)


class _FakeIface:
    def __init__(self, canvas):
        self._canvas = canvas

    def mapCanvas(self):
        return self._canvas


def _install_fake_canvas_matching_layer_crs(monkeypatch, qgis_app):
    """A canvas whose own CRS already matches the selected layer's CRS
    (both EPSG:3857, matching the first layer in
    _multi_layer_capabilities_xml) - makes the reprojection step an
    identity transform, so the resulting bbox is deterministic and
    simple to assert on without needing real cross-CRS transform data."""
    from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle

    import sigate.ui.wmts_wms_widget as widget_module

    extent = QgsRectangle(100.0, 200.0, 300.0, 400.0)
    crs = QgsCoordinateReferenceSystem("EPSG:3857")
    fake_iface = _FakeIface(_FakeMapCanvas(extent, crs))
    monkeypatch.setattr(widget_module, "iface", fake_iface)
    return extent


def test_export_shows_message_when_no_layer_selected(qgis_app, monkeypatch):
    from qgis.PyQt.QtWidgets import QMessageBox

    shown = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda *a, **k: shown.append(True)),
    )
    widget = _make_widget(qgis_app, fetch=lambda url: _multi_layer_capabilities_xml())
    widget.list_widget.setCurrentItem(None)

    widget._on_export_clicked()

    assert shown == [True]


def test_export_reprojects_canvas_extent_and_calls_run_modal(qgis_app, monkeypatch):
    from qgis.PyQt.QtWidgets import QFileDialog

    import sigate.ui.wmts_wms_widget as widget_module

    extent = _install_fake_canvas_matching_layer_crs(monkeypatch, qgis_app)
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: ("/tmp/out.tif", "GeoTIFF (*.tif)")),
    )
    captured = {}

    def fake_run_modal(parent, **kwargs):
        captured.update(kwargs)
        return True, None, False

    monkeypatch.setattr(widget_module, "run_modal", fake_run_modal)

    widget = _make_widget(qgis_app, fetch=lambda url: _multi_layer_capabilities_xml())
    widget.list_widget.setCurrentRow(0)  # ORTHOIMAGERY.ORTHOPHOTOS, CRS PM/EPSG:3857

    added = []
    widget.addRasterLayer.connect(
        lambda uri, name, provider: added.append((uri, name, provider))
    )

    widget._on_export_clicked()

    assert captured["layer"] == "ORTHOIMAGERY.ORTHOPHOTOS"
    assert captured["out_path"] == "/tmp/out.tif"
    assert captured["bbox_crs"] == "EPSG:3857"
    assert captured["tmp_dir"] == "/tmp"  # the output file's own directory
    assert Path(captured["def_path"]).parent == Path(
        "/tmp"
    )  # regression: was the QGIS profile settings dir, flagged as unacceptable
    assert captured["bbox"] == pytest.approx(
        (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())
    )
    assert added == [("/tmp/out.tif", "out", "gdal")]
    assert "Exported" in widget.status_label.text()


def test_export_appends_tif_extension_if_missing(qgis_app, monkeypatch):
    from qgis.PyQt.QtWidgets import QFileDialog

    import sigate.ui.wmts_wms_widget as widget_module

    _install_fake_canvas_matching_layer_crs(monkeypatch, qgis_app)
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: ("/tmp/out", "GeoTIFF (*.tif)")),
    )
    captured = {}

    def fake_run_modal(parent, **kwargs):
        captured.update(kwargs)
        return True, None, False

    monkeypatch.setattr(widget_module, "run_modal", fake_run_modal)

    widget = _make_widget(qgis_app, fetch=lambda url: _multi_layer_capabilities_xml())
    widget.list_widget.setCurrentRow(0)
    widget._on_export_clicked()

    assert captured["out_path"] == "/tmp/out.tif"


def test_export_does_nothing_when_file_dialog_is_cancelled(qgis_app, monkeypatch):
    from qgis.PyQt.QtWidgets import QFileDialog

    import sigate.ui.wmts_wms_widget as widget_module

    _install_fake_canvas_matching_layer_crs(monkeypatch, qgis_app)
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", ""))
    )

    def run_modal_should_not_be_called(parent, **kwargs):
        raise AssertionError("must not run the export if no path was chosen")

    monkeypatch.setattr(widget_module, "run_modal", run_modal_should_not_be_called)

    widget = _make_widget(qgis_app, fetch=lambda url: _multi_layer_capabilities_xml())
    widget.list_widget.setCurrentRow(0)
    widget._on_export_clicked()  # must not raise


def test_export_reports_failure_via_message_box(qgis_app, monkeypatch):
    from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox

    import sigate.ui.wmts_wms_widget as widget_module

    _install_fake_canvas_matching_layer_crs(monkeypatch, qgis_app)
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: ("/tmp/out.tif", "")),
    )
    monkeypatch.setattr(
        widget_module,
        "run_modal",
        lambda parent, **k: (False, RuntimeError("gdal: command not found"), False),
    )
    shown = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *a, **k: shown.append(a[2] if len(a) > 2 else "")),
    )

    widget = _make_widget(qgis_app, fetch=lambda url: _multi_layer_capabilities_xml())
    widget.list_widget.setCurrentRow(0)
    widget._on_export_clicked()

    assert len(shown) == 1
    assert "gdal: command not found" in shown[0]


def test_export_reports_cancellation_via_status_label(qgis_app, monkeypatch):
    from qgis.PyQt.QtWidgets import QFileDialog

    import sigate.ui.wmts_wms_widget as widget_module

    _install_fake_canvas_matching_layer_crs(monkeypatch, qgis_app)
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: ("/tmp/out.tif", "")),
    )
    monkeypatch.setattr(
        widget_module, "run_modal", lambda parent, **k: (False, None, True)
    )

    widget = _make_widget(qgis_app, fetch=lambda url: _multi_layer_capabilities_xml())
    widget.list_widget.setCurrentRow(0)
    widget._on_export_clicked()

    assert "cancelled" in widget.status_label.text().lower()


def _set_levels(widget, tilematrixset_id, levels):
    """Like _set_finest_resolution, but for the multi-level picker path -
    replaces a LayerChoice's tilematrixset info with one carrying real
    WmtsTileMatrixLevel entries."""
    from qgis.PyQt.QtCore import Qt

    from sigate.gateways.wmts_wms import WmtsTileMatrixSetInfo

    choice = widget.list_widget.currentItem().data(Qt.ItemDataRole.UserRole)
    existing = choice.tilematrixsets[tilematrixset_id]
    choice.tilematrixsets[tilematrixset_id] = WmtsTileMatrixSetInfo(
        identifier=existing.identifier,
        crs=existing.crs,
        levels=levels,
        finest_resolution=min(lv.resolution for lv in levels) if levels else None,
    )


def test_export_shows_zoom_level_picker_when_multiple_levels_exist(
    qgis_app, monkeypatch
):
    """The actual feature requested directly ("if we can compute size we
    should perhaps give the choice between different zoom levels if
    they exist with the different sizes"): more than one real zoom level
    means a picker, not just a size-threshold warning."""
    from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox

    import sigate.ui.wmts_wms_widget as widget_module
    from sigate.gateways.wmts_wms import WmtsTileMatrixLevel

    _install_fake_canvas_matching_layer_crs(monkeypatch, qgis_app)
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: ("/tmp/out.tif", "")),
    )

    def question_should_not_be_called(*a, **k):
        raise AssertionError(
            "with multiple levels, the picker replaces the threshold warning"
        )

    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(question_should_not_be_called)
    )

    picker_calls = []

    def fake_choose_zoom_level(parent, levels, bbox):
        picker_calls.append((levels, bbox))
        return "18"

    monkeypatch.setattr(widget_module, "choose_zoom_level", fake_choose_zoom_level)

    captured = {}
    monkeypatch.setattr(
        widget_module,
        "run_modal",
        lambda parent, **k: (captured.update(k), (True, None, False))[1],
    )

    widget = _make_widget(qgis_app, fetch=lambda url: _multi_layer_capabilities_xml())
    widget.list_widget.setCurrentRow(0)
    levels = [
        WmtsTileMatrixLevel(identifier="10", resolution=100.0),
        WmtsTileMatrixLevel(identifier="18", resolution=0.2),
    ]
    _set_levels(widget, "PM", levels)

    widget._on_export_clicked()

    assert len(picker_calls) == 1
    assert picker_calls[0][0] == levels
    assert captured["tilematrix"] == "18"


def test_export_cancelled_from_zoom_level_picker_does_not_run(qgis_app, monkeypatch):
    from qgis.PyQt.QtWidgets import QFileDialog

    import sigate.ui.wmts_wms_widget as widget_module
    from sigate.gateways.wmts_wms import WmtsTileMatrixLevel

    _install_fake_canvas_matching_layer_crs(monkeypatch, qgis_app)
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: ("/tmp/out.tif", "")),
    )
    monkeypatch.setattr(
        widget_module, "choose_zoom_level", lambda parent, levels, bbox: None
    )

    def run_modal_should_not_be_called(parent, **k):
        raise AssertionError("must not export after cancelling the zoom-level picker")

    monkeypatch.setattr(widget_module, "run_modal", run_modal_should_not_be_called)

    widget = _make_widget(qgis_app, fetch=lambda url: _multi_layer_capabilities_xml())
    widget.list_widget.setCurrentRow(0)
    _set_levels(
        widget,
        "PM",
        [
            WmtsTileMatrixLevel(identifier="10", resolution=100.0),
            WmtsTileMatrixLevel(identifier="18", resolution=0.2),
        ],
    )

    widget._on_export_clicked()  # must not raise


def test_export_with_a_single_level_does_not_show_the_picker(qgis_app, monkeypatch):
    """A single real level (or none) keeps the simpler threshold-only
    warning, not the picker - nothing meaningful to choose between."""
    from qgis.PyQt.QtWidgets import QFileDialog

    import sigate.ui.wmts_wms_widget as widget_module
    from sigate.gateways.wmts_wms import WmtsTileMatrixLevel

    _install_fake_canvas_matching_layer_crs(monkeypatch, qgis_app)
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: ("/tmp/out.tif", "")),
    )

    def picker_should_not_be_called(parent, levels, bbox):
        raise AssertionError("a single level must not show the picker")

    monkeypatch.setattr(widget_module, "choose_zoom_level", picker_should_not_be_called)
    monkeypatch.setattr(
        widget_module, "run_modal", lambda parent, **k: (True, None, False)
    )

    widget = _make_widget(qgis_app, fetch=lambda url: _multi_layer_capabilities_xml())
    widget.list_widget.setCurrentRow(0)
    _set_levels(widget, "PM", [WmtsTileMatrixLevel(identifier="18", resolution=1000.0)])

    widget._on_export_clicked()  # must not raise


def test_export_uses_apikey_header_for_gated_gateway(qgis_app, monkeypatch, tmp_path):
    """The other real gap this needs to avoid: a gateway requiring an
    apikey header must have that header attached to GDAL's own request
    mechanism (GDAL_HTTP_HEADERS), not silently dropped - this module
    talks to GDAL's CLI tools directly, not through QGIS's own provider,
    so QGIS's authcfg mechanism (used for Add to map) doesn't apply
    here."""
    from qgis.PyQt.QtWidgets import QFileDialog

    import sigate.ui.wmts_wms_widget as widget_module
    from sigate.sources.store import add_or_replace_source

    _install_fake_canvas_matching_layer_crs(monkeypatch, qgis_app)
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: ("/tmp/out.tif", "")),
    )
    captured = {}

    def fake_run_modal(parent, **k):
        captured.update(k)
        return True, None, False

    monkeypatch.setattr(widget_module, "run_modal", fake_run_modal)

    add_or_replace_source(
        tmp_path,
        SourceConfig(
            key="gated",
            display_name="Gated",
            country="X",
            gateways=[
                GatewayConfig(
                    gateway_type="wmts_wms",
                    base_url="https://private.test/wmts",
                    auth=AuthConfig(
                        kind="apikey_header", header_name="apikey", value="secret"
                    ),
                )
            ],
        ),
    )

    widget = _make_widget(qgis_app, fetch=lambda url: _multi_layer_capabilities_xml())
    widget.connection_manager.data_dir_provider = lambda: tmp_path
    widget.connection_manager.reload_connections(select_key="gated")
    widget.list_widget.setCurrentRow(0)

    widget._on_export_clicked()

    assert captured["gdal_config"] == [
        "--config",
        "GDAL_HTTP_HEADERS",
        "apikey: secret",
    ]


def _set_finest_resolution(widget, tilematrixset_id, resolution):
    """Replaces a LayerChoice's tilematrixset info with one carrying a
    known finest_resolution - simpler than crafting real GetCapabilities
    XML with TileMatrix/ScaleDenominator levels for each test, and
    exercises the exact same widget-level logic either way."""
    from qgis.PyQt.QtCore import Qt

    from sigate.gateways.wmts_wms import WmtsTileMatrixSetInfo

    choice = widget.list_widget.currentItem().data(Qt.ItemDataRole.UserRole)
    existing = choice.tilematrixsets[tilematrixset_id]
    choice.tilematrixsets[tilematrixset_id] = WmtsTileMatrixSetInfo(
        identifier=existing.identifier,
        crs=existing.crs,
        finest_resolution=resolution,
    )


def test_export_warns_before_a_genuinely_huge_area_and_proceeds_on_yes(
    qgis_app, monkeypatch
):
    """Requested directly ("we should perhaps do something to avoid such
    sizes to involuntarily be downloaded"). The fake canvas extent is
    200 x 200 map units (_install_fake_canvas_matching_layer_crs); at an
    unrealistically fine 0.001 resolution this is ~40 GB uncompressed,
    comfortably over the default 1 GB warning threshold."""
    from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox

    import sigate.ui.wmts_wms_widget as widget_module

    _install_fake_canvas_matching_layer_crs(monkeypatch, qgis_app)
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: ("/tmp/out.tif", "")),
    )
    questions_shown = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(
            lambda *a, **k: (
                questions_shown.append(True),
                QMessageBox.StandardButton.Yes,
            )[1]
        ),
    )
    captured = {}
    monkeypatch.setattr(
        widget_module,
        "run_modal",
        lambda parent, **k: (captured.update(k), (True, None, False))[1],
    )

    widget = _make_widget(qgis_app, fetch=lambda url: _multi_layer_capabilities_xml())
    widget.list_widget.setCurrentRow(0)
    _set_finest_resolution(widget, "PM", 0.001)

    widget._on_export_clicked()

    assert len(questions_shown) == 1
    assert captured["out_path"] == "/tmp/out.tif"  # proceeded after "Yes"


def test_export_warning_declined_does_not_run_the_export(qgis_app, monkeypatch):
    from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox

    import sigate.ui.wmts_wms_widget as widget_module

    _install_fake_canvas_matching_layer_crs(monkeypatch, qgis_app)
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: ("/tmp/out.tif", "")),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )

    def run_modal_should_not_be_called(parent, **k):
        raise AssertionError("must not export after declining the size warning")

    monkeypatch.setattr(widget_module, "run_modal", run_modal_should_not_be_called)

    widget = _make_widget(qgis_app, fetch=lambda url: _multi_layer_capabilities_xml())
    widget.list_widget.setCurrentRow(0)
    _set_finest_resolution(widget, "PM", 0.001)

    widget._on_export_clicked()  # must not raise


def test_export_does_not_warn_for_a_small_area(qgis_app, monkeypatch):
    """The common case must stay frictionless - a small, ordinary export
    shouldn't trigger any confirmation dialog at all."""
    from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox

    import sigate.ui.wmts_wms_widget as widget_module

    _install_fake_canvas_matching_layer_crs(monkeypatch, qgis_app)
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: ("/tmp/out.tif", "")),
    )

    def question_should_not_be_called(*a, **k):
        raise AssertionError("must not warn for a small, ordinary export")

    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(question_should_not_be_called)
    )
    monkeypatch.setattr(
        widget_module, "run_modal", lambda parent, **k: (True, None, False)
    )

    widget = _make_widget(qgis_app, fetch=lambda url: _multi_layer_capabilities_xml())
    widget.list_widget.setCurrentRow(0)
    _set_finest_resolution(widget, "PM", 1000.0)  # coarse - tiny estimated size

    widget._on_export_clicked()  # must not raise


def test_export_skips_size_check_when_finest_resolution_is_unknown(
    qgis_app, monkeypatch
):
    """A server whose GetCapabilities had no usable TileMatrix/
    ScaleDenominator entries (finest_resolution stays None) must not
    block the export or crash - the check is skipped entirely rather
    than guessing at a resolution."""
    from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox

    import sigate.ui.wmts_wms_widget as widget_module

    _install_fake_canvas_matching_layer_crs(monkeypatch, qgis_app)
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: ("/tmp/out.tif", "")),
    )

    def question_should_not_be_called(*a, **k):
        raise AssertionError("must not warn when resolution is unknown")

    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(question_should_not_be_called)
    )
    monkeypatch.setattr(
        widget_module, "run_modal", lambda parent, **k: (True, None, False)
    )

    widget = _make_widget(qgis_app, fetch=lambda url: _multi_layer_capabilities_xml())
    widget.list_widget.setCurrentRow(0)
    # _multi_layer_capabilities_xml's tilematrixsets have no TileMatrix
    # levels at all - finest_resolution is already None, untouched here.

    widget._on_export_clicked()  # must not raise
