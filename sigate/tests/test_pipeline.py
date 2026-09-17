"""
Tests for sigate.download.pipeline - every stage tested independently,
using injected fake download functions (no real network) and, for the
archive-handling stages, real zip/7z archives built on the fly (the same
approach used for sigate.download.archive's own tests).
"""

import zipfile
from pathlib import Path

import pytest

from sigate.download import pipeline
from sigate.download.targets import DownloadTargets

# --------------------------------------------------------------------------- already-downloaded check


def test_check_already_downloaded_reports_presence_per_file(tmp_path):
    (tmp_path / "present.txt").write_text("x")
    items = [
        pipeline.DownloadItem(url="https://x/present.txt", filename="present.txt"),
        pipeline.DownloadItem(url="https://x/missing.txt", filename="missing.txt"),
    ]
    result = pipeline.check_already_downloaded(items, tmp_path)
    assert result == {"present.txt": True, "missing.txt": False}


# --------------------------------------------------------------------------- size / free space


def test_combined_declared_size_sums_when_all_known():
    items = [
        pipeline.DownloadItem(url="https://x/a", filename="a", declared_size=100),
        pipeline.DownloadItem(url="https://x/b", filename="b", declared_size=250),
    ]
    assert pipeline.combined_declared_size(items) == 350


def test_combined_declared_size_none_when_any_unknown():
    items = [
        pipeline.DownloadItem(url="https://x/a", filename="a", declared_size=100),
        pipeline.DownloadItem(url="https://x/b", filename="b", declared_size=None),
    ]
    assert pipeline.combined_declared_size(items) is None


def test_exceeds_size_warning_threshold():
    assert pipeline.exceeds_size_warning_threshold(2 * 1024**3) is True
    assert pipeline.exceeds_size_warning_threshold(100) is False
    assert pipeline.exceeds_size_warning_threshold(None) is False


def test_check_free_space_for_download_true_when_size_unknown(tmp_path):
    assert pipeline.check_free_space_for_download(tmp_path, None) is True


def test_check_free_space_for_download_false_for_absurd_size(tmp_path):
    assert pipeline.check_free_space_for_download(tmp_path, 10**18) is False


# --------------------------------------------------------------------------- download + integrity


def test_download_items_skips_already_present_file(tmp_path):
    (tmp_path / "existing.txt").write_text("already here")
    calls = []

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        calls.append(url)
        raise AssertionError("should not be called for an already-present file")

    items = [
        pipeline.DownloadItem(url="https://x/existing.txt", filename="existing.txt")
    ]
    outcomes = pipeline.download_items(items, tmp_path, download_fn=fake_download)
    assert len(outcomes) == 1
    assert outcomes[0].already_existed is True
    assert calls == []


def test_download_items_downloads_missing_file_and_checks_byte_count(tmp_path):
    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = dest_dir / filename
        path.write_bytes(b"12345")
        return path

    items = [
        pipeline.DownloadItem(
            url="https://x/new.bin", filename="new.bin", declared_size=5
        )
    ]
    outcomes = pipeline.download_items(items, tmp_path, download_fn=fake_download)
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.already_existed is False
    assert outcome.byte_count_ok is True
    assert outcome.succeeded is True


def test_download_items_detects_byte_count_mismatch(tmp_path):
    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = dest_dir / filename
        path.write_bytes(b"12345")  # 5 bytes, but declared_size below says 999
        return path

    items = [
        pipeline.DownloadItem(
            url="https://x/new.bin", filename="new.bin", declared_size=999
        )
    ]
    outcomes = pipeline.download_items(items, tmp_path, download_fn=fake_download)
    outcome = outcomes[0]
    assert outcome.byte_count_ok is False
    assert outcome.succeeded is False


def test_download_items_checks_md5_when_expected_hash_given(tmp_path):
    import hashlib

    content = b"hello world"
    correct_md5 = hashlib.md5(content).hexdigest()

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = dest_dir / filename
        path.write_bytes(content)
        return path

    good_item = pipeline.DownloadItem(
        url="https://x/a", filename="a.bin", expected_md5=correct_md5
    )
    outcomes = pipeline.download_items([good_item], tmp_path, download_fn=fake_download)
    assert outcomes[0].md5_ok is True
    assert outcomes[0].succeeded is True


