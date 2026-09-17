"""
Tests for download.mosaic - the VRT-mosaic-building module, previously
only ever exercised indirectly through widget-level tests
(test_download_flow.py, which needs a real QGIS and can't run in this
sandbox). All of this module's own logic is pure Python (subprocess
calls aside), so it's tested directly here.
"""

from pathlib import Path

import pytest

from sigate.download import mosaic


def test_raster_tile_paths_keeps_only_known_raster_extensions(tmp_path):
    paths = [
        tmp_path / "a.tif",
        tmp_path / "b.TIFF",
        tmp_path / "readme.txt",
        tmp_path / "c.jp2",
        tmp_path / "index.shp",
    ]
    assert mosaic.raster_tile_paths(paths) == [
        tmp_path / "a.tif",
        tmp_path / "b.TIFF",
        tmp_path / "c.jp2",
    ]


def test_looks_like_tileable_raster_set_true_for_multiple_same_extension(tmp_path):
    paths = [tmp_path / "a.tif", tmp_path / "b.tif", tmp_path / "c.tif"]
    assert mosaic.looks_like_tileable_raster_set(paths) is True


def test_looks_like_tileable_raster_set_false_for_a_single_file(tmp_path):
    assert mosaic.looks_like_tileable_raster_set([tmp_path / "a.tif"]) is False


def test_looks_like_tileable_raster_set_false_for_mixed_extensions(tmp_path):
    """A mix of extensions (e.g. .tif and .jp2 together) doesn't look
    like one coherent tile set the way multiple files sharing the same
    extension does."""
    paths = [tmp_path / "a.tif", tmp_path / "b.jp2"]
    assert mosaic.looks_like_tileable_raster_set(paths) is False


def test_mosaic_filename_uses_lowercased_product_key():
    assert mosaic.mosaic_filename("RGEALTI") == "sigate_mosaic_rgealti.vrt"


def test_find_existing_mosaic_returns_none_when_absent(tmp_path):
    assert mosaic.find_existing_mosaic(tmp_path, "rgealti") is None


def test_find_existing_mosaic_returns_the_path_when_present(tmp_path):
    expected = tmp_path / "sigate_mosaic_rgealti.vrt"
    expected.write_text("<VRTDataset/>")
    assert mosaic.find_existing_mosaic(tmp_path, "rgealti") == expected


def _write_vrt(path: Path, source_filenames):
    sources_xml = "\n".join(
        f'    <SourceFilename relativeToVRT="1">{name}</SourceFilename>'
        for name in source_filenames
    )
    path.write_text(
        f"""<VRTDataset rasterXSize="1" rasterYSize="1">
  <VRTRasterBand dataType="Byte" band="1">
    <SimpleSource>
{sources_xml}
    </SimpleSource>
  </VRTRasterBand>
</VRTDataset>"""
    )


def test_mosaic_covers_tiles_true_when_source_files_exactly_match(tmp_path):
    vrt = tmp_path / "m.vrt"
    _write_vrt(vrt, ["a.tif", "b.tif"])
    tiles = [tmp_path / "a.tif", tmp_path / "b.tif"]
    assert mosaic.mosaic_covers_tiles(vrt, tiles) is True


def test_mosaic_covers_tiles_false_when_a_new_tile_is_missing_from_the_vrt(tmp_path):
    """Regression test for the actual reported failure ("the vrt
    however is not built properly and a preexisting one was included"):
    a VRT built from an earlier, smaller tile set must not be silently
    reused once more tiles are present on disk."""
    vrt = tmp_path / "m.vrt"
    _write_vrt(vrt, ["a.tif"])
    tiles = [tmp_path / "a.tif", tmp_path / "b.tif"]
    assert mosaic.mosaic_covers_tiles(vrt, tiles) is False


def test_mosaic_covers_tiles_false_when_the_vrt_references_a_tile_no_longer_present(
    tmp_path,
):
    """The reverse mismatch - a stale VRT referencing a file that isn't
    part of the current tile set at all - is just as stale as one
    missing tiles, and must also trigger a rebuild."""
    vrt = tmp_path / "m.vrt"
    _write_vrt(vrt, ["a.tif", "old_tile_from_a_previous_delivery.tif"])
    tiles = [tmp_path / "a.tif", tmp_path / "b.tif"]
    assert mosaic.mosaic_covers_tiles(vrt, tiles) is False


