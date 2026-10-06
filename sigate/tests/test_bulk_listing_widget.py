"""
End-to-end tests for the Bulk Listing tab, run against a real headless
QGIS application (see conftest.py's qgis_app fixture). Network access and
QMessageBox prompts are both replaced with test doubles - injected
fetch/download functions for the former, monkeypatched static methods for
the latter, since a real QMessageBox call would block waiting for a
click that never comes in a headless test.
"""

import io
import zipfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def clean_target_picker_persistence(qgis_app):
    """The Bulk Listing tab's TargetPicker now remembers the last-used
    central-repo/destination paths across sessions (see
    ui.target_picker). Without resetting this between tests, one test's
    typed-in path leaks into the next test's fresh widget instance via
    that same persisted setting - exactly the cross-test contamination
    this fixture exists to prevent. Applied automatically to every test
    in this file, not opt-in, since any test constructing the widget is
    affected."""
    from sigate.ui import settings as sigate_settings

    sigate_settings.set_last_central_repo(None)
    sigate_settings.set_last_destination(None)
    yield
    sigate_settings.set_last_central_repo(None)
    sigate_settings.set_last_destination(None)


@pytest.fixture
def no_block_message_boxes(monkeypatch):
    """Replaces QMessageBox's static prompt methods so none of them block
    waiting for a real click - every question answers Yes, every
    information/warning/critical is a no-op."""
    from qgis.PyQt.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))


def _sync_progress_runner(parent, items, central_repo, download_fn):
    """A synchronous stand-in for the real progress_runner (a modal
    QDialog backed by a background QgsTask) - the real one blocks on
    QDialog.exec() until a background task finishes, which has nothing
    to drive it forward deterministically in a headless test. Calls
    download.pipeline.download_items() directly instead."""
    from sigate.download import pipeline

    outcomes = pipeline.download_items(items, central_repo, download_fn=download_fn)
    return outcomes, False


def _make_widget(qgis_app, fetch, download_fn):
    from qgis.PyQt.QtCore import Qt

    from sigate.ui.bulk_listing_widget import BulkListingSourceSelectWidget

    return BulkListingSourceSelectWidget(
        None,
        Qt.WindowType(0),
        fetch=fetch,
        download_fn=download_fn,
        progress_runner=_sync_progress_runner,
    )


def _single_entry_feed(
    title: str, download_url: str, byte_length: int, mime_type: str = "application/zip"
) -> bytes:
    return f"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:gpf_dl="http://x"
     gpf_dl:page="1" gpf_dl:pagesize="50" gpf_dl:pagecount="1" gpf_dl:totalentries="1">
<entry><title>{title}</title><id>p</id>
<link href="{download_url}" type="{mime_type}" length="{byte_length}"/></entry>
</feed>""".encode()


def test_widget_construction_registers_both_providers(qgis_app):
    from qgis.gui import QgsGui

    from sigate.plugin import SigatePlugin

    registry = QgsGui.sourceSelectProviderRegistry()
    before = len(registry.providers())
    plugin = SigatePlugin(iface=None)
    plugin.initGui()
    try:
        # The plugin now registers four providers: WM(T)S, Bulk Listing,
        # WFS and ArcGIS REST. This count is expected to grow again as further tabs
        # are added.
        assert len(registry.providers()) == before + 4
        assert len(registry.providersByKey("sigate_bulk_listing")) == 1
        assert len(registry.providersByKey("sigate_wmts_wms")) == 1
    finally:
        plugin.unload()
    assert len(registry.providers()) == before


def test_widget_loads_initial_entries_via_injected_fetch_no_real_network(qgis_app):
    feed = _single_entry_feed("TEST_PRODUCT", "https://x/download/test.zip", 12345)
    widget = _make_widget(qgis_app, fetch=lambda url: feed, download_fn=None)
    assert len(widget.current_entries) == 1
    assert widget.current_entries[0].title == "TEST_PRODUCT"


def test_pagination_next_prev_and_reset_to_page_one_on_new_navigation(qgis_app):
    """The gap this test exists for: browsing used to only ever fetch
    page 1 of a listing, with no way to reach further pages if a folder
    had more entries than one page's worth."""
    page_1_xml = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:gpf_dl="http://x"
        gpf_dl:page="1" gpf_dl:pagesize="1" gpf_dl:pagecount="2" gpf_dl:totalentries="2">
    <entry><title>Entry A</title><id>a</id>
    <link href="https://x/download/a.tif" type="image/tiff"/></entry>
    </feed>"""
    page_2_xml = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:gpf_dl="http://x"
        gpf_dl:page="2" gpf_dl:pagesize="1" gpf_dl:pagecount="2" gpf_dl:totalentries="2">
    <entry><title>Entry B</title><id>b</id>
    <link href="https://x/download/b.tif" type="image/tiff"/></entry>
    </feed>"""

    def fake_fetch(url):
        return page_2_xml if "page=2" in url else page_1_xml

    widget = _make_widget(qgis_app, fetch=fake_fetch, download_fn=None)

    assert widget.current_page == 1
    assert [e.title for e in widget.current_entries] == ["Entry A"]
    assert not widget.prev_page_button.isEnabled()
    assert widget.next_page_button.isEnabled()

    widget._next_page()
    assert widget.current_page == 2
    assert [e.title for e in widget.current_entries] == ["Entry B"]
    assert widget.prev_page_button.isEnabled()
    assert not widget.next_page_button.isEnabled()

    widget._prev_page()
    assert widget.current_page == 1
    assert [e.title for e in widget.current_entries] == ["Entry A"]

    # navigating to a genuinely new URL (not paging within the same one)
    # must reset back to page 1, not silently stay on whatever page a
    # previous listing happened to be on
    widget._load("https://x/resource/OTHER", push_history=False)
    assert widget.current_page == 1


