"""
Tests for sigate.ui.download_flow - the shared download-through-add-layer
orchestration extracted from the Bulk Listing tab so the WFS tab's
file-index use pattern could reuse it rather than duplicate it. Most of
this logic was already covered indirectly through
test_bulk_listing_widget.py before the extraction; this file adds direct
coverage of the module itself, calling it with a minimal fake "tab"
object rather than a full widget.

Every call injects a synchronous fake progress_runner
(_sync_progress_runner below) rather than letting the real default (a
modal QDialog backed by a real QgsTask) run - the real one blocks on
QDialog.exec() until a background task finishes, which has nothing to
drive it forward deterministically in a headless test with no real user
to click anything. The fake calls download.pipeline.download_items()
directly instead, exercising all the same logic synchronously.
"""

from pathlib import Path

import pytest


def _sync_progress_runner(parent, items, central_repo, download_fn):
    from sigate.download import pipeline

    outcomes = pipeline.download_items(items, central_repo, download_fn=download_fn)
    return outcomes, False


@pytest.fixture(autouse=True)
def clean_target_picker_persistence(qgis_app):
    """These tests construct real TargetPicker instances, which now
    persist the last-used central-repo/destination paths across
    sessions - reset between tests to avoid one test's path leaking
    into another's, same as the equivalent fixture in
    test_bulk_listing_widget.py and test_wfs_widget.py."""
    from sigate.ui import settings as sigate_settings

    sigate_settings.set_last_central_repo(None)
    sigate_settings.set_last_destination(None)
    yield
    sigate_settings.set_last_central_repo(None)
    sigate_settings.set_last_destination(None)


@pytest.fixture
def no_block_message_boxes(monkeypatch):
    from qgis.PyQt.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))


class _FakeParent:
    """A minimal stand-in for the owning tab - just enough of QWidget's
    interface (tr) for download_flow's QMessageBox calls to work."""

    def tr(self, text):
        return text


def _make_target_picker(qgis_app, tmp_path, require_destination=False):
    from sigate.ui.target_picker import TargetPicker

    picker = TargetPicker()
    picker.repo_edit.setText(str(tmp_path / "repo"))
    if require_destination:
        picker.dest_edit.setText(str(tmp_path / "dest"))
    return picker


def test_guess_layer_kind():
    from sigate.ui.download_flow import guess_layer_kind

    assert guess_layer_kind(Path("a.tif")) == "raster"
    assert guess_layer_kind(Path("a.shp")) == "vector"
    assert guess_layer_kind(Path("a.txt")) is None


def test_run_download_and_add_layers_simple_file(
    qgis_app, tmp_path, no_block_message_boxes
):
    from sigate.download.pipeline import DownloadItem
    from sigate.ui.download_flow import run_download_and_add_layers

    content = b"fake raster bytes"

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = Path(dest_dir) / filename
        path.write_bytes(content)
        return path

    picker = _make_target_picker(qgis_app, tmp_path)
    added = []

    run_download_and_add_layers(
        _FakeParent(),
        [DownloadItem(url="https://x/a.tif", filename="a.tif")],
        picker,
        sevenzip_exe=None,
        set_status=lambda text: None,
        add_layer=lambda path: added.append(path),
        download_fn=fake_download,
        progress_runner=_sync_progress_runner,
    )

    assert len(added) == 1
    assert added[0] == tmp_path / "repo" / "a.tif"
    assert added[0].read_bytes() == content


def test_run_download_and_add_layers_rejects_byte_count_mismatch(
    qgis_app, tmp_path, no_block_message_boxes
):
    from sigate.download.pipeline import DownloadItem
    from sigate.ui.download_flow import run_download_and_add_layers

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = Path(dest_dir) / filename
        path.write_bytes(b"short")
        return path

    picker = _make_target_picker(qgis_app, tmp_path)
    added = []

    run_download_and_add_layers(
        _FakeParent(),
        [DownloadItem(url="https://x/a.tif", filename="a.tif", declared_size=99999)],
        picker,
        sevenzip_exe=None,
        set_status=lambda text: None,
        add_layer=lambda path: added.append(path),
        download_fn=fake_download,
        progress_runner=_sync_progress_runner,
    )

    assert added == []
    assert not (tmp_path / "repo" / "a.tif").exists()