def test_mosaic_covers_tiles_matches_by_basename_regardless_of_directory(tmp_path):
    """A VRT commonly stores source paths relative to its own
    directory - comparison must be robust to that, not require exact
    full-path equality."""
    vrt = tmp_path / "sub" / "m.vrt"
    vrt.parent.mkdir()
    _write_vrt(vrt, ["a.tif", "b.tif"])
    tiles = [Path("/completely/different/absolute/path") / "a.tif", tmp_path / "b.tif"]
    assert mosaic.mosaic_covers_tiles(vrt, tiles) is True


def test_mosaic_covers_tiles_false_for_unparseable_vrt_content(tmp_path):
    """A corrupted or unexpected file at the mosaic's expected path is
    treated as stale (triggering a safe rebuild) rather than raising -
    a broken .vrt file blocking the whole download would be worse than
    just rebuilding it."""
    vrt = tmp_path / "m.vrt"
    vrt.write_text("not valid xml at all{{{")
    tiles = [tmp_path / "a.tif"]
    assert mosaic.mosaic_covers_tiles(vrt, tiles) is False


def test_build_vrt_mosaic_passes_overwrite_flag(tmp_path, monkeypatch):
    """Regression test for the actual fix this staleness detection
    needed to work at all: build_vrt_mosaic must succeed when called a
    second time against a destination that already exists (the
    rebuild-a-stale-mosaic path), not silently fail or behave
    unpredictably on gdalbuildvrt's own default when the output already
    exists."""
    calls = []

    def fake_run(cmd, check=True, capture_output=True):
        calls.append(cmd)

    monkeypatch.setattr(mosaic.subprocess, "run", fake_run)
    mosaic.build_vrt_mosaic(
        [tmp_path / "a.tif", tmp_path / "b.tif"], tmp_path, "rgealti"
    )
    assert "-overwrite" in calls[0]


def test_build_vrt_mosaic_returns_the_correct_destination_path(tmp_path, monkeypatch):
    monkeypatch.setattr(mosaic.subprocess, "run", lambda *a, **k: None)
    result = mosaic.build_vrt_mosaic([tmp_path / "a.tif"], tmp_path, "RGEALTI")
    assert result == tmp_path / "sigate_mosaic_rgealti.vrt"


def test_overview_path_appends_ovr_extension():
    assert mosaic.overview_path(Path("/x/mosaic.vrt")) == Path("/x/mosaic.vrt.ovr")


def test_has_overviews_false_when_sidecar_absent(tmp_path):
    vrt = tmp_path / "m.vrt"
    vrt.write_text("<VRTDataset/>")
    assert mosaic.has_overviews(vrt) is False


def test_has_overviews_true_when_sidecar_present(tmp_path):
    vrt = tmp_path / "m.vrt"
    vrt.write_text("<VRTDataset/>")
    (tmp_path / "m.vrt.ovr").write_text("")
    assert mosaic.has_overviews(vrt) is True


def test_build_overviews_uses_average_resampling_by_default(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, check=True, capture_output=True):
        calls.append(cmd)

    monkeypatch.setattr(mosaic.subprocess, "run", fake_run)
    vrt = tmp_path / "m.vrt"
    mosaic.build_overviews(vrt)
    assert "-r" in calls[0]
    assert "average" in calls[0]


@pytest.mark.parametrize("missing_text", [None, "", "   "])
def test_referenced_source_filenames_skips_empty_elements(tmp_path, missing_text):
    vrt = tmp_path / "m.vrt"
    if missing_text is None:
        vrt.write_text("<VRTDataset><SourceFilename/></VRTDataset>")
    else:
        vrt.write_text(
            f"<VRTDataset><SourceFilename>{missing_text}</SourceFilename></VRTDataset>"
        )
    assert mosaic._referenced_source_filenames(vrt) == set()
