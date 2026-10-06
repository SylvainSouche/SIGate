"""
Tests for the WFS tab, run against a real headless QGIS application (see
conftest.py's qgis_app fixture). Network access is replaced with an
injected fetch function.
"""

from urllib.parse import unquote_plus

from qgis.core import QgsDataSourceUri
import pytest


def _sync_progress_runner(parent, items, central_repo, download_fn):
    """A synchronous stand-in for the real progress_runner (a modal
    QDialog backed by a background QgsTask) - the real one blocks on
    QDialog.exec() until a background task finishes, which has nothing
    to drive it forward deterministically in a headless test. Calls
    download.pipeline.download_items() directly instead."""
    from sigate.download import pipeline

    outcomes = pipeline.download_items(items, central_repo, download_fn=download_fn)
    return outcomes, False


@pytest.fixture(autouse=True)
def clean_target_picker_persistence(qgis_app):
    """WfsSourceSelectWidget now embeds a TargetPicker (for the
    file-index download case), which remembers the last-used central-
    repo/destination paths across sessions - without resetting this
    between tests, one test's path can leak into another's fresh widget
    via that persisted setting, exactly as it did for the Bulk Listing
    tab's own tests before this same fixture was added there."""
    from sigate.ui import settings as sigate_settings

    sigate_settings.set_last_central_repo(None)
    sigate_settings.set_last_destination(None)
    yield
    sigate_settings.set_last_central_repo(None)
    sigate_settings.set_last_destination(None)


def _capabilities_xml():
    return b"""<?xml version="1.0"?>
<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0">
<wfs:FeatureTypeList>
<wfs:FeatureType xmlns:IGNF_MNS-LIDAR-HD="http://IGNF_MNS-LIDAR-HD">
<wfs:Name>IGNF_MNS-LIDAR-HD:bloc</wfs:Name>
<wfs:Title>LiDAR HD - Blocs MNS</wfs:Title>
</wfs:FeatureType>
<wfs:FeatureType>
<wfs:Name>IGNF_MNS-LIDAR-HD:dalle</wfs:Name>
<wfs:Title>LiDAR HD - Dalles MNS</wfs:Title>
</wfs:FeatureType>
</wfs:FeatureTypeList>
</wfs:WFS_Capabilities>"""


def _make_widget(qgis_app, fetch):
    from qgis.PyQt.QtCore import Qt

    from sigate.ui.wfs_widget import WfsSourceSelectWidget

    return WfsSourceSelectWidget(None, Qt.WindowType(0), fetch=fetch)


def test_build_wfs_uri_produces_correct_parameters():
    from sigate.ui.wfs_widget import build_wfs_uri

    uri_string = build_wfs_uri(
        "https://data.geopf.fr/wfs/ows",
        typename="IGNF_MNS-LIDAR-HD:dalle",
        srsname="EPSG:2154",
    )
    decoded = QgsDataSourceUri(uri_string)
    assert decoded.param("url") == "https://data.geopf.fr/wfs/ows"
    assert decoded.param("typename") == "IGNF_MNS-LIDAR-HD:dalle"
    assert decoded.param("srsname") == "EPSG:2154"
    assert decoded.param("version") == "2.0.0"
    assert decoded.param("pagingEnabled") == "true"
    assert decoded.param("restrictToRequestBBOX") == "1"  # the default


def test_build_wfs_uri_restrict_to_bbox_can_be_disabled():
    """Regression test for a real reported failure: an id-list-based
    selection filter is already a complete, canvas-independent
    selector - restricting the request to the current viewport on top
    of it is both semantically wrong (could silently drop a selected
    feature that's since scrolled out of view) and, for a real, long
    id list, adds unnecessary request length that plausibly contributed
    to an actual "empty response: Unknown error" failure."""
    from sigate.ui.wfs_widget import build_wfs_uri

    uri_string = build_wfs_uri(
        "https://data.geopf.fr/wfs/ows",
        typename="test:layer",
        restrict_to_bbox=False,
    )
    decoded = QgsDataSourceUri(uri_string)
    assert decoded.param("restrictToRequestBBOX") == "0"


def test_build_wfs_uri_correctly_escapes_a_filter_with_an_embedded_quote():
    """Regression check: a filter value containing an embedded single
    quote must round-trip correctly rather than breaking the connection
    string's own quoting."""
    from sigate.ui.wfs_widget import build_wfs_uri

    uri_string = build_wfs_uri(
        "https://data.geopf.fr/wfs/ows",
        typename="test:layer",
        filter_expression="name = 'D003'",
    )
    decoded = QgsDataSourceUri(uri_string)
    assert decoded.param("filter") == "name = 'D003'"


def test_widget_construction_registers_all_three_providers(qgis_app):
    from qgis.gui import QgsGui

    from sigate.plugin import SigatePlugin

    registry = QgsGui.sourceSelectProviderRegistry()
    before = len(registry.providers())
    plugin = SigatePlugin(iface=None)
    plugin.initGui()
    try:
        assert len(registry.providers()) == before + 4
        assert len(registry.providersByKey("sigate_wfs")) == 1
        assert len(registry.providersByKey("sigate_arcgis_rest")) == 1
    finally:
        plugin.unload()
    assert len(registry.providers()) == before


def test_providers_create_widgets_from_the_widget_mode_qgis_supplies(
    qgis_app, monkeypatch
):
    """QGIS's Data Source Manager hands each provider a real
    QgsProviderRegistry.WidgetMode enum member, which the widgets must
    forward to QgsAbstractDataSourceWidget as-is (a plain int is rejected
    by QGIS 4 / Qt 6). The other tests here construct widgets without one,
    so this covers the path QGIS itself takes.

    A provider can't be handed a fake fetch function, so the widgets fall
    back to the real HTTP transport while populating - blocked here so the
    test never touches (or waits on) the network."""
    import urllib.error

    from qgis.core import QgsProviderRegistry
    from qgis.PyQt.QtCore import Qt

    from sigate.ui.bulk_listing_provider import BulkListingSourceSelectProvider
    from sigate.ui.wfs_provider import WfsSourceSelectProvider
    from sigate.ui.wmts_wms_provider import WmtsWmsSourceSelectProvider

    def no_network(*args, **kwargs):
        raise urllib.error.URLError("network blocked in test")

    # Both gateway transports import urlopen by name, so patch each binding.
    monkeypatch.setattr("sigate.gateways.atom.urlopen", no_network)
    monkeypatch.setattr("sigate.gateways.stac.urlopen", no_network)
    widget_mode = QgsProviderRegistry.WidgetMode(0)
    for provider_cls in (
        WmtsWmsSourceSelectProvider,
        BulkListingSourceSelectProvider,
        WfsSourceSelectProvider,
    ):
        widget = provider_cls().createDataSourceWidget(
            None, Qt.WindowType(0), widget_mode
        )
        assert widget is not None, provider_cls.__name__


def test_widget_loads_feature_types_via_injected_fetch(qgis_app):
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    assert [(ft.name, ft.title) for ft in widget.feature_types] == [
        ("IGNF_MNS-LIDAR-HD:bloc", "LiDAR HD - Blocs MNS"),
        ("IGNF_MNS-LIDAR-HD:dalle", "LiDAR HD - Dalles MNS"),
    ]
    assert widget.list_widget.count() == 2