def test_filter_current_page_narrows_display_without_extra_fetch(qgis_app):
    fetch_calls = []

    def fake_fetch(url):
        fetch_calls.append(url)
        return b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:gpf_dl="http://x"
            gpf_dl:page="1" gpf_dl:pagesize="50" gpf_dl:pagecount="1" gpf_dl:totalentries="3">
        <entry><title>RGEALTI_D003</title><id>a</id>
        <link href="https://x/download/a.7z" type="application/x-7z-compressed"/></entry>
        <entry><title>RGEALTI_D074</title><id>b</id>
        <link href="https://x/download/b.7z" type="application/x-7z-compressed"/></entry>
        <entry><title>ORTHOHR_D003</title><id>c</id>
        <link href="https://x/download/c.zip" type="application/zip"/></entry>
        </feed>"""

    widget = _make_widget(qgis_app, fetch=fake_fetch, download_fn=None)
    calls_after_load = len(fetch_calls)

    widget.filter_edit.setText("D003")
    assert [e.title for e in widget.current_entries] == ["RGEALTI_D003", "ORTHOHR_D003"]
    assert len(fetch_calls) == calls_after_load, (
        "current-page filtering must not trigger any new fetch"
    )

    widget.filter_edit.setText("")
    assert len(widget.current_entries) == 3


def test_filter_all_pages_fetches_everything_and_finds_entry_beyond_current_page(
    qgis_app,
):
    """The gap this test exists for: only the first page (50 entries)
    was ever searchable through the UI, with no way to find something
    sitting on a later page."""
    page_1 = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:gpf_dl="http://x"
        gpf_dl:page="1" gpf_dl:pagesize="1" gpf_dl:pagecount="2" gpf_dl:totalentries="2">
    <entry><title>RGEALTI_D001</title><id>a</id>
    <link href="https://x/download/a.7z" type="application/x-7z-compressed"/></entry>
    </feed>"""
    page_2 = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:gpf_dl="http://x"
        gpf_dl:page="2" gpf_dl:pagesize="1" gpf_dl:pagecount="2" gpf_dl:totalentries="2">
    <entry><title>RGEALTI_D999</title><id>b</id>
    <link href="https://x/download/b.7z" type="application/x-7z-compressed"/></entry>
    </feed>"""

    def fake_fetch(url):
        return page_2 if "page=2" in url else page_1

    widget = _make_widget(qgis_app, fetch=fake_fetch, download_fn=None)
    assert [e.title for e in widget.current_entries] == [
        "RGEALTI_D001"
    ]  # only page 1, so far

    widget.filter_edit.setText("D999")
    widget._filter_all_pages()

    assert widget.filtered_mode is True
    assert [e.title for e in widget.current_entries] == ["RGEALTI_D999"]
    assert not widget.prev_page_button.isEnabled()
    assert not widget.next_page_button.isEnabled()

    widget._clear_filter()
    assert widget.filtered_mode is False
    assert widget.current_page == 1


def test_simple_file_download_places_file_and_emits_raster_layer_signal(
    qgis_app, no_block_message_boxes, tmp_path
):
    content = b"fake geotiff bytes"
    feed = _single_entry_feed(
        "SIMPLE_FILE",
        "https://x/download/simple.tif",
        len(content),
        mime_type="image/tiff",
    )

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = Path(dest_dir) / filename
        path.write_bytes(content)
        return path

    widget = _make_widget(qgis_app, fetch=lambda url: feed, download_fn=fake_download)
    widget.tree.topLevelItem(0).setSelected(True)

    central_repo = tmp_path / "repo"
    widget.target_picker.repo_edit.setText(str(central_repo))
    # no destination given - a simple file should stay in the central repo

    captured = []
    widget.addRasterLayer.connect(
        lambda uri, name, provider: captured.append((uri, name, provider))
    )

    widget._on_download_clicked()

    downloaded_path = (
        central_repo / "IGN (France)" / "raster" / "SIMPLE_FILE" / "simple.tif"
    )
    assert downloaded_path.exists()
    assert len(captured) == 1
    assert captured[0] == (str(downloaded_path), "simple", "gdal")


def test_archive_download_extracts_everything_at_correct_nested_path(
    qgis_app, no_block_message_boxes, tmp_path
):
    """Extraction preserves the archive member's full relative path, not
    just its basename - it does not flatten files to the destination
    root - and (requested directly) now keeps every file the archive
    contained, inside a subfolder named after the archive itself, not
    just the ones classified as "data"."""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("1_DONNEES_LIVRAISON_x/PRODUCT/tile_a.tif", b"raster data")
        zf.writestr("2_METADONNEES_LIVRAISON_x/readme.txt", b"metadata")
    zip_bytes = zip_buffer.getvalue()

    feed = _single_entry_feed(
        "ARCHIVE_PRODUCT", "https://x/download/archive.zip", len(zip_bytes)
    )

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = Path(dest_dir) / filename
        path.write_bytes(zip_bytes)
        return path

    widget = _make_widget(qgis_app, fetch=lambda url: feed, download_fn=fake_download)
    widget.tree.topLevelItem(0).setSelected(True)

    central_repo = tmp_path / "repo"
    destination = tmp_path / "dest"
    widget.target_picker.repo_edit.setText(str(central_repo))
    widget.target_picker.dest_edit.setText(str(destination))

    captured = []
    widget.addRasterLayer.connect(
        lambda uri, name, provider: captured.append((uri, name, provider))
    )

    widget._on_download_clicked()

    archive_folder = destination / "archive"  # "archive.zip" -> "archive"
    expected_path = archive_folder / "1_DONNEES_LIVRAISON_x" / "PRODUCT" / "tile_a.tif"
    assert expected_path.exists()
    # kept on disk, even though it's not added as a layer (no recognized
    # layer kind for a plain .txt file)
    assert any(p.name == "readme.txt" for p in archive_folder.rglob("*"))
    assert len(captured) == 1
    assert captured[0] == (str(expected_path), "tile_a", "gdal")


def test_byte_count_mismatch_is_rejected_and_no_layer_is_added(
    qgis_app, no_block_message_boxes, tmp_path
):
    content = b"actual content"
    # Declare a length that does not match the real content - the
    # integrity check must catch this rather than proceed.
    feed = _single_entry_feed(
        "BAD_LENGTH",
        "https://x/download/bad.tif",
        byte_length=999999,
        mime_type="image/tiff",
    )

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = Path(dest_dir) / filename
        path.write_bytes(content)
        return path

    widget = _make_widget(qgis_app, fetch=lambda url: feed, download_fn=fake_download)
    widget.tree.topLevelItem(0).setSelected(True)
    widget.target_picker.repo_edit.setText(str(tmp_path / "repo"))

    captured = []
    widget.addRasterLayer.connect(
        lambda uri, name, provider: captured.append((uri, name, provider))
    )

    widget._on_download_clicked()

    assert not (tmp_path / "repo" / "bad.tif").exists(), (
        "a failed-integrity file must not be left on disk"
    )
    assert captured == []


def test_download_verifies_md5_when_a_checksum_sidecar_is_present(
    qgis_app, no_block_message_boxes, tmp_path
):
    """End-to-end regression test, requested directly ("verify integrity
    when any given checksum is provided"): a checksum sidecar link
    alongside the real download link must actually be fetched, parsed,
    and used to verify the download - not just theoretically supported
    by download.pipeline while nothing ever supplies a real hash."""
    import hashlib

    file_content = b"real tile content"
    real_md5 = hashlib.md5(file_content).hexdigest()

    feed = f"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:gpf_dl="http://x"
     gpf_dl:page="1" gpf_dl:pagesize="50" gpf_dl:pagecount="1" gpf_dl:totalentries="1">
<entry><title>a tile</title><id>p</id>
<link href="https://x/download/tile.tif" type="image/tiff" length="{len(file_content)}"/>
<link href="https://x/download/tile.tif.md5" type="text/plain"/></entry>
</feed>""".encode()

    def fake_fetch(url):
        if url == "https://x/download/tile.tif.md5":
            return f"{real_md5}  tile.tif\n"
        return feed

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = Path(dest_dir) / filename
        path.write_bytes(file_content)
        return path

    widget = _make_widget(qgis_app, fetch=fake_fetch, download_fn=fake_download)
    widget.tree.topLevelItem(0).setSelected(True)
    widget.target_picker.repo_edit.setText(str(tmp_path / "repo"))

    captured = []
    widget.addRasterLayer.connect(
        lambda uri, name, provider: captured.append((uri, name, provider))
    )

    widget._on_download_clicked()

    # the file was kept (checksum matched) and added as a layer
    assert len(captured) == 1
    assert (tmp_path / "repo").rglob("tile.tif")


def test_download_rejects_a_file_whose_content_does_not_match_its_checksum_sidecar(
    qgis_app, no_block_message_boxes, tmp_path
):
    feed = """<feed xmlns="http://www.w3.org/2005/Atom" xmlns:gpf_dl="http://x"
     gpf_dl:page="1" gpf_dl:pagesize="50" gpf_dl:pagecount="1" gpf_dl:totalentries="1">
<entry><title>a tile</title><id>p</id>
<link href="https://x/download/tile.tif" type="image/tiff" length="7"/>
<link href="https://x/download/tile.tif.md5" type="text/plain"/></entry>
</feed>""".encode()

    def fake_fetch(url):
        if url == "https://x/download/tile.tif.md5":
            return "0" * 32 + "  tile.tif\n"  # deliberately wrong hash
        return feed

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = Path(dest_dir) / filename
        path.write_bytes(b"content")  # real content will never match "0"*32
        return path

    widget = _make_widget(qgis_app, fetch=fake_fetch, download_fn=fake_download)
    widget.tree.topLevelItem(0).setSelected(True)
    widget.target_picker.repo_edit.setText(str(tmp_path / "repo"))

    captured = []
    widget.addRasterLayer.connect(
        lambda uri, name, provider: captured.append((uri, name, provider))
    )

    widget._on_download_clicked()

    assert not (tmp_path / "repo" / "tile.tif").exists(), (
        "a checksum-mismatched file must not be left on disk"
    )
    assert captured == []


# --------------------------------------------------------------- stac connection


def _stac_items_page(item_id: str, self_href: str) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": item_id,
                "links": [{"rel": "self", "href": self_href}],
            }
        ],
        "links": [],
    }