def test_download_items_detects_md5_mismatch(tmp_path):
    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = dest_dir / filename
        path.write_bytes(b"hello world")
        return path

    bad_item = pipeline.DownloadItem(
        url="https://x/a", filename="a.bin", expected_md5="0" * 32
    )
    outcomes = pipeline.download_items([bad_item], tmp_path, download_fn=fake_download)
    assert outcomes[0].md5_ok is False
    assert outcomes[0].succeeded is False


def test_download_items_checks_generic_hash_when_expected_hash_given(tmp_path):
    """The gateways.stac counterpart to the expected_md5 tests above -
    a STAC item's checksum is sha256, not md5, so this goes through
    expected_hash/hash_ok instead of expected_md5/md5_ok."""
    import hashlib

    content = b"hello world"
    correct_sha256 = hashlib.sha256(content).hexdigest()

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = dest_dir / filename
        path.write_bytes(content)
        return path

    good_item = pipeline.DownloadItem(
        url="https://x/a", filename="a.bin", expected_hash=("sha256", correct_sha256)
    )
    outcomes = pipeline.download_items([good_item], tmp_path, download_fn=fake_download)
    assert outcomes[0].hash_ok is True
    assert outcomes[0].md5_ok is None  # the two checks are independent
    assert outcomes[0].succeeded is True


def test_download_items_detects_generic_hash_mismatch(tmp_path):
    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = dest_dir / filename
        path.write_bytes(b"hello world")
        return path

    bad_item = pipeline.DownloadItem(
        url="https://x/a", filename="a.bin", expected_hash=("sha256", "0" * 64)
    )
    outcomes = pipeline.download_items([bad_item], tmp_path, download_fn=fake_download)
    assert outcomes[0].hash_ok is False
    assert outcomes[0].succeeded is False


def test_download_items_records_error_without_raising(tmp_path):
    def failing_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        raise ConnectionError("simulated network failure")

    items = [pipeline.DownloadItem(url="https://x/a", filename="a.bin")]
    outcomes = pipeline.download_items(items, tmp_path, download_fn=failing_download)
    assert outcomes[0].error == "simulated network failure"
    assert outcomes[0].succeeded is False


def test_delete_failed_outcome_removes_the_file(tmp_path):
    bad_file = tmp_path / "bad.bin"
    bad_file.write_bytes(b"corrupt")
    outcome = pipeline.DownloadOutcome(
        item=pipeline.DownloadItem(url="https://x/bad.bin", filename="bad.bin"),
        path=bad_file,
        already_existed=False,
        byte_count_ok=False,
    )
    pipeline.delete_failed_outcome(outcome)
    assert not bad_file.exists()


# --------------------------------------------------------------------------- archive vs. simple file


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("data.zip", True),
        ("data.7z", True),
        ("data.tar.gz", True),
        ("plain.tif", False),
    ],
)
def test_is_archive_file(filename, expected):
    assert pipeline.is_archive_file(filename) == expected


def test_list_and_classify_archive_real_zip_with_numbered_folders(tmp_path):
    zip_path = tmp_path / "RGEALTI_2-0_5M_ASC_LAMB93-IGN69_D073_2020-10-15.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("1_DONNEES_LIVRAISON_x/tile_a.asc", "data")
        zf.writestr("2_METADONNEES_LIVRAISON_x/readme.txt", "meta")
        zf.writestr("3_SUPPLEMENTS_LIVRAISON_x/dalles.shp", "shapefile")

    classification = pipeline.list_and_classify_archive(zip_path)
    assert classification["DONNEES"] == ["1_DONNEES_LIVRAISON_x/tile_a.asc"]
    assert classification["METADONNEES"] == ["2_METADONNEES_LIVRAISON_x/readme.txt"]
    assert classification["SUPPLEMENTS"] == ["3_SUPPLEMENTS_LIVRAISON_x/dalles.shp"]