def test_layer_filter_narrows_the_list(qgis_app):
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.layer_filter_edit.setText("dalle")
    assert widget.list_widget.count() == 1
    assert "dalle" in widget.list_widget.item(0).text()


def test_url_field_for_detects_a_download_link_case_insensitively():
    from sigate.ui.wfs_widget import url_field_for

    assert url_field_for({"name": "x", "URL": "https://x/y.tif"}) == "URL"
    assert url_field_for({"name": "x", "href": "https://x/y.tif"}) == "href"
    assert url_field_for({"name": "x", "id": "137"}) is None


def test_query_features_populates_results_and_enables_download_when_url_field_present(
    qgis_app,
):
    """The gap this closes: WFS's file-index use pattern (confirmed real
    for IGN's LiDAR HD ":dalle" layer, where a feature's geometry is just
    a tile footprint and the real payload is a url attribute) was never
    reachable through the UI at all - only "Add to map" existed."""
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)  # the "dalle" entry

    csv_response = (
        "wkt_geom\tname\turl\tname_download\tid_chantier\n"
        "Polygon ((1 2, 3 4))\tLHD_FXX_1003_6558\thttps://data.geopf.fr/wms-r?X\tLHD_FXX_1003_6558.tif\t137\n"
    ).encode("utf-8")
    widget.fetch = lambda url: csv_response

    widget._on_query_clicked()

    assert len(widget.query_rows) == 1
    assert widget.query_rows[0]["id_chantier"] == "137"
    assert widget.results_tree.topLevelItemCount() == 1
    assert widget.download_button.isEnabled() is True


def test_query_features_does_not_enable_download_when_no_url_field(qgis_app):
    """The other side of the same case: a plain vector-layer-use feature
    type (e.g. administrative boundaries) has no url attribute at all -
    Download selected should stay disabled rather than offer something
    that can't work."""
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(0)  # the "bloc" entry

    csv_response = (
        "wkt_geom\tname\tid_chantier\nPolygon ((1 2, 3 4))\tBLOC_1\t137\n".encode(
            "utf-8"
        )
    )
    widget.fetch = lambda url: csv_response

    widget._on_query_clicked()

    assert widget.download_button.isEnabled() is False


def test_switching_feature_type_clears_stale_query_fieldnames(qgis_app):
    """Regression test for the actual reported bug: a real WFS filter
    kept failing with "Illegal property name: geom" even after geom
    field guessing was fixed to recognize the real column name for the
    target feature type - traced to query_fieldnames (used to guess the
    $geom substitution target) being left stale from whatever feature
    type was queried *before* switching, since _on_feature_type_changed
    only ever cleared the active filter text, not the field list itself
    - despite its own docstring already reasoning that a different
    feature type means "a different field schema." A field name that
    happens to be real on the previously-queried type (here: "geom"
    itself, deliberately - a real column name on plenty of layers) gets
    guessed with full, silent confidence for a completely different,
    now-selected feature type it was never actually queried against."""
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(0)  # the "bloc" entry

    csv_response = "geom\tname\nPolygon ((1 2, 3 4))\tBLOC_1\n".encode("utf-8")
    widget.fetch = lambda url: csv_response
    widget._on_query_clicked()
    assert widget.query_fieldnames == ["geom", "name"]

    widget.list_widget.setCurrentRow(1)  # switch to the "dalle" entry

    assert widget.query_fieldnames == []
    assert widget.query_rows == []
    assert widget._all_query_rows == []
    assert widget.results_tree.topLevelItemCount() == 0


def test_download_selected_downloads_the_linked_file_and_adds_a_layer(
    qgis_app, tmp_path, monkeypatch
):
    from qgis.PyQt.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    content = b"fake geotiff bytes"

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = dest_dir / filename
        path.write_bytes(content)
        return path

    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.download_fn = fake_download
    widget.progress_runner = _sync_progress_runner
    widget.list_widget.setCurrentRow(1)

    csv_response = (
        "wkt_geom\tname\turl\tname_download\n"
        "Polygon ((1 2, 3 4))\tLHD_FXX_1003_6558\thttps://data.geopf.fr/download/tile.tif\ttile.tif\n"
    ).encode("utf-8")
    widget.fetch = lambda url: csv_response
    widget._on_query_clicked()

    widget.results_tree.setCurrentItem(widget.results_tree.topLevelItem(0))
    widget.results_tree.topLevelItem(0).setSelected(True)
    widget.target_picker.repo_edit.setText(str(tmp_path))

    captured = []
    widget.addRasterLayer.connect(
        lambda uri, name, provider: captured.append((uri, name, provider))
    )
    widget._on_download_selected_clicked()

    downloaded_path = (
        tmp_path / "IGN (France)" / "raster" / "IGNF_MNS-LIDAR-HD_dalle" / "tile.tif"
    )
    assert downloaded_path.exists()
    assert len(captured) == 1
    assert captured[0] == (str(downloaded_path), "tile", "gdal")


def test_query_pagination_next_page_uses_start_index(qgis_app):
    calls = []

    def fake_fetch(url):
        calls.append(url)
        if "STARTINDEX" in url:
            return (
                "wkt_geom\tname\turl\nPolygon ((1 2, 3 4))\tTILE_B\thttps://x/b.tif\n"
            ).encode("utf-8")
        return (
            "wkt_geom\tname\turl\nPolygon ((1 2, 3 4))\tTILE_A\thttps://x/a.tif\n"
        ).encode("utf-8")

    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    widget.count_edit.setText("1")
    widget.fetch = fake_fetch
    widget._on_query_clicked()

    assert [r["name"] for r in widget.query_rows] == ["TILE_A"]
    assert (
        widget.next_page_button.isEnabled() is True
    )  # a full page came back - might be more

    widget._next_query_page()
    assert [r["name"] for r in widget.query_rows] == ["TILE_B"]
    assert widget.prev_page_button.isEnabled() is True

    widget._prev_query_page()
    assert [r["name"] for r in widget.query_rows] == ["TILE_A"]


def test_query_pagination_next_disabled_when_page_is_short(qgis_app):
    """A page shorter than requested means there's nothing more to page
    into - Next should disable rather than offer a page that would come
    back empty."""

    def fake_fetch(url):
        return (
            "wkt_geom\tname\turl\nPolygon ((1 2, 3 4))\tONLY_ONE\thttps://x/a.tif\n"
        ).encode("utf-8")

    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    widget.count_edit.setText(
        "200"
    )  # far more than the single row the fake server returns
    widget.fetch = fake_fetch
    widget._on_query_clicked()

    assert widget.next_page_button.isEnabled() is False


def test_result_filter_narrows_current_page_without_extra_fetch(qgis_app):
    calls = []

    def fake_fetch(url):
        calls.append(url)
        return (
            "wkt_geom\tname\turl\n"
            "Polygon ((1 2, 3 4))\tLHD_D003\thttps://x/a.tif\n"
            "Polygon ((1 2, 3 4))\tLHD_D074\thttps://x/b.tif\n"
        ).encode("utf-8")

    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    widget.fetch = fake_fetch
    widget._on_query_clicked()
    calls_after_query = len(calls)

    widget.result_filter_edit.setText("D003")
    assert [r["name"] for r in widget.query_rows] == ["LHD_D003"]
    assert len(calls) == calls_after_query, (
        "current-page result filtering must not trigger any new fetch"
    )

    widget.result_filter_edit.setText("")
    assert len(widget.query_rows) == 2