def test_run_download_and_add_layers_extracts_everything_into_an_archive_named_folder(
    qgis_app, tmp_path, no_block_message_boxes
):
    """Requested directly: extraction now keeps every file the archive
    contained (not just the ones classified as "data"), and extracts
    into a subfolder named after the archive itself - so extracting more
    than one archive into the same destination can't mix their contents
    together, and non-geodata files (a metadata readme, here) are kept
    on disk even though they still correctly aren't added as a QGIS
    layer (guess_layer_kind has no kind for a plain .txt file)."""
    import io
    import zipfile

    from sigate.download.pipeline import DownloadItem
    from sigate.ui.download_flow import guess_layer_kind, run_download_and_add_layers

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("1_DONNEES_LIVRAISON_x/PRODUCT/tile.tif", b"raster data")
        zf.writestr("2_METADONNEES_LIVRAISON_x/readme.txt", b"metadata")
    zip_bytes = zip_buffer.getvalue()

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = Path(dest_dir) / filename
        path.write_bytes(zip_bytes)
        return path

    picker = _make_target_picker(qgis_app, tmp_path, require_destination=True)
    added = []

    def fake_add_layer(path):
        # Mirrors the real per-tab _add_layer (bulk_listing_widget.py /
        # wfs_widget.py): run_download_and_add_layers documents add_layer
        # as "supplied by the calling tab, since adding a layer means
        # emitting that tab's own addRasterLayer/addVectorLayer signal,
        # not something this shared module can do on its own" - filtering
        # by recognized kind is the caller's job, not this module's, so a
        # faithful fake has to replicate it rather than record everything
        # unconditionally.
        if guess_layer_kind(path) is not None:
            added.append(path)

    run_download_and_add_layers(
        _FakeParent(),
        [
            DownloadItem(
                url="https://x/archive.zip",
                filename="archive.zip",
                declared_size=len(zip_bytes),
            )
        ],
        picker,
        sevenzip_exe=None,
        set_status=lambda text: None,
        add_layer=fake_add_layer,
        download_fn=fake_download,
        progress_runner=_sync_progress_runner,
    )

    # Only the raster tile was added as a layer - the readme has no
    # recognized layer kind - but both files are genuinely on disk,
    # kept, inside a subfolder named after the archive ("archive.zip" ->
    # "archive").
    assert len(added) == 1
    assert added[0].name == "tile.tif"
    archive_folder = tmp_path / "dest" / "archive"
    assert any(p.name == "tile.tif" for p in archive_folder.rglob("*"))
    assert any(p.name == "readme.txt" for p in archive_folder.rglob("*"))


def test_building_a_mosaic_also_builds_overviews_for_it(
    qgis_app, tmp_path, no_block_message_boxes, monkeypatch
):
    """The actual gap being closed: build_vrt_mosaic alone only ever
    produced the VRT half of the real documented artifact shape
    (docs/spec.md's "dem73.vrt" + "dem73.vrt.ovr" pair) - building a
    fresh mosaic must also build its pyramid, not leave that as a
    separate, never-taken step. gdaladdo/gdalbuildvrt aren't available
    to run for real here (same real limitation this project's
    mosaic-building has always had), so both are monkeypatched to
    confirm the *wiring* - that build_overviews is actually called with
    the mosaic's own path - rather than their real subprocess behavior."""
    import io
    import zipfile

    import sigate.ui.download_flow as download_flow_module
    from sigate.download.pipeline import DownloadItem
    from sigate.ui.download_flow import run_download_and_add_layers

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("1_DONNEES_LIVRAISON_x/PRODUCT/r0c0.tif", b"tile a")
        zf.writestr("1_DONNEES_LIVRAISON_x/PRODUCT/r0c1.tif", b"tile b")
    zip_bytes = zip_buffer.getvalue()

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = Path(dest_dir) / filename
        path.write_bytes(zip_bytes)
        return path

    mosaic_path = tmp_path / "dest" / "sigate_mosaic_archive.vrt"

    def fake_build_vrt_mosaic(tile_paths, dest_dir, product_key, log=None):
        mosaic_path.parent.mkdir(parents=True, exist_ok=True)
        mosaic_path.write_text("<VRTDataset/>")
        return mosaic_path

    overview_calls = []
    monkeypatch.setattr(download_flow_module, "build_vrt_mosaic", fake_build_vrt_mosaic)
    monkeypatch.setattr(
        download_flow_module,
        "build_overviews",
        lambda path, log=None: overview_calls.append(path),
    )

    picker = _make_target_picker(qgis_app, tmp_path, require_destination=True)
    added = []

    run_download_and_add_layers(
        _FakeParent(),
        [
            DownloadItem(
                url="https://x/archive.zip",
                filename="archive.zip",
                declared_size=len(zip_bytes),
            )
        ],
        picker,
        sevenzip_exe=None,
        set_status=lambda text: None,
        add_layer=lambda path: added.append(path),
        download_fn=fake_download,
        progress_runner=_sync_progress_runner,
    )

    assert overview_calls == [mosaic_path]
    assert added == [mosaic_path]