def _stac_item_detail(asset_href: str, checksum_multihash: str) -> dict:
    return {
        "type": "Feature",
        "id": "tile-1",
        "assets": {
            "tile.tif": {
                "href": asset_href,
                "type": "image/tiff",
                "file:checksum": checksum_multihash,
            }
        },
    }


def test_double_click_on_an_empty_tree_does_not_crash(qgis_app):
    """Regression test for a real crash surfaced by an actual test run
    against real QGIS: double-clicking when the tree has nothing in it
    at all (item=None, whatever the reason - an empty listing, a stale
    reference) must no-op, matching the same defensive pattern
    _selected_entries already documents and implements for the
    equivalent stale-index case, rather than crash with a raw
    IndexError."""
    widget = _make_widget(qgis_app, fetch=lambda url: b"", download_fn=None)
    widget._on_item_double_clicked(None, 0)  # must not raise


def test_bulk_tab_lists_atom_stac_and_geonorge_catalog_connections_in_one_combo(
    qgis_app,
):
    """The real point of this session's generalization: France (atom),
    Switzerland (stac), and Norway (geonorge_catalog) all selectable
    from the exact same tab, not three separate UIs."""
    widget = _make_widget(qgis_app, fetch=lambda url: b"", download_fn=None)
    types_seen = {
        widget.connection_manager.combo.itemData(i)[1].gateway_type
        for i in range(widget.connection_manager.combo.count())
    }
    assert types_seen == {"atom", "stac", "geonorge_catalog"}
    source_keys_seen = {
        widget.connection_manager.combo.itemData(i)[0].key
        for i in range(widget.connection_manager.combo.count())
    }
    # geodienste.ch's Gefahrenkarten stac gateway and Norway's
    # geonorge_catalog gateway must both appear alongside France and
    # swisstopo - four distinct sources in the same combo now, not just
    # "swisstopo has more collections".
    assert source_keys_seen == {
        "ign_fr",
        "swisstopo_ch",
        "geodienste_ch",
        "geonorge_no",
    }