def test_search_all_pages_finds_feature_beyond_current_page(qgis_app):
    """The gap this closes: querying only ever returned the first page,
    with no way to find a feature sitting further in a large layer."""
    page_1 = (
        "wkt_geom\tname\turl\nPolygon ((1 2, 3 4))\tLHD_D001\thttps://x/a.tif\n"
    ).encode("utf-8")
    page_2 = (
        "wkt_geom\tname\turl\nPolygon ((1 2, 3 4))\tLHD_D999\thttps://x/z.tif\n"
    ).encode("utf-8")
    empty_page = "wkt_geom\tname\turl\n".encode("utf-8")

    def fake_fetch(url):
        if "STARTINDEX=1" in url:
            return page_2
        if "STARTINDEX=2" in url:
            return empty_page
        return page_1

    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    widget.count_edit.setText("1")
    widget.fetch = fake_fetch
    widget._on_query_clicked()
    assert [r["name"] for r in widget.query_rows] == ["LHD_D001"]  # only page 1 so far

    widget.result_filter_edit.setText("D999")
    widget._search_all_pages()

    assert widget.query_filtered_mode is True
    assert [r["name"] for r in widget.query_rows] == ["LHD_D999"]
    assert widget.prev_page_button.isEnabled() is False
    assert widget.next_page_button.isEnabled() is False

    widget._clear_result_filter()
    assert widget.query_filtered_mode is False


def test_download_all_pages_fetches_everything_and_downloads_all_linked_files(
    qgis_app, tmp_path, monkeypatch
):
    """Also the actual regression test for a real reported gap: two
    downloaded files sharing the same raster extension - exactly what a
    WFS file-index download (e.g. LiDAR HD's ":dalle" layer) delivers,
    plain files never wrapped in an archive - must now trigger the same
    mosaic offer archive-extracted tiles already got, not silently skip
    it. gdalbuildvrt/gdaladdo aren't available for real in this
    environment (the same real limitation this project's mosaic-building
    has always had), so both are monkeypatched to confirm the wiring."""
    from qgis.PyQt.QtWidgets import QMessageBox

    import sigate.ui.download_flow as download_flow_module

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    mosaic_path = tmp_path / "sigate_mosaic_a.vrt"
    overview_calls = []
    monkeypatch.setattr(
        download_flow_module,
        "build_vrt_mosaic",
        lambda tile_paths, dest_dir, product_key, log=None: mosaic_path,
    )
    monkeypatch.setattr(
        download_flow_module,
        "build_overviews",
        lambda path, log=None: overview_calls.append(path),
    )

    page_1 = (
        "wkt_geom\tname\turl\tname_download\n"
        "Polygon ((1 2, 3 4))\tA\thttps://x/a.tif\ta.tif\n"
    ).encode("utf-8")
    page_2 = (
        "wkt_geom\tname\turl\tname_download\n"
        "Polygon ((1 2, 3 4))\tB\thttps://x/b.tif\tb.tif\n"
    ).encode("utf-8")
    empty_page = "wkt_geom\tname\turl\tname_download\n".encode("utf-8")

    def fake_fetch(url):
        if "STARTINDEX=1" in url:
            return page_2
        if "STARTINDEX=2" in url:
            return empty_page
        return page_1

    downloaded_files = {}

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = dest_dir / filename
        path.write_bytes(b"content")
        downloaded_files[filename] = path
        return path

    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    widget.count_edit.setText("1")
    widget.fetch = fake_fetch
    widget.download_fn = fake_download
    widget.progress_runner = _sync_progress_runner
    widget.target_picker.repo_edit.setText(str(tmp_path))

    captured = []
    widget.addRasterLayer.connect(lambda uri, name, provider: captured.append(name))
    widget._on_download_all_pages_clicked()

    # both files were still genuinely downloaded (pagination worked)...
    assert set(downloaded_files.keys()) == {"a.tif", "b.tif"}
    # ...but added as one mosaic, not two independent layers, and its
    # pyramid was built too.
    assert captured == [mosaic_path.stem]
    assert overview_calls == [mosaic_path]


def test_current_geometry_field_prefers_the_real_describefeaturetype_answer(qgis_app):
    """Regression test for the actual, repeatedly-reported bug: even
    once guess_geometry_field's own candidate list was corrected (real
    IGN convention added), the guess only ever mattered when
    query_fieldnames happened to already reflect the right feature
    type - a real, separate source of failure. This is the actual fix:
    DescribeFeatureType's own authoritative answer is used directly,
    not inferred from whatever happens to have been queried before."""
    schema = b"""<?xml version="1.0"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:gml="http://www.opengis.net/gml/3.2">
<xsd:complexType name="t"><xsd:sequence>
<xsd:element name="cleabs" type="xsd:string"/>
<xsd:element name="geometrie" type="gml:MultiCurvePropertyType"/>
</xsd:sequence></xsd:complexType>
</xsd:schema>"""

    def fetch(url):
        if "DescribeFeatureType" in url:
            return schema
        return _capabilities_xml()

    widget = _make_widget(qgis_app, fetch=fetch)
    widget.list_widget.setCurrentRow(0)

    # query_fieldnames deliberately left empty/unrelated - the old
    # guessing-only code path would have fallen back to the plain
    # "geom" default here; the fix doesn't need it at all.
    assert widget._current_geometry_field() == "geometrie"


def test_current_geometry_field_caches_per_typename_not_refetched(qgis_app):
    schema = b"""<?xml version="1.0"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:gml="http://www.opengis.net/gml/3.2">
<xsd:complexType name="t"><xsd:sequence>
<xsd:element name="the_geom" type="gml:PointPropertyType"/>
</xsd:sequence></xsd:complexType>
</xsd:schema>"""
    calls = []

    def fetch(url):
        if "DescribeFeatureType" in url:
            calls.append(url)
            return schema
        return _capabilities_xml()

    widget = _make_widget(qgis_app, fetch=fetch)
    widget.list_widget.setCurrentRow(0)

    first = widget._current_geometry_field()
    second = widget._current_geometry_field()
    assert first == second == "the_geom"
    assert len(calls) == 1  # only fetched once, reused from cache the second time


def test_current_geometry_field_falls_back_to_guessing_when_describefeaturetype_fails(
    qgis_app,
):
    def failing_fetch(url):
        if "DescribeFeatureType" in url:
            raise OSError("connection refused")
        return _capabilities_xml()

    widget = _make_widget(qgis_app, fetch=failing_fetch)
    widget.list_widget.setCurrentRow(0)
    widget.query_fieldnames = ["name", "wkt_geom", "url"]

    assert widget._current_geometry_field() == "wkt_geom"


def test_guess_geometry_field_finds_wkt_geom():
    from sigate.ui.wfs_widget import guess_geometry_field

    assert guess_geometry_field(["name", "wkt_geom", "url"]) == "wkt_geom"