def test_revisiting_an_existing_mosaic_without_overviews_builds_them_lazily(
    qgis_app, tmp_path, no_block_message_boxes, monkeypatch
):
    """A mosaic from before overview-building existed (or a previous
    build_overviews attempt that failed) shouldn't stay permanently
    without pyramids just because the very first visit predated this
    fix - a revisit finding an existing mosaic must check for and build
    missing overviews too, not just reuse the VRT as-is."""
    import sigate.ui.download_flow as download_flow_module
    from sigate.download.pipeline import DownloadItem
    from sigate.ui.download_flow import run_download_and_add_layers

    dest = tmp_path / "dest" / "archive"
    dest.mkdir(parents=True)
    existing_mosaic = dest / "sigate_mosaic_archive.vrt"
    # References the real tile's own filename (r0c0.tif, extracted
    # below) so mosaic_covers_tiles correctly identifies this as
    # already up to date - this test's actual intent is "missing only
    # its overview pyramid", not "stale tile coverage". A bare,
    # source-less placeholder here would (correctly, by the staleness
    # fix this comment references) look stale and trigger a real
    # rebuild via actual gdalbuildvrt - which fails for real against
    # this test's non-TIFF fake tile content, a real gap the earlier
    # staleness fix introduced into this specific pre-existing test.
    existing_mosaic.write_text(
        "<VRTDataset><VRTRasterBand><SimpleSource>"
        '<SourceFilename relativeToVRT="1">r0c0.tif</SourceFilename>'
        "</SimpleSource></VRTRasterBand></VRTDataset>"
    )
    # deliberately no .ovr sidecar - simulates the gap this fix closes

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("1_DONNEES_LIVRAISON_x/PRODUCT/r0c0.tif", b"tile a")
        path = Path(dest_dir) / filename
        path.write_bytes(buf.getvalue())
        return path

    overview_calls = []
    monkeypatch.setattr(
        download_flow_module,
        "build_overviews",
        lambda path, log=None: overview_calls.append(path),
    )

    picker = _make_target_picker(qgis_app, tmp_path, require_destination=True)
    added = []

    run_download_and_add_layers(
        _FakeParent(),
        [DownloadItem(url="https://x/archive.zip", filename="archive.zip")],
        picker,
        sevenzip_exe=None,
        set_status=lambda text: None,
        add_layer=lambda path: added.append(path),
        download_fn=fake_download,
        progress_runner=_sync_progress_runner,
    )

    assert overview_calls == [existing_mosaic]
    assert added == [existing_mosaic]