def test_selecting_a_stac_connection_shows_items_as_directories(qgis_app):
    items_url = "https://data.geo.admin.ch/api/stac/v1/collections/ch.swisstopo.swissalti3d/items"
    item_self_href = f"{items_url}/tile-1"

    def fetch(url):
        import json as _json

        if url == items_url:
            return _json.dumps(_stac_items_page("tile-1", item_self_href)).encode(
                "utf-8"
            )
        raise AssertionError(f"unexpected fetch: {url}")

    widget = _make_widget(qgis_app, fetch=fetch, download_fn=None)
    widget.connection_manager.reload_connections(
        select_key="swisstopo_ch", select_role="swissALTI3D"
    )

    assert widget.tree.topLevelItemCount() == 1
    assert widget.tree.topLevelItem(0).text(0) == "DIR"
    assert widget.tree.topLevelItem(0).text(1) == "tile-1"


def test_downloading_a_stac_asset_verifies_its_inline_sha256_checksum(
    qgis_app, tmp_path
):
    """The stac counterpart to test_download_verifies_md5_when_a_checksum_
    sidecar_is_present above - same end-to-end shape, but the checksum
    is inline on the asset (no separate sidecar fetch) and verified as
    sha256, not md5."""
    import hashlib
    import json as _json

    items_url = "https://data.geo.admin.ch/api/stac/v1/collections/ch.swisstopo.swissalti3d/items"
    item_self_href = f"{items_url}/tile-1"
    asset_href = "https://data.geo.admin.ch/ch.swisstopo.swissalti3d/tile.tif"

    file_content = b"real dem tile content"
    real_sha256 = hashlib.sha256(file_content).hexdigest()
    multihash = "1220" + real_sha256

    def fetch(url):
        if url == items_url:
            return _json.dumps(_stac_items_page("tile-1", item_self_href)).encode(
                "utf-8"
            )
        if url == item_self_href:
            return _json.dumps(_stac_item_detail(asset_href, multihash)).encode("utf-8")
        raise AssertionError(f"unexpected fetch: {url}")

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = Path(dest_dir) / filename
        path.write_bytes(file_content)
        return path

    widget = _make_widget(qgis_app, fetch=fetch, download_fn=fake_download)
    widget.connection_manager.reload_connections(
        select_key="swisstopo_ch", select_role="swissALTI3D"
    )
    widget.tree.topLevelItem(0).setSelected(True)
    widget.target_picker.repo_edit.setText(str(tmp_path / "repo"))

    captured = []
    widget.addRasterLayer.connect(
        lambda uri, name, provider: captured.append((uri, name, provider))
    )

    widget._on_download_clicked()

    assert len(captured) == 1
    assert any((tmp_path / "repo").rglob("tile.tif"))