def test_guess_geometry_field_finds_ign_bdtopo_v3_geometrie():
    """Regression test for the actual reported failure ("Query failed:
    HTTP 400 Bad Request: Illegal property name: geom for feature type
    BDTOPO_V3:itineraire_autre") - confirmed real via a real working
    IGN query using this exact field name for the BDTOPO_V3 family."""
    from sigate.ui.wfs_widget import guess_geometry_field

    assert guess_geometry_field(["nature", "geometrie", "id"]) == "geometrie"


def test_guess_geometry_field_falls_back_when_nothing_matches():
    from sigate.ui.wfs_widget import guess_geometry_field

    assert guess_geometry_field(["name", "id"]) == "geom"


def test_guess_id_field_finds_known_candidates():
    from sigate.ui.wfs_widget import guess_id_field

    assert guess_id_field(["name", "FID", "url"]) == "FID"
    assert guess_id_field(["gml_id", "name"]) == "gml_id"


def test_guess_id_field_prefers_cleabs_over_a_synthetic_csv_fid_column():
    """Regression test for the actual reported bug: "Illegal property
    name: BDTOPO_V3:FID for feature type BDTOPO_V3:itineraire_autre".
    GeoServer's CSV output automatically prepends a synthetic "FID"
    pseudo-column (the raw GML feature id) regardless of the feature
    type's real schema - it isn't necessarily a real, CQL_FILTER-
    filterable attribute at all, which this exact feature type's real
    schema (confirmed via a live DescribeFeatureType fetch) demonstrates
    directly: it has no "FID" attribute, only "cleabs" - IGN's own real
    cross-product identifier convention. "cleabs" must be preferred
    when both are present in the CSV column list."""
    from sigate.ui.wfs_widget import guess_id_field

    assert guess_id_field(["cleabs", "FID", "nature", "geometrie"]) == "cleabs"


def test_guess_id_field_returns_none_when_nothing_matches():
    from sigate.ui.wfs_widget import guess_id_field

    assert guess_id_field(["name", "url"]) is None


def test_build_id_list_filter_quotes_and_escapes_values():
    from sigate.ui.wfs_widget import build_id_list_filter

    rows = [{"FID": "abc"}, {"FID": "o'brien"}]
    assert build_id_list_filter("FID", rows) == "FID IN ('abc', 'o''brien')"


def _install_fake_query_builder_dialog(monkeypatch, expression_text="", accept=True):
    """Stands in for ui.query_builder_dialog.QueryBuilderDialog - records
    what it was seeded with and returns a canned accept/reject +
    expression text, without a real (blocking) QDialog.exec(). Field/
    value population itself is ExpressionBuilderWidget's own job and is
    already covered directly in tests/test_expression_builder.py; these
    tests only need to confirm the WFS tab seeds and reacts to the
    dialog correctly."""
    import sigate.ui.wfs_widget as widget_module
    from qgis.PyQt.QtWidgets import QDialog

    class FakeDialog:
        instances = []

        def __init__(self, parent=None, get_canvas_extent_wkt=None):
            self.seeded_fieldnames = None
            self.seeded_rows = None
            self.seeded_geometry_field = None
            self.preloaded_text = None
            FakeDialog.instances.append(self)

        def set_fields_and_rows(self, fieldnames, rows, geometry_field_name="geom"):
            self.seeded_fieldnames = fieldnames
            self.seeded_rows = rows
            self.seeded_geometry_field = geometry_field_name

        def set_expression_text(self, text):
            self.preloaded_text = text

        def expression_text(self):
            return expression_text

        def exec(self):
            return (
                QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected
            )

    monkeypatch.setattr(widget_module, "QueryBuilderDialog", FakeDialog)
    return FakeDialog


def test_filter_button_auto_loads_fields_when_nothing_queried_yet(
    qgis_app, monkeypatch
):
    """The actual bug reported live: selecting a feature type then going
    straight to Filter, without an intervening manual "Query features"
    click, opened an empty panel with no fields to build a filter
    against - the panel must always be initialised from the currently
    selected layer, fetching a first page automatically if needed."""
    fake_dialog_cls = _install_fake_query_builder_dialog(monkeypatch)
    fetch_calls = []

    def fake_fetch(url):
        fetch_calls.append(url)
        return ("wkt_geom\tname\nPolygon ((1 2, 3 4))\tLHD_D001\n").encode("utf-8")

    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    widget.fetch = fake_fetch
    assert widget.query_fieldnames == []  # nothing queried yet - the bug's precondition

    widget._on_filter_button_clicked()

    assert any("GetFeature" in url or "SERVICE=WFS" in url for url in fetch_calls), (
        "Filter must trigger a real fetch when nothing has been queried yet"
    )
    dialog = fake_dialog_cls.instances[-1]
    assert dialog.seeded_fieldnames == ["wkt_geom", "name"]
    assert dialog.seeded_geometry_field == "wkt_geom"


def test_filter_button_does_not_requery_when_already_queried(qgis_app, monkeypatch):
    """The common, already-working case must stay cheap: if the current
    feature type has already been queried, Filter must seed straight
    from the cached result rather than fetching again just to open the
    panel.

    accept=False here is deliberate: accepting the dialog always
    re-runs the query by design (see _on_filter_button_clicked's own
    docstring - "Accepting the dialog immediately re-runs the current
    query... so the grid updates right away"), which would guarantee a
    fetch regardless of the caching this test actually checks. Only a
    cancelled dialog isolates whether *opening* the panel needlessly
    re-fetches.

    A one-time DescribeFeatureType request (cached per feature type, used
    to find the real geometry field for the $geom token) is expected the
    first time the panel opens and isn't a re-query of the features, so
    it's excluded from the check."""
    fake_dialog_cls = _install_fake_query_builder_dialog(monkeypatch, accept=False)
    fetch_calls = []

    def fake_fetch(url):
        fetch_calls.append(url)
        return ("wkt_geom\tname\nPolygon ((1 2, 3 4))\tLHD_D001\n").encode("utf-8")

    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    widget.fetch = fake_fetch
    widget._on_query_clicked()
    fetch_calls.clear()

    widget._on_filter_button_clicked()

    feature_fetches = [url for url in fetch_calls if "DescribeFeatureType" not in url]
    assert feature_fetches == []  # no re-query - already had a queried result
    dialog = fake_dialog_cls.instances[-1]
    assert dialog.seeded_fieldnames == ["wkt_geom", "name"]


def test_filter_button_shows_message_when_no_feature_type_selected(
    qgis_app, monkeypatch
):
    from qgis.PyQt.QtWidgets import QMessageBox

    shown = []
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(True))
    )
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentItem(None)

    widget._on_filter_button_clicked()

    assert shown == [True]


def test_filter_dialog_is_seeded_with_real_fields_from_last_query(
    qgis_app, monkeypatch
):
    """The gap this closes: the expression builder existed as a tested,
    standalone widget, but nothing fed it real field names or sample
    values from an actual query result. Now checked at the seeding call
    made right before the (faked) dialog is shown."""
    fake_dialog_cls = _install_fake_query_builder_dialog(monkeypatch)
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)

    csv_response = (
        "wkt_geom\tname\tid_chantier\n"
        "Polygon ((1 2, 3 4))\tLHD_D001\t137\n"
        "Polygon ((1 2, 3 4))\tLHD_D002\t140\n"
    ).encode("utf-8")
    widget.fetch = lambda url: csv_response
    widget._on_query_clicked()

    widget._on_filter_button_clicked()

    dialog = fake_dialog_cls.instances[-1]
    assert set(dialog.seeded_fieldnames) == {"wkt_geom", "name", "id_chantier"}
    assert dialog.seeded_geometry_field == "wkt_geom"
    assert len(dialog.seeded_rows) == 2