def test_extract_selected_members_from_real_zip_filters_non_data_artifacts(tmp_path):
    zip_path = tmp_path / "sample.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("data/tile_a.tif", "real data")
        zf.writestr("data/._tile_a.tif", "appledouble junk")

    dest = tmp_path / "extracted"
    pipeline.extract_selected_members(
        zip_path, dest, ["data/tile_a.tif", "data/._tile_a.tif"]
    )
    assert (dest / "data" / "tile_a.tif").exists()
    assert not (dest / "data" / "._tile_a.tif").exists()


# --------------------------------------------------------------------------- simple file placement


def test_place_simple_file_leaves_in_place_when_no_destination(tmp_path):
    central_repo = tmp_path / "repo"
    central_repo.mkdir()
    source = central_repo / "file.tif"
    source.write_text("data")

    targets = DownloadTargets(central_repo=central_repo)
    result = pipeline.place_simple_file(source, targets)
    assert result == source


def test_place_simple_file_copies_to_explicit_destination(tmp_path):
    central_repo = tmp_path / "repo"
    central_repo.mkdir()
    source = central_repo / "file.tif"
    source.write_text("data")

    destination = tmp_path / "dest"
    targets = DownloadTargets(central_repo=central_repo, destination=destination)
    result = pipeline.place_simple_file(source, targets)
    assert result == destination / "file.tif"
    assert result.read_text() == "data"
    assert source.exists()  # original is not moved, only copied


def test_place_simple_file_does_not_copy_when_destination_equals_central_repo(tmp_path):
    central_repo = tmp_path / "repo"
    central_repo.mkdir()
    source = central_repo / "file.tif"
    source.write_text("data")

    targets = DownloadTargets(central_repo=central_repo, destination=central_repo)
    result = pipeline.place_simple_file(source, targets)
    assert result == source


def test_place_simple_file_preserves_subdirectory_structure_when_copying(tmp_path):
    """Regression test for a real reported bug adjacent to the mosaic-
    location fix ("the vrt file is always named the same, its location
    doesn't follow any subdirectory logic"): a file already placed at
    central_repo/{source}/{category}/{layer}/filename.tif must keep
    that same nested structure when copied to a separate Destination
    folder, not get flattened to destination/filename.tif - a
    flattened copy here would have silently undone the mosaic fix in
    exactly this case, since the mosaic is placed alongside wherever
    its own tiles actually ended up."""
    central_repo = tmp_path / "repo"
    nested = central_repo / "IGN (France)" / "raster" / "SOME_LAYER"
    nested.mkdir(parents=True)
    source = nested / "file.tif"
    source.write_text("data")

    destination = tmp_path / "dest"
    targets = DownloadTargets(central_repo=central_repo, destination=destination)
    result = pipeline.place_simple_file(source, targets)

    assert result == destination / "IGN (France)" / "raster" / "SOME_LAYER" / "file.tif"
    assert result.read_text() == "data"
    assert source.exists()  # original is not moved, only copied


def test_place_simple_file_falls_back_to_flat_copy_when_source_is_not_under_central_repo(
    tmp_path,
):
    """Defensive: source_path isn't guaranteed to be beneath
    central_repo in every conceivable call (download_items always
    places there first in practice, but this function doesn't itself
    enforce that) - falls back to a flat copy by filename rather than
    raising on the relative_to() mismatch."""
    central_repo = tmp_path / "repo"
    central_repo.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    source = elsewhere / "file.tif"
    source.write_text("data")

    destination = tmp_path / "dest"
    targets = DownloadTargets(central_repo=central_repo, destination=destination)
    result = pipeline.place_simple_file(source, targets)
    assert result == destination / "file.tif"
    assert result.read_text() == "data"


# --------------------------------------------------------------------------- download directory structure


def test_guess_data_category_recognizes_raster():
    assert pipeline.guess_data_category("tile.tif") == "raster"
    assert pipeline.guess_data_category("TILE.TIF") == "raster"  # case-insensitive


def test_guess_data_category_recognizes_vector():
    assert pipeline.guess_data_category("roads.shp") == "vector"
    assert pipeline.guess_data_category("data.gpkg") == "vector"


def test_guess_data_category_recognizes_point_cloud():
    assert pipeline.guess_data_category("scan.laz") == "point_cloud"
    assert pipeline.guess_data_category("scan.copc.laz") == "point_cloud"