# --------------------------------------------------------------- geonorge_catalog connection

# Column names confirmed real and correct here (Tema/Tittel, not
# Topic/Title) - see test_gateways.py's own identically-named fixture
# and gateways/geonorge_catalog.py's module docstring for the full
# story: this project's first attempt at this fixture used English
# column names, matching an initial (wrong) assumption in the module
# itself about the real API's header language. Both were fixed
# together once a real CSV response confirmed the actual Norwegian
# header - but this second, separate copy of the fixture (written for
# these widget-level tests specifically) was missed at the time, and
# stayed stale until running against real QGIS surfaced it: with the
# old "Topic" header, row.get("Tema") (what the real, already-fixed
# module code looks up) returns None for every row, so no root-level
# Theme entries are ever produced at all - "the tree is unexpectedly
# empty" rather than a crash, exactly the failure mode a silently
# wrong test fixture produces.
_GEONORGE_CSV_HEADER = (
    "Tittel;Type;Tema;Organisasjon;Åpne data;DOK-data;Uuid;Wms-url;"
    "Wfs-url;Atom-feed;Dekningsområde;Distribusjonsform;Distribusjons-url"
)


def _geonorge_csv_row(title, topic, atom_feed):
    return (
        f"{title};dataset;{topic};Kartverket;Open data;;{title.lower()}-uuid;;;"
        f"{atom_feed};National;GEONORGE:DOWNLOAD;"
    )