def test_filter_button_preloads_dialog_with_the_currently_active_filter(
    qgis_app, monkeypatch
):
    fake_dialog_cls = _install_fake_query_builder_dialog(monkeypatch)
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    widget._active_filter_text = "name = 'LHD_D001'"

    widget._on_filter_button_clicked()

    assert fake_dialog_cls.instances[-1].preloaded_text == "name = 'LHD_D001'"


def test_accepting_the_filter_dialog_applies_the_filter_and_requeries(
    qgis_app, monkeypatch
):
    """The actual requested flow: open a layer, hit Filter, build a
    filter, accept - the grid re-queries with that filter applied
    immediately, without a separate manual "Query features" click."""
    _install_fake_query_builder_dialog(
        monkeypatch, expression_text="name = 'LHD_D001'", accept=True
    )
    captured_urls = []

    def fake_fetch(url):
        captured_urls.append(url)
        return ("wkt_geom\tname\nPolygon ((1 2, 3 4))\tLHD_D001\n").encode("utf-8")

    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    widget.fetch = fake_fetch

    widget._on_filter_button_clicked()

    assert widget._active_filter_text == "name = 'LHD_D001'"
    assert "Filter: name = 'LHD_D001'" in widget.filter_status_label.text()
    assert any("CQL_FILTER" in url for url in captured_urls)


def test_cancelling_the_filter_dialog_leaves_the_active_filter_unchanged(
    qgis_app, monkeypatch
):
    _install_fake_query_builder_dialog(
        monkeypatch, expression_text="name = 'should not apply'", accept=False
    )
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    widget._active_filter_text = "name = 'original'"

    widget._on_filter_button_clicked()

    assert widget._active_filter_text == "name = 'original'"


def test_clear_filter_button_removes_the_active_filter_and_requeries(qgis_app):
    captured_urls = []

    def fake_fetch(url):
        captured_urls.append(url)
        return ("wkt_geom\tname\nPolygon ((1 2, 3 4))\tLHD_D001\n").encode("utf-8")

    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    widget.fetch = fake_fetch
    widget._on_query_clicked()

    widget._active_filter_text = "name = 'LHD_D001'"
    widget._update_filter_status_label()
    captured_urls.clear()

    widget._on_clear_filter_clicked()

    assert widget._active_filter_text == ""
    assert widget.filter_status_label.text() == "Filter: (none)"
    assert all("CQL_FILTER" not in url for url in captured_urls)


def test_selecting_a_different_feature_type_clears_the_active_filter(qgis_app):
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    widget._active_filter_text = "name = 'LHD_D001'"
    widget._update_filter_status_label()

    widget.list_widget.setCurrentRow(0)  # switch to the other feature type

    assert widget._active_filter_text == ""
    assert widget.filter_status_label.text() == "Filter: (none)"


def test_query_features_uses_the_active_filter_text_as_cql_filter(qgis_app):
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)

    captured_urls = []

    def fake_fetch(url):
        captured_urls.append(url)
        return ("wkt_geom\tname\nPolygon ((1 2, 3 4))\tLHD_D001\n").encode("utf-8")

    widget.fetch = fake_fetch
    widget._active_filter_text = "name = 'LHD_D001'"
    widget._on_query_clicked()

    assert any("CQL_FILTER" in url for url in captured_urls)


class _FakeMapSettingsForExtent:
    def __init__(self, crs):
        self._crs = crs

    def destinationCrs(self):
        return self._crs


class _FakeMapCanvasForExtent:
    def __init__(self, extent, crs):
        self._extent = extent
        self._crs = crs

    def extent(self):
        return self._extent

    def mapSettings(self):
        return _FakeMapSettingsForExtent(self._crs)


class _FakeIfaceForExtent:
    def __init__(self, canvas):
        self._canvas = canvas

    def mapCanvas(self):
        return self._canvas


def test_map_extent_token_actually_resolves_to_a_real_wkt_polygon(
    qgis_app, monkeypatch
):
    """Regression test for the actual root cause of a real reported
    failure ("ST_Within($geom,@map_extent) error 400 bad request"):
    @map_extent existed as an insertable token, but get_canvas_extent_wkt
    was always hardcoded to None when constructing the Filter dialog and
    when resolving the active filter for a real query - meaning
    @map_extent could never have resolved to anything, regardless of
    which spatial function name was used. Confirmed here end to end,
    with a fake canvas standing in for the real map (no real QGIS canvas
    exists in this headless test), that a filter using @map_extent now
    reaches the actual query URL with a real polygon, not the literal
    token text."""
    import sigate.ui.wfs_widget as widget_module
    from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle

    extent = QgsRectangle(1.0, 2.0, 3.0, 4.0)
    crs = QgsCoordinateReferenceSystem("EPSG:4326")
    monkeypatch.setattr(
        widget_module,
        "iface",
        _FakeIfaceForExtent(_FakeMapCanvasForExtent(extent, crs)),
    )

    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)

    captured_urls = []

    def fake_fetch(url):
        captured_urls.append(url)
        return ("wkt_geom\tname\nPolygon ((1 2, 3 4))\tLHD_D001\n").encode("utf-8")

    widget.fetch = fake_fetch
    widget._on_query_clicked()  # populates query_fieldnames, so the
    # geometry field guess below resolves to the real "wkt_geom" rather
    # than the pre-any-query fallback of "geom" - matching real usage,
    # where the Filter panel's own auto-query already does this first.
    captured_urls.clear()

    widget._active_filter_text = "WITHIN($geom, @map_extent)"
    widget._on_query_clicked()

    matching = [u for u in captured_urls if "CQL_FILTER" in u]
    assert matching
    decoded = unquote_plus(matching[0])
    assert "@map_extent" not in decoded  # the literal token must be gone
    assert "within(wkt_geom, polygon" in decoded.lower()
    assert "'polygon" not in decoded.lower()  # must not be quoted as a string


def _capabilities_xml_with_default_crs(crs_text: str) -> bytes:
    return f"""<?xml version="1.0"?>
<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0">
<wfs:FeatureTypeList>
<wfs:FeatureType>
<wfs:Name>test:layer</wfs:Name>
<wfs:Title>Test layer</wfs:Title>
<wfs:DefaultCRS>{crs_text}</wfs:DefaultCRS>
</wfs:FeatureType>
</wfs:FeatureTypeList>
</wfs:WFS_Capabilities>""".encode("utf-8")