def test_guess_data_category_falls_back_to_other():
    assert pipeline.guess_data_category("readme.txt") == "other"
    assert pipeline.guess_data_category("no_extension_at_all") == "other"


def test_sanitize_path_component_replaces_unsafe_characters():
    assert pipeline.sanitize_path_component("a/b\\c:d*e?f") == "a_b_c_d_e_f"


def test_sanitize_path_component_leaves_readable_names_alone():
    """A source display name like "IGN (France)" must stay readable as a
    folder name, not get mangled just because it has spaces/parens."""
    assert pipeline.sanitize_path_component("IGN (France)") == "IGN (France)"


def test_sanitize_path_component_falls_back_to_unnamed_for_empty_input():
    assert pipeline.sanitize_path_component("   ") == "unnamed"
    assert pipeline.sanitize_path_component("") == "unnamed"


def test_build_download_subdirectory_combines_source_category_and_layer():
    result = pipeline.build_download_subdirectory(
        "IGN (France)", "dalle_137.tif", layer_name="IGNF_MNS-LIDAR-HD:dalle"
    )
    assert result == "IGN (France)/raster/IGNF_MNS-LIDAR-HD_dalle"


def test_build_download_subdirectory_omits_layer_when_not_given():
    result = pipeline.build_download_subdirectory("swisstopo", "data.shp")
    assert result == "swisstopo/vector"


def test_download_items_places_files_using_item_subdirectory(tmp_path):
    """The actual reported gap: without subdirectory, every download
    landed flatly in central_repo regardless of source or layer.

    Deliberately does NOT create dest_dir itself in the fake - real
    production always worked here because gateways.atom.download (the
    real download_fn) happens to call os.makedirs internally, but a
    widget-level test's own simpler fake, and any other future
    download_fn, has no obligation to. This masked a real gap for a
    while: download_items itself must guarantee the destination
    directory exists before calling ANY download_fn, not rely on each
    one remembering to."""

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = Path(dest_dir) / filename
        path.write_bytes(b"data")
        return path

    items = [
        pipeline.DownloadItem(
            url="https://x/a.tif",
            filename="a.tif",
            subdirectory="IGN (France)/raster/some_layer",
        )
    ]
    outcomes = pipeline.download_items(items, tmp_path, download_fn=fake_download)

    expected_path = tmp_path / "IGN (France)" / "raster" / "some_layer" / "a.tif"
    assert outcomes[0].path == expected_path
    assert expected_path.exists()


def test_download_items_without_subdirectory_stays_flat(tmp_path):
    """Backward compatibility: an item with no subdirectory (the
    default, and every caller predating this feature) must behave
    exactly as before - flat, directly in central_repo."""

    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = Path(dest_dir) / filename
        path.write_bytes(b"data")
        return path

    items = [pipeline.DownloadItem(url="https://x/a.tif", filename="a.tif")]
    outcomes = pipeline.download_items(items, tmp_path, download_fn=fake_download)
    assert outcomes[0].path == tmp_path / "a.tif"


def test_download_items_already_existing_check_honors_subdirectory(tmp_path):
    """The 'already downloaded' check (both check_already_downloaded and
    download_items' own skip-if-present logic) must look in the same
    nested location a subdirectory-bearing item actually downloads to -
    otherwise a file that's already there would never be recognized as
    such, and get re-downloaded every time."""
    nested = tmp_path / "IGN (France)" / "raster" / "some_layer"
    nested.mkdir(parents=True)
    (nested / "a.tif").write_bytes(b"already here")

    item = pipeline.DownloadItem(
        url="https://x/a.tif",
        filename="a.tif",
        subdirectory="IGN (France)/raster/some_layer",
    )

    presence = pipeline.check_already_downloaded([item], tmp_path)
    assert presence["a.tif"] is True

    def download_fn_should_not_be_called(*a, **k):
        raise AssertionError("must not re-download an already-present file")

    outcomes = pipeline.download_items(
        [item], tmp_path, download_fn=download_fn_should_not_be_called
    )
    assert outcomes[0].already_existed is True
    assert outcomes[0].path == nested / "a.tif"