def test_overview_build_failure_still_adds_the_mosaic(
    qgis_app, tmp_path, no_block_message_boxes, monkeypatch
):
    """A gdaladdo failure (missing executable, unwritable destination,
    etc.) must not cost the user the mosaic itself - it's still fully
    usable without a pyramid, just slower to render at reduced zoom."""
    import io
    import zipfile

    import sigate.ui.download_flow as download_flow_module
    from sigate.download.pipeline import DownloadItem
    from sigate.ui.download_flow import run_download_and_add_layers

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("1_DONNEES_LIVRAISON_x/PRODUCT/r0c0.tif", b"tile a")
        zf.writestr("1_DONNEES_LIVRAISON_x/PRODUCT/r0c1.tif", b"tile b")
    zip_bytes = zip_buffer.getvalue()

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = Path(dest_dir) / filename
        path.write_bytes(zip_bytes)
        return path

    mosaic_path = tmp_path / "dest" / "sigate_mosaic_archive.vrt"

    def fake_build_vrt_mosaic(tile_paths, dest_dir, product_key, log=None):
        mosaic_path.parent.mkdir(parents=True, exist_ok=True)
        mosaic_path.write_text("<VRTDataset/>")
        return mosaic_path

    def failing_build_overviews(path, log=None):
        raise RuntimeError("gdaladdo: command not found")

    monkeypatch.setattr(download_flow_module, "build_vrt_mosaic", fake_build_vrt_mosaic)
    monkeypatch.setattr(
        download_flow_module, "build_overviews", failing_build_overviews
    )

    picker = _make_target_picker(qgis_app, tmp_path, require_destination=True)
    added = []

    run_download_and_add_layers(
        _FakeParent(),
        [
            DownloadItem(
                url="https://x/archive.zip",
                filename="archive.zip",
                declared_size=len(zip_bytes),
            )
        ],
        picker,
        sevenzip_exe=None,
        set_status=lambda text: None,
        add_layer=lambda path: added.append(path),
        download_fn=fake_download,
        progress_runner=_sync_progress_runner,
    )

    # the mosaic still gets added despite the overview failure
    assert added == [mosaic_path]


def test_multiple_plain_tiles_downloaded_independently_offer_a_mosaic(
    qgis_app, tmp_path, no_block_message_boxes, monkeypatch
):
    """The actual reported gap: a WFS file-index download (e.g. LiDAR
    HD's ":dalle" layer, the real case reported) delivers several plain
    .tif files directly, each its own DownloadItem - never wrapped in an
    archive. Mosaic/pyramid-building had only ever been wired inside
    archive processing, so this batch got no mosaic offer at all
    (nothing to do with GDAL not being on PATH - the code never even
    tried). Must now be checked as a batch, the same way tiles extracted
    from one archive already were."""
    import sigate.ui.download_flow as download_flow_module
    from sigate.download.pipeline import DownloadItem
    from sigate.ui.download_flow import run_download_and_add_layers

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = Path(dest_dir) / filename
        path.write_bytes(b"tile data")
        return path

    mosaic_path = tmp_path / "repo" / "sigate_mosaic_lhd.vrt"
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

    picker = _make_target_picker(qgis_app, tmp_path)
    added = []

    run_download_and_add_layers(
        _FakeParent(),
        [
            DownloadItem(url="https://x/LHD_D001.tif", filename="LHD_D001.tif"),
            DownloadItem(url="https://x/LHD_D002.tif", filename="LHD_D002.tif"),
            DownloadItem(url="https://x/LHD_D003.tif", filename="LHD_D003.tif"),
            DownloadItem(url="https://x/LHD_D004.tif", filename="LHD_D004.tif"),
        ],
        picker,
        sevenzip_exe=None,
        set_status=lambda text: None,
        add_layer=lambda path: added.append(path),
        download_fn=fake_download,
        progress_runner=_sync_progress_runner,
    )

    # one mosaic added, not four independent tiles
    assert added == [mosaic_path]
    assert overview_calls == [mosaic_path]