def test_get_canvas_extent_wkt_swaps_axes_for_a_geographic_crs(qgis_app, monkeypatch):
    """Regression test for the actual reported failure: a spatial
    filter against a feature type declared in EPSG:4326 (confirmed
    directly by the user) returned zero results with both WITHIN and
    INTERSECTS, ruling out a predicate-semantics mistake. EPSG:4326 is
    authority-defined with lat,lon axis order, but QgsGeometry.asWkt()
    always emits x,y - _get_canvas_extent_wkt must swap coordinates for
    a feature type whose CRS has this inverted order, or the resulting
    WKT is silently in the wrong axis order for a server that expects
    the authority-defined one."""
    import sigate.ui.wfs_widget as widget_module
    from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle

    # Same source and target CRS here deliberately - isolates the axis-
    # swap behavior itself from any actual reprojection, matching how
    # the user's own report described it (the project canvas and the
    # feature type were both effectively 4326-equivalent for this
    # check).
    extent = QgsRectangle(6.6, 45.9, 6.8, 46.0)  # (lon_min, lat_min, lon_max, lat_max)
    crs = QgsCoordinateReferenceSystem("EPSG:4326")
    monkeypatch.setattr(
        widget_module,
        "iface",
        _FakeIfaceForExtent(_FakeMapCanvasForExtent(extent, crs)),
    )
    widget = _make_widget(
        qgis_app, fetch=lambda url: _capabilities_xml_with_default_crs("EPSG:4326")
    )
    widget.list_widget.setCurrentRow(0)

    wkt = widget._get_canvas_extent_wkt()

    assert QgsCoordinateReferenceSystem("EPSG:4326").hasAxisInverted(), (
        "sanity check on the test's own assumption about EPSG:4326's axis order"
    )
    # Swapped: the first coordinate of each point must be latitude
    # (~45.9), not longitude (~6.6). Parsed numerically rather than a
    # naive substring check - real QGIS/GEOS WKT serialization emits
    # full floating-point precision (e.g. "45.89999999999999858", not
    # a clean "45.9"), so "45.9" in wkt is not a reliable check even
    # though the underlying value is correct.
    first_pair = wkt.split("((")[1].split(",")[0].split()
    first_coord = float(first_pair[0])
    second_coord = float(first_pair[1])
    assert abs(first_coord - 45.9) < 0.01
    assert abs(second_coord - 6.6) < 0.01


def test_get_canvas_extent_wkt_does_not_swap_axes_for_a_projected_crs(
    qgis_app, monkeypatch
):
    """The two real sources this project already had confirmed working
    before this fix (IGN's Lambert-93, geodienste.ch's LV95) are both
    projected CRSes with no axis-order ambiguity - this must keep
    working exactly as before, unaffected by the new swap logic."""
    import sigate.ui.wfs_widget as widget_module
    from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle

    extent = QgsRectangle(900000.0, 6500000.0, 901000.0, 6501000.0)
    crs = QgsCoordinateReferenceSystem("EPSG:2154")
    monkeypatch.setattr(
        widget_module,
        "iface",
        _FakeIfaceForExtent(_FakeMapCanvasForExtent(extent, crs)),
    )
    widget = _make_widget(
        qgis_app, fetch=lambda url: _capabilities_xml_with_default_crs("EPSG:2154")
    )
    widget.list_widget.setCurrentRow(0)

    wkt = widget._get_canvas_extent_wkt()

    assert not QgsCoordinateReferenceSystem("EPSG:2154").hasAxisInverted()
    assert "900000" in wkt
    assert wkt.index("900000") < wkt.index("6500000")


def test_current_filter_expression_does_not_touch_canvas_when_extent_not_used(
    qgis_app, monkeypatch
):
    """A filter that never references @map_extent must not call
    iface.mapCanvas() at all - avoids any cost/risk of touching the real
    canvas for the (much more common) plain attribute-filter case."""
    import sigate.ui.wfs_widget as widget_module

    def fail_if_called():
        raise AssertionError(
            "must not access the map canvas unless @map_extent is used"
        )

    monkeypatch.setattr(widget_module, "iface", _FakeIfaceForExtent(canvas=None))
    widget_module.iface.mapCanvas = fail_if_called

    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    widget._active_filter_text = "name = 'LHD_D001'"

    result = widget._current_filter_expression()

    assert result == "name = 'LHD_D001'"


def test_query_success_status_confirms_the_filter_was_actually_applied(qgis_app):
    """A real report ("setting the filter and hitting query features do
    not change list content") had no way to tell, just from looking at
    the tab, whether a filter had genuinely reached the server for a
    given query versus silently being dropped somewhere - the status
    text now says explicitly, every time."""
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    widget.fetch = lambda url: (
        "wkt_geom\tname\nPolygon ((1 2, 3 4))\tLHD_D001\n"
    ).encode("utf-8")

    widget._active_filter_text = "name = 'LHD_D001'"
    widget._on_query_clicked()
    assert "filter applied: name = 'LHD_D001'" in widget.status_label.text()

    widget._active_filter_text = ""
    widget._on_query_clicked()
    assert "no filter" in widget.status_label.text()


def test_query_failure_shows_a_message_box_not_just_a_status_label(
    qgis_app, monkeypatch
):
    """A real, plausible cause of "the filter doesn't seem to do
    anything": the filtered query fails server-side (e.g. the server
    rejects the CQL syntax) and the grid is deliberately left showing
    whatever it had before - previously the only visible sign was a
    small status label, easy to miss entirely. A failure must now be
    impossible to miss."""
    from qgis.PyQt.QtWidgets import QMessageBox

    shown = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *a, **k: shown.append(a[2] if len(a) > 2 else "")),
    )

    def failing_fetch(url):
        raise ConnectionError("simulated server rejection")

    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    widget.fetch = failing_fetch
    widget._active_filter_text = "name = 'LHD_D001'"

    widget._on_query_clicked()

    assert len(shown) == 1
    assert "simulated server rejection" in shown[0]
    assert "name = 'LHD_D001'" in shown[0]  # names the filter that was active


def test_add_to_map_with_no_filter_emits_correct_uri(qgis_app):
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)  # the "dalle" entry

    captured = []
    widget.addVectorLayer.connect(
        lambda uri, name, provider: captured.append((uri, name, provider))
    )
    widget._on_add_clicked()

    assert len(captured) == 1
    uri, name, provider = captured[0]
    assert provider == "WFS"
    assert name == "IGNF_MNS-LIDAR-HD:dalle"
    decoded = QgsDataSourceUri(uri)
    assert decoded.param("typename") == "IGNF_MNS-LIDAR-HD:dalle"
    assert decoded.param("filter") == ""  # no attribute filter was given
    assert decoded.param("restrictToRequestBBOX") == "1"  # unaffected by the fix


def test_add_to_map_with_attribute_filter_includes_it_in_the_uri(qgis_app):
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    widget._active_filter_text = "id_chantier = '137'"

    captured = []
    widget.addVectorLayer.connect(
        lambda uri, name, provider: captured.append((uri, name, provider))
    )
    widget._on_add_clicked()

    uri, _, _ = captured[0]
    decoded = QgsDataSourceUri(uri)
    assert decoded.param("filter") == "id_chantier = '137'"