def test_selecting_the_geonorge_connection_shows_themes_then_datasets(qgis_app):
    """End-to-end counterpart to test_gateways.py's own geonorge_catalog
    unit tests - confirms the real widget, not just the gateway module
    in isolation, correctly shows Level 1 (themes) then, after
    double-clicking one, Level 2 (datasets with a real Atom feed)."""
    from sigate.gateways.geonorge_catalog import CATALOG_URL

    csv_content = "\n".join(
        [
            _GEONORGE_CSV_HEADER,
            _geonorge_csv_row(
                "DTM10 Terrengmodell",
                "Høydedata",
                "http://nedlasting.geonorge.no/geonorge/ATOM-feeds/DTM10.xml",
            ),
        ]
    ).encode("utf-8")

    def fetch(url):
        if url == CATALOG_URL:
            return csv_content
        raise AssertionError(f"unexpected fetch: {url}")

    widget = _make_widget(qgis_app, fetch=fetch, download_fn=None)
    widget.connection_manager.reload_connections(select_key="geonorge_no")

    assert widget.tree.topLevelItemCount() == 1
    assert widget.tree.topLevelItem(0).text(1) == "Høydedata"

    widget._on_item_double_clicked(widget.tree.topLevelItem(0), 0)

    assert widget.tree.topLevelItemCount() == 1
    assert widget.tree.topLevelItem(0).text(1) == "DTM10 Terrengmodell"


def test_downloading_via_geonorge_delegates_to_real_atom_parsing_end_to_end(
    qgis_app, tmp_path
):
    """The geonorge_catalog counterpart to the stac/atom download tests
    above - confirms the delegation to gateways.atom this module's
    whole design rests on actually works through the real widget, not
    just in gateway-level isolation."""
    from sigate.gateways.geonorge_catalog import CATALOG_URL

    feed_url = "http://nedlasting.geonorge.no/geonorge/ATOM-feeds/DTM10.xml"
    csv_content = "\n".join(
        [
            _GEONORGE_CSV_HEADER,
            _geonorge_csv_row("DTM10 Terrengmodell", "Høydedata", feed_url),
        ]
    ).encode("utf-8")
    file_content = b"real terrain tile content"
    atom_feed_xml = f"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:gpf_dl="http://x"
     gpf_dl:page="1" gpf_dl:pagesize="50" gpf_dl:pagecount="1" gpf_dl:totalentries="1">
<entry><title>tile</title><id>p</id>
<link href="https://nedlasting.geonorge.no/dtm10.tif" type="image/tiff" length="{len(file_content)}"/>
</entry></feed>""".encode("utf-8")

    def fetch(url):
        if url == CATALOG_URL:
            return csv_content
        # atom.fetch_page/fetch_all_pages always appends its own
        # pagination query params (page=/limit=/pagesize=) via
        # build_url before fetching - the real URL is never the bare
        # feed_url, matching the same startswith pattern already used
        # by this file's other atom-based tests (e.g. "page=2" in url)
        # rather than exact equality, which could never match a real
        # atom fetch.
        if url.startswith(feed_url):
            return atom_feed_xml
        raise AssertionError(f"unexpected fetch: {url}")

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = Path(dest_dir) / filename
        path.write_bytes(file_content)
        return path

    widget = _make_widget(qgis_app, fetch=fetch, download_fn=fake_download)
    widget.connection_manager.reload_connections(select_key="geonorge_no")
    widget._on_item_double_clicked(widget.tree.topLevelItem(0), 0)  # into Høydedata
    widget.tree.topLevelItem(0).setSelected(True)  # the DTM10 dataset entry
    widget.target_picker.repo_edit.setText(str(tmp_path / "repo"))

    captured = []
    widget.addRasterLayer.connect(
        lambda uri, name, provider: captured.append((uri, name, provider))
    )

    widget._on_download_clicked()

    assert len(captured) == 1
    assert any((tmp_path / "repo").rglob("dtm10.tif"))