def test_declining_the_mosaic_offer_adds_each_plain_tile_individually(
    qgis_app, tmp_path, monkeypatch
):
    """The other side of the same fix: declining the offer must still
    add every tile independently, exactly as before this fix existed -
    the offer is additive, not a forced behavior change."""
    from qgis.PyQt.QtWidgets import QMessageBox

    from sigate.download.pipeline import DownloadItem
    from sigate.ui.download_flow import run_download_and_add_layers

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = Path(dest_dir) / filename
        path.write_bytes(b"tile data")
        return path

    picker = _make_target_picker(qgis_app, tmp_path)
    added = []

    run_download_and_add_layers(
        _FakeParent(),
        [
            DownloadItem(url="https://x/a.tif", filename="a.tif"),
            DownloadItem(url="https://x/b.tif", filename="b.tif"),
        ],
        picker,
        sevenzip_exe=None,
        set_status=lambda text: None,
        add_layer=lambda path: added.append(path),
        download_fn=fake_download,
        progress_runner=_sync_progress_runner,
    )

    assert {p.name for p in added} == {"a.tif", "b.tif"}


def test_plain_files_that_do_not_look_tileable_are_added_individually_no_prompt(
    qgis_app, tmp_path, monkeypatch
):
    """Mixed formats (or a single file) must not trigger a mosaic offer
    at all - confirmed here by making QMessageBox.question raise, so the
    test fails loudly if a prompt is shown when it shouldn't be, rather
    than silently passing either way."""
    from qgis.PyQt.QtWidgets import QMessageBox

    from sigate.download.pipeline import DownloadItem
    from sigate.ui.download_flow import run_download_and_add_layers

    def question_should_not_be_called(*a, **k):
        raise AssertionError("no mosaic offer should be shown for mixed formats")

    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(question_should_not_be_called)
    )

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = Path(dest_dir) / filename
        path.write_bytes(b"data")
        return path

    picker = _make_target_picker(qgis_app, tmp_path)
    added = []

    run_download_and_add_layers(
        _FakeParent(),
        [
            DownloadItem(url="https://x/a.tif", filename="a.tif"),
            DownloadItem(url="https://x/b.shp", filename="b.shp"),
        ],
        picker,
        sevenzip_exe=None,
        set_status=lambda text: None,
        add_layer=lambda path: added.append(path),
        download_fn=fake_download,
        progress_runner=_sync_progress_runner,
    )

    assert {p.name for p in added} == {"a.tif", "b.shp"}


def test_revisiting_plain_tiles_with_an_existing_mosaic_missing_overviews_builds_them(
    qgis_app, tmp_path, no_block_message_boxes, monkeypatch
):
    """Symmetry with the archive-download revisit case: a mosaic from a
    previous plain-tile batch that's missing its pyramid must get one
    built lazily on this revisit too, not just when the mosaic came from
    an archive."""
    import sigate.ui.download_flow as download_flow_module
    from sigate.download.pipeline import DownloadItem
    from sigate.ui.download_flow import run_download_and_add_layers

    repo = tmp_path / "repo"
    repo.mkdir()
    existing_mosaic = repo / "sigate_mosaic_lhd.vrt"
    # References both real tile filenames (LHD_D001.tif, LHD_D002.tif,
    # downloaded below) so mosaic_covers_tiles correctly identifies
    # this as already up to date - see the archive-download test
    # above's own comment for why a bare, source-less placeholder here
    # would incorrectly look stale and trigger a real gdalbuildvrt
    # rebuild against this test's non-TIFF fake tile content.
    existing_mosaic.write_text(
        "<VRTDataset><VRTRasterBand><SimpleSource>"
        '<SourceFilename relativeToVRT="1">LHD_D001.tif</SourceFilename>'
        "</SimpleSource></VRTRasterBand>"
        "<VRTRasterBand><SimpleSource>"
        '<SourceFilename relativeToVRT="1">LHD_D002.tif</SourceFilename>'
        "</SimpleSource></VRTRasterBand></VRTDataset>"
    )
    # deliberately no .ovr sidecar

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = Path(dest_dir) / filename
        path.write_bytes(b"tile data")
        return path

    overview_calls = []
    monkeypatch.setattr(
        download_flow_module,
        "build_overviews",
        lambda path, log=None: overview_calls.append(path),
    )

    picker = _make_target_picker(qgis_app, tmp_path)
    added = []

    run_download_and_add_layers(
        _FakeParent(),
        [
            DownloadItem(url="https://x/LHD_D001.tif", filename="LHD_D001.tif"),
            DownloadItem(url="https://x/LHD_D002.tif", filename="LHD_D002.tif"),
        ],
        picker,
        sevenzip_exe=None,
        set_status=lambda text: None,
        add_layer=lambda path: added.append(path),
        download_fn=fake_download,
        progress_runner=_sync_progress_runner,
    )

    assert overview_calls == [existing_mosaic]
    assert added == [existing_mosaic]