def test_add_to_map_with_selected_rows_restricts_to_those_rows_by_id(qgis_app):
    """The actual reported mismatch: Add to map was adding the whole
    filtered result regardless of which rows were selected in the grid -
    it must now restrict to exactly the selected rows when any are
    selected."""
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    widget.fetch = lambda url: (
        "FID\tname\nfid.1\tLHD_D001\nfid.2\tLHD_D002\nfid.3\tLHD_D003\n"
    ).encode("utf-8")
    widget._on_query_clicked()

    # select rows 0 and 2 (LHD_D001 and LHD_D003), leaving LHD_D002 out
    widget.results_tree.topLevelItem(0).setSelected(True)
    widget.results_tree.topLevelItem(2).setSelected(True)

    captured = []
    widget.addVectorLayer.connect(
        lambda uri, name, provider: captured.append((uri, name, provider))
    )
    widget._on_add_clicked()

    uri, name, _ = captured[0]
    decoded = QgsDataSourceUri(uri)
    assert decoded.param("filter") == "FID IN ('fid.1', 'fid.3')"
    assert "2 selected" in name
    # Regression assertion for a real reported failure: an id-list
    # selection is already a complete, canvas-independent selector -
    # restricting to the current viewport on top of it is both
    # semantically wrong (could silently drop a selected feature that's
    # since scrolled out of view) and adds unnecessary request length.
    assert decoded.param("restrictToRequestBBOX") == "0"


def test_add_to_map_falls_back_to_filter_when_nothing_selected(qgis_app):
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    widget.fetch = lambda url: ("FID\tname\nfid.1\tLHD_D001\nfid.2\tLHD_D002\n").encode(
        "utf-8"
    )
    widget._on_query_clicked()
    widget._active_filter_text = "name = 'LHD_D001'"
    # nothing selected in results_tree

    captured = []
    widget.addVectorLayer.connect(
        lambda uri, name, provider: captured.append((uri, name, provider))
    )
    widget._on_add_clicked()

    uri, _, _ = captured[0]
    decoded = QgsDataSourceUri(uri)
    assert decoded.param("filter") == "name = 'LHD_D001'"


def test_add_to_map_falls_back_and_warns_when_selection_has_no_id_field(qgis_app):
    """If the currently displayed columns don't include anything
    guess_id_field recognises, restricting to just the selected rows
    isn't possible - falls back to the whole-filter behaviour, but must
    say so rather than silently adding more than what's selected."""
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    widget.fetch = lambda url: (
        "wkt_geom\tname\nPolygon ((1 2, 3 4))\tLHD_D001\n"
    ).encode("utf-8")
    widget._on_query_clicked()
    widget.results_tree.topLevelItem(0).setSelected(True)

    captured = []
    widget.addVectorLayer.connect(
        lambda uri, name, provider: captured.append((uri, name, provider))
    )
    widget._on_add_clicked()

    uri, _, _ = captured[0]
    decoded = QgsDataSourceUri(uri)
    assert decoded.param("filter") == ""  # whole-filter fallback, no filter active
    assert "no identifying field" in widget.status_label.text()


def test_query_clears_the_grid_before_fetching_new_results(qgis_app):
    """Requested directly: query should empty the list before
    repopulating it - checked here by having the fetch itself observe
    that the grid is already empty by the time the network request is
    attempted, not just that it ends up correct once data arrives."""
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    widget.fetch = lambda url: (
        "wkt_geom\tname\nPolygon ((1 2, 3 4))\tLHD_D001\n"
    ).encode("utf-8")
    widget._on_query_clicked()
    assert widget.results_tree.topLevelItemCount() == 1

    observed_counts_during_fetch = []

    def fetch_observing_grid_state(url):
        observed_counts_during_fetch.append(widget.results_tree.topLevelItemCount())
        return ("wkt_geom\tname\nPolygon ((1 2, 3 4))\tLHD_D002\n").encode("utf-8")

    widget.fetch = fetch_observing_grid_state
    widget._on_query_clicked()

    assert observed_counts_during_fetch == [0]  # grid was already empty at fetch time
    assert widget.results_tree.topLevelItemCount() == 1  # repopulated after


def test_md5_field_for_recognizes_common_checksum_field_names():
    from sigate.ui.wfs_widget import md5_field_for

    assert md5_field_for({"url": "x", "md5": "abc"}) == "md5"
    assert md5_field_for({"url": "x", "checksum": "abc"}) == "checksum"
    assert md5_field_for({"url": "x", "name": "y"}) is None


def test_build_download_items_from_rows_populates_expected_md5_when_present():
    """Requested directly ("verify integrity when any given checksum is
    provided"): a WFS file-index row carrying an md5-like field must
    have that value wired through to DownloadItem.expected_md5, so the
    existing integrity check in download.pipeline.download_items
    actually verifies it rather than silently never running."""
    from sigate.ui.wfs_widget import build_download_items_from_rows

    rows = [
        {"url": "https://x/tile.tif", "name": "tile.tif", "md5": "abc123"},
        {"url": "https://x/other.tif", "name": "other.tif"},  # no checksum field
    ]
    items = build_download_items_from_rows(rows, "IGN (France)", layer_name="layer")

    assert items[0].expected_md5 == "abc123"
    assert items[1].expected_md5 is None


# --- IGN LiDAR HD metadata layer: several download links per feature --------

_LHD_ROW = {
    "capteur": "RIEGL VQ-1560 II",
    "url_mnh": "https://data.geopf.fr/wms-r?SERVICE=WMS&REQUEST=GetMap&FILENAME=LHD_FXX_0998_6542_MNH_O_0M50_LAMB93_IGN69.tif",
    "url_mns": "https://data.geopf.fr/wms-r?SERVICE=WMS&REQUEST=GetMap&FILENAME=LHD_FXX_0998_6542_MNS_O_0M50_LAMB93_IGN69.tif",
    "url_mnt": "https://data.geopf.fr/wms-r?SERVICE=WMS&REQUEST=GetMap&FILENAME=LHD_FXX_0998_6542_MNT_O_0M50_LAMB93_IGN69.tif",
    "url_npl": "https://data.geopf.fr/telechargement/download/LiDARHD-NUALID/NUALHD_1-0__LAZ_LAMB93_QK_2025-06-13/LHD_FXX_0998_6542_PTS_LAMB93_IGN69.copc.laz",
    "code_mission": "21LHD5QK2",
    "metadata": "{}",
}


def test_url_fields_for_finds_every_url_product_column():
    from sigate.ui.wfs_widget import url_field_for, url_fields_for

    assert url_fields_for(_LHD_ROW) == ["url_mnh", "url_mns", "url_mnt", "url_npl"]
    assert url_field_for(_LHD_ROW) == "url_mnh"
    # an empty or non-URI value is not a download link, whatever the name
    assert url_fields_for({"url_x": "", "url_y": "not a link", "id": "1"}) == []


def test_url_fields_are_detected_by_value_not_by_column_name():
    from sigate.ui.wfs_widget import is_download_uri, url_fields_for

    row = {
        "lien_telechargement": "https://x.example/data/a.zip",
        "doc": "http://x.example/readme.html",
        "metadata": '{"capteur": ["x"], "url": "https://x.example/a"}',
        "contact": "mailto:someone@example.org",
        "chemin": "/data/a.tif",
        "ftp_link": "ftp://x.example/a.zip",
        "note": "see https://x.example/a for details",
        "id": "137",
    }
    assert url_fields_for(row) == ["lien_telechargement", "doc"]
    assert is_download_uri("HTTPS://x.example/a")
    assert not is_download_uri("https://")
    assert not is_download_uri(None)
    assert not is_download_uri("")


def test_product_label_and_filename_from_url():
    from sigate.ui.wfs_widget import filename_from_url, product_label_for

    assert product_label_for("url_mnt") == "MNT"
    assert product_label_for("url") == "url"
    assert (
        filename_from_url(_LHD_ROW["url_mnt"])
        == "LHD_FXX_0998_6542_MNT_O_0M50_LAMB93_IGN69.tif"
    )
    assert (
        filename_from_url(_LHD_ROW["url_npl"])
        == "LHD_FXX_0998_6542_PTS_LAMB93_IGN69.copc.laz"
    )
    assert filename_from_url("https://x/y/z.zip?token=1") == "z.zip"


def test_build_download_items_yields_one_item_per_chosen_product():
    from sigate.ui.wfs_widget import build_download_items_from_rows

    items = build_download_items_from_rows(
        [_LHD_ROW],
        "IGN (France)",
        layer_name="IGNF_LIDAR-HD_METADONNEE:metadata",
        fields=["url_mnt", "url_npl"],
    )
    assert [i.filename for i in items] == [
        "LHD_FXX_0998_6542_MNT_O_0M50_LAMB93_IGN69.tif",
        "LHD_FXX_0998_6542_PTS_LAMB93_IGN69.copc.laz",
    ]
    # category follows the file type, so rasters and the point cloud
    # land in different folders
    assert [i.product for i in items] == ["MNT", "NPL"]
    assert "/raster/" in items[0].subdirectory
    assert "/point_cloud/" in items[1].subdirectory


def test_download_selected_is_enabled_for_url_product_columns(qgis_app):
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    widget.list_widget.setCurrentRow(1)
    header = "\t".join(["wkt_geom"] + list(_LHD_ROW))
    values = "\t".join(["Polygon ((1 2, 3 4))"] + list(_LHD_ROW.values()))
    widget.fetch = lambda url: f"{header}\n{values}\n".encode("utf-8")

    widget._on_query_clicked()

    assert widget.download_button.isEnabled()


def test_choose_download_fields_asks_only_when_several_products(qgis_app):
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    asked = []

    def chooser(products, preselected, count):
        asked.append((dict(products), set(preselected), count))
        return ["url_mnt"]

    widget.product_chooser = chooser

    assert widget._choose_download_fields([_LHD_ROW]) == ["url_mnt"]
    # rasters pre-ticked, the (far larger) point cloud not
    assert asked[0][1] == {"url_mnh", "url_mns", "url_mnt"}
    assert asked[0][0]["url_npl"] == "NPL"
    # a one-link layer is never asked about
    asked.clear()
    assert widget._choose_download_fields([{"url": "https://x/a.tif"}]) == ["url"]
    assert asked == []
    # cancelling / ticking nothing aborts the download
    widget.product_chooser = lambda *a: None
    assert widget._choose_download_fields([_LHD_ROW]) is None


def test_copc_laz_is_added_as_a_point_cloud_layer(qgis_app, tmp_path):
    widget = _make_widget(qgis_app, fetch=lambda url: _capabilities_xml())
    emitted = []
    widget.addPointCloudLayer.connect(lambda *a: emitted.append(a))
    path = tmp_path / "LHD_FXX_0998_6542_PTS_LAMB93_IGN69.copc.laz"
    path.write_bytes(b"x")

    widget._add_layer(path)

    assert emitted == [(str(path), "LHD_FXX_0998_6542_PTS_LAMB93_IGN69", "copc")]


def _fake_extent_widget(qgis_app, monkeypatch, extent, crs_text):
    import sigate.ui.wfs_widget as widget_module
    from qgis.core import QgsCoordinateReferenceSystem

    monkeypatch.setattr(
        widget_module,
        "iface",
        _FakeIfaceForExtent(
            _FakeMapCanvasForExtent(extent, QgsCoordinateReferenceSystem(crs_text))
        ),
    )
    widget = _make_widget(
        qgis_app, fetch=lambda url: _capabilities_xml_with_default_crs(crs_text)
    )
    widget.list_widget.setCurrentRow(0)
    widget._active_filter_text = "WITHIN($geom, @map_extent)"
    return widget


def _first_pair(wkt):
    pair = wkt.split("((")[1].split(",")[0].split()
    return float(pair[0]), float(pair[1])


def test_add_to_map_filter_is_a_qgis_expression_in_qgis_axis_order(
    qgis_app, monkeypatch
):
    """Live-confirmed against a 4326 layer: the server's CQL wants the
    extent as lat,lon, but the QGIS expression handed to the native
    provider must be lon,lat (QGIS's own x,y) - the two paths can't share
    one resolved text, and the add path must also state the CRS the
    literal is in (srsname)."""
    from qgis.core import QgsDataSourceUri, QgsRectangle

    widget = _fake_extent_widget(
        qgis_app, monkeypatch, QgsRectangle(6.8, 45.9, 6.95, 46.0), "EPSG:4326"
    )
    emitted = []
    widget.addVectorLayer.connect(lambda *a: emitted.append(a))

    widget._on_add_clicked()

    (uri_text, _name, _provider) = emitted[0]
    uri = QgsDataSourceUri(uri_text)
    expression = uri.param("filter")
    assert expression.startswith("WITHIN($geometry, geom_from_wkt('")
    assert "@map_extent" not in expression and "$geom," not in expression
    lon, lat = _first_pair(expression.split("geom_from_wkt('")[1])
    assert abs(lon - 6.8) < 0.01 and abs(lat - 45.9) < 0.01  # lon,lat
    assert uri.param("srsname") == "EPSG:4326"

    # ...while the query path still sends lat,lon for the same extent
    cql = widget._current_filter_expression()
    first, second = _first_pair(cql)
    assert abs(first - 45.9) < 0.01 and abs(second - 6.8) < 0.01


def test_add_to_map_filter_for_a_projected_crs_keeps_x_y_order(qgis_app, monkeypatch):
    from qgis.core import QgsDataSourceUri, QgsRectangle

    widget = _fake_extent_widget(
        qgis_app,
        monkeypatch,
        QgsRectangle(900000.0, 6500000.0, 901000.0, 6501000.0),
        "EPSG:2154",
    )
    emitted = []
    widget.addVectorLayer.connect(lambda *a: emitted.append(a))

    widget._on_add_clicked()

    uri = QgsDataSourceUri(emitted[0][0])
    expression = uri.param("filter")
    x, y = _first_pair(expression.split("geom_from_wkt('")[1])
    assert x == 900000.0 and y == 6500000.0
    assert uri.param("srsname") == "EPSG:2154"


def test_selecting_a_second_wfs_connection_of_the_same_source_uses_its_own_url(
    qgis_app,
):
    """geodienste.ch offers several WFS services in one source; the tab
    must read the selected entry's gateway, not the source's first."""
    seen = []

    def fetch(url):
        seen.append(url)
        return _capabilities_xml()

    widget = _make_widget(qgis_app, fetch=fetch)
    combo = widget.connection_manager.combo
    index = next(i for i in range(combo.count()) if "umfassend" in combo.itemText(i))
    combo.setCurrentIndex(index)
    assert "naturereigniskataster_umfassend_v1_0_0" in seen[-1]
    assert (
        widget.connection_manager.current_gateway().base_url
        == "https://geodienste.ch/db/naturereigniskataster_umfassend_v1_0_0/deu"
    )