def test_group_outcomes_by_product_keeps_mnt_mns_mnh_apart():
    """IGN's LiDAR HD metadata layer delivers a tile's MNT, MNS and MNH
    in one batch - same format and resolution, so they would otherwise
    look like one tileable set and be mosaicked together."""
    from pathlib import Path

    from sigate.download.pipeline import DownloadItem, DownloadOutcome
    from sigate.ui.download_flow import group_outcomes_by_product

    outcomes, paths = [], []
    for product in ("MNT", "MNS", "MNH"):
        for col in ("0998", "0999"):
            path = Path(f"/r/LHD_FXX_{col}_6542_{product}.tif")
            outcomes.append(
                DownloadOutcome(
                    item=DownloadItem(url="u", filename=path.name, product=product),
                    path=path,
                    already_existed=False,
                )
            )
            paths.append(path)

    groups = group_outcomes_by_product(outcomes, paths)

    assert sorted(groups) == ["MNH", "MNS", "MNT"]
    assert all(len(v) == 2 for v in groups.values())
    assert all(p.name.endswith(f"_{k}.tif") for k, v in groups.items() for p in v)


def test_items_without_a_product_stay_one_group():
    from pathlib import Path

    from sigate.download.pipeline import DownloadItem, DownloadOutcome
    from sigate.ui.download_flow import group_outcomes_by_product

    outcomes = [
        DownloadOutcome(
            item=DownloadItem(url="u", filename=n), path=Path(n), already_existed=False
        )
        for n in ("a.tif", "b.tif")
    ]
    groups = group_outcomes_by_product(outcomes, [Path("a.tif"), Path("b.tif")])
    assert list(groups) == [None] and len(groups[None]) == 2


def test_guess_layer_kind_treats_copc_laz_as_point_cloud():
    from pathlib import Path

    from sigate.ui.download_flow import guess_layer_kind

    assert guess_layer_kind(Path("x_PTS.copc.laz")) == "point_cloud"
    assert guess_layer_kind(Path("x.laz")) is None
    assert guess_layer_kind(Path("x.tif")) == "raster"


def test_flow_adds_each_product_into_its_own_layer_tree_group(
    qgis_app, tmp_path, no_block_message_boxes, monkeypatch
):
    """The group a layer is added into is decided by the flow: group_base
    (the tab's source name) > layer folder > product."""
    from sigate.download.pipeline import DownloadItem
    from sigate.ui.download_flow import run_download_and_add_layers
    from sigate.ui.layer_groups import current_group_path

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = Path(dest_dir) / filename
        path.write_bytes(b"data")
        return path

    seen = []
    picker = _make_target_picker(qgis_app, tmp_path)
    run_download_and_add_layers(
        _FakeParent(),
        [
            DownloadItem(
                url="https://x/a.tif",
                filename="LHD_0998_MNT.tif",
                subdirectory="IGN (France)/raster/layer_x",
                product="MNT",
            ),
            DownloadItem(
                url="https://x/b.tif",
                filename="LHD_0998_MNS.tif",
                subdirectory="IGN (France)/raster/layer_x",
                product="MNS",
            ),
        ],
        picker,
        None,
        set_status=lambda s: None,
        add_layer=lambda path: seen.append((path.name, current_group_path())),
        download_fn=fake_download,
        progress_runner=_sync_progress_runner,
        group_base=["IGN (France)"],
    )

    assert sorted(seen) == [
        ("LHD_0998_MNS.tif", ["IGN (France)", "layer_x", "MNS"]),
        ("LHD_0998_MNT.tif", ["IGN (France)", "layer_x", "MNT"]),
    ]
