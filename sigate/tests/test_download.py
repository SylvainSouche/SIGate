"""
Tests for sigate.download - the download pipeline's supporting modules.
Where practical, these tests exercise real file formats (zip, tar, and,
where a 7-Zip executable is available, real 7z archives including a
genuinely multi-part split archive) rather than only testing parsing
logic against captured sample output.
"""

import tarfile
import zipfile
from pathlib import Path

import pytest

from sigate.download import (
    archive,
    extraction_structure,
    integrity,
    mosaic,
    pipeline,
    targets,
)

# --------------------------------------------------------------------------- targets


def test_download_targets_effective_destination_falls_back_to_central_repo(tmp_path):
    dt = targets.DownloadTargets(central_repo=tmp_path)
    assert dt.effective_destination == tmp_path


def test_download_targets_effective_destination_uses_explicit_destination(tmp_path):
    dest = tmp_path / "dest"
    dt = targets.DownloadTargets(central_repo=tmp_path, destination=dest)
    assert dt.effective_destination == dest


def test_bookmarks_round_trip(tmp_path):
    targets.save_bookmarks(
        tmp_path, [targets.TargetBookmark(name="Archive", path="/data/archive")]
    )
    loaded = targets.load_bookmarks(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].name == "Archive"
    assert loaded[0].path == "/data/archive"


def test_bookmarks_empty_when_no_file_exists(tmp_path):
    assert targets.load_bookmarks(tmp_path / "missing") == []


def test_add_or_replace_bookmark_replaces_same_name(tmp_path):
    targets.add_or_replace_bookmark(
        tmp_path, targets.TargetBookmark(name="A", path="/one")
    )
    updated = targets.add_or_replace_bookmark(
        tmp_path, targets.TargetBookmark(name="A", path="/two")
    )
    assert len(updated) == 1
    assert updated[0].path == "/two"


def test_required_bytes_with_margin_uses_larger_of_percentage_or_minimum():
    # Small requirement: the flat minimum dominates.
    assert (
        targets.required_bytes_with_margin(1000)
        == 1000 + targets.FREE_SPACE_MARGIN_MINIMUM_BYTES
    )
    # Large requirement: the percentage dominates.
    large = 10 * 1024 * 1024 * 1024  # 10 GB
    expected_margin = int(large * targets.FREE_SPACE_MARGIN_FRACTION)
    assert expected_margin > targets.FREE_SPACE_MARGIN_MINIMUM_BYTES
    assert targets.required_bytes_with_margin(large) == large + expected_margin


def test_has_enough_free_space_true_for_small_requirement(tmp_path):
    assert targets.has_enough_free_space(tmp_path, 1024) is True


def test_has_enough_free_space_false_for_absurd_requirement(tmp_path):
    absurd = 10**18  # an exabyte; no test machine has this much free space
    assert targets.has_enough_free_space(tmp_path, absurd) is False


# --------------------------------------------------------------------------- archive: format detection


@pytest.mark.parametrize(
    "filename,expected_kind",
    [
        ("data.zip", archive.ARCHIVE_KIND_ZIP),
        ("data.tar", archive.ARCHIVE_KIND_TAR),
        ("data.tar.gz", archive.ARCHIVE_KIND_TAR),
        ("data.tgz", archive.ARCHIVE_KIND_TAR),
        ("data.7z", archive.ARCHIVE_KIND_7Z),
        ("data.7z.001", archive.ARCHIVE_KIND_7Z),
        ("data.txt", None),
    ],
)
def test_detect_archive_kind(filename, expected_kind):
    assert archive.detect_archive_kind(filename) == expected_kind


def test_is_non_data_artifact_detects_macos_sidecars_and_ds_store():
    assert archive.is_non_data_artifact("some/dir/._file.tif") is True
    assert archive.is_non_data_artifact("some/dir/.DS_Store") is True
    assert archive.is_non_data_artifact("some/dir/real_file.tif") is False


def test_archive_base_name_strips_a_split_7z_parts_two_suffixes():
    """Regression test for a real off-by-one caught while writing this:
    a split part's own base name still carries ".7z" (e.g.
    "RGEALTI_D073.7z.001" -> base group "RGEALTI_D073.7z"), which must
    also be stripped, not left attached."""
    assert archive.archive_base_name("RGEALTI_D073.7z.001") == "RGEALTI_D073"
    assert archive.archive_base_name("RGEALTI_D073.7z.010") == "RGEALTI_D073"


def test_archive_base_name_strips_a_plain_7z_extension():
    assert archive.archive_base_name("RGEALTI_D073.7z") == "RGEALTI_D073"


def test_archive_base_name_strips_a_zip_extension():
    assert archive.archive_base_name("photos.zip") == "photos"


def test_archive_base_name_strips_a_compound_tar_extension():
    """A compound suffix like .tar.gz must be stripped in full, not just
    the final ".gz" - "foo.tar.gz" must become "foo", not "foo.tar"."""
    assert archive.archive_base_name("dataset.tar.gz") == "dataset"
    assert archive.archive_base_name("dataset.tgz") == "dataset"


def test_archive_base_name_falls_back_to_plain_stem_for_unknown_extensions():
    assert archive.archive_base_name("readme.txt") == "readme"


def test_group_split_7z_parts_groups_and_sorts():
    filenames = ["archive.7z.002", "archive.7z.001", "other.txt", "archive.7z.010"]
    groups = archive.group_split_7z_parts(filenames)
    assert groups == {
        "archive.7z": ["archive.7z.001", "archive.7z.002", "archive.7z.010"]
    }


def test_find_7z_executable_finds_installed_binary():
    # This environment has p7zip-full installed for these tests.
    found = archive.find_7z_executable()
    assert found is not None


# --------------------------------------------------------------------------- archive: real zip/tar


def test_list_and_extract_real_zip(tmp_path):
    zip_path = tmp_path / "sample.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.txt", "hello")
        zf.writestr("sub/b.txt", "world")

    members = archive.list_archive(zip_path)
    names = {m.name for m in members}
    assert "a.txt" in names
    assert "sub/b.txt" in names

    dest = tmp_path / "extracted"
    archive.extract_archive(zip_path, dest)
    assert (dest / "a.txt").read_text() == "hello"
    assert (dest / "sub" / "b.txt").read_text() == "world"


def test_list_and_extract_real_tar(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "a.txt").write_text("hello")
    tar_path = tmp_path / "sample.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(src_dir / "a.txt", arcname="a.txt")

    members = archive.list_archive(tar_path)
    assert any(m.name == "a.txt" for m in members)

    dest = tmp_path / "extracted"
    archive.extract_archive(tar_path, dest)
    assert (dest / "a.txt").read_text() == "hello"


# --------------------------------------------------------------------------- archive: real 7z, including a genuine split archive


@pytest.fixture
def sevenzip_exe():
    found = archive.find_7z_executable()
    if not found:
        pytest.skip("no 7-Zip executable available in this environment")
    return found


def _make_7z_archive(tmp_path, sevenzip_exe, *file_specs, volume_size=None):
    """Builds a real 7z archive containing the given (relative_path,
    content_bytes) pairs, optionally split into volumes. Returns the
    directory the archive's part(s) were written into."""
    import subprocess

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    for rel_path, content in file_specs:
        full_path = src_dir / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)

    archive_dir = tmp_path / "archive_out"
    archive_dir.mkdir()
    archive_path = archive_dir / "sample.7z"
    cmd = [sevenzip_exe, "a"]
    if volume_size:
        cmd.append(f"-v{volume_size}")
    cmd += [str(archive_path), "."]
    subprocess.run(cmd, cwd=src_dir, check=True, capture_output=True)
    return archive_dir


def test_list_and_extract_real_7z(tmp_path, sevenzip_exe):
    archive_dir = _make_7z_archive(
        tmp_path, sevenzip_exe, ("file1.txt", b"hello"), ("sub/file2.txt", b"world")
    )
    archive_path = archive_dir / "sample.7z"

    members = archive.list_archive(archive_path, sevenzip_exe=sevenzip_exe)
    names = {m.name for m in members}
    assert "file1.txt" in names
    assert "sub/file2.txt" in names


def test_list_and_extract_real_7z_logs_the_actual_commands_issued(
    tmp_path, sevenzip_exe
):
    """Requested directly ("can you output all issued commands in the
    console?") - confirmed here against the real 7z binary, not a fake,
    that the exact command list_archive/extract_archive actually runs is
    what gets passed to the log callback."""
    archive_dir = _make_7z_archive(tmp_path, sevenzip_exe, ("file1.txt", b"hello"))
    archive_path = archive_dir / "sample.7z"
    logged = []

    members = archive.list_archive(
        archive_path, sevenzip_exe=sevenzip_exe, log=logged.append
    )
    assert len(logged) == 1
    assert logged[0].startswith("+ ")
    assert sevenzip_exe in logged[0]
    assert "-slt" in logged[0]
    assert str(archive.first_7z_part(archive_path)) in logged[0]

    logged.clear()
    dest_dir = tmp_path / "extracted"
    archive.extract_archive(
        archive_path,
        dest_dir,
        members=[m.name for m in members],
        sevenzip_exe=sevenzip_exe,
        log=logged.append,
    )
    assert len(logged) == 1
    assert logged[0].startswith("+ ")
    assert sevenzip_exe in logged[0]
    assert str(dest_dir) in logged[0]


def test_archive_functions_stay_silent_when_no_log_callback_given(
    tmp_path, sevenzip_exe
):
    """The default (log=None) must be a genuine no-op, not an error -
    every existing caller that predates this feature didn't pass one."""
    archive_dir = _make_7z_archive(tmp_path, sevenzip_exe, ("file1.txt", b"hello"))
    archive_path = archive_dir / "sample.7z"
    # no log= given at all - must not raise
    archive.list_archive(archive_path, sevenzip_exe=sevenzip_exe)

    dest = tmp_path / "extracted"
    archive.extract_archive(archive_path, dest, sevenzip_exe=sevenzip_exe)
    assert (dest / "file1.txt").read_bytes() == b"hello"


def test_list_and_extract_real_split_7z_archive(tmp_path, sevenzip_exe):
    import hashlib
    import os

    big_content = os.urandom(10 * 1024)
    archive_dir = _make_7z_archive(
        tmp_path,
        sevenzip_exe,
        ("bigfile.bin", big_content),
        ("file1.txt", b"hello"),
        volume_size="3k",
    )
    part_paths = sorted(archive_dir.glob("sample.7z.*"))
    assert len(part_paths) >= 2, (
        "test archive was not actually split into multiple parts"
    )

    # Listing and extraction should work correctly starting from ANY part,
    # not only the first one - 7z locates the sibling parts itself.
    for start_part in (part_paths[0], part_paths[-1]):
        members = archive.list_archive(start_part, sevenzip_exe=sevenzip_exe)
        names = {m.name for m in members}
        assert "bigfile.bin" in names
        assert "file1.txt" in names

        dest = tmp_path / f"extracted_from_{start_part.name}"
        archive.extract_archive(start_part, dest, sevenzip_exe=sevenzip_exe)
        assert (
            hashlib.md5((dest / "bigfile.bin").read_bytes()).digest()
            == hashlib.md5(big_content).digest()
        )
        assert (dest / "file1.txt").read_bytes() == b"hello"


# --------------------------------------------------------------------------- integrity


def test_verify_byte_count_matches(tmp_path):
    f = tmp_path / "file.bin"
    f.write_bytes(b"12345")
    assert integrity.verify_byte_count(f, 5) is True
    assert integrity.verify_byte_count(f, 6) is False


def test_verify_byte_count_none_expected_always_passes(tmp_path):
    f = tmp_path / "file.bin"
    f.write_bytes(b"12345")
    assert integrity.verify_byte_count(f, None) is True


def test_compute_and_verify_md5(tmp_path):
    f = tmp_path / "file.bin"
    f.write_bytes(b"hello world")
    digest = integrity.compute_md5(f)
    assert integrity.verify_md5(f, digest) is True
    assert integrity.verify_md5(f, "0" * 32) is False


def test_parse_md5_checksum_file_bare_hash():
    assert (
        integrity.parse_md5_checksum_file("5eb63bbbe01eeed093cb22bb8f5acdc3\n")
        == "5eb63bbbe01eeed093cb22bb8f5acdc3"
    )


def test_parse_md5_checksum_file_md5sum_style():
    content = "5eb63bbbe01eeed093cb22bb8f5acdc3  hello.txt\n"
    assert (
        integrity.parse_md5_checksum_file(content) == "5eb63bbbe01eeed093cb22bb8f5acdc3"
    )


def test_parse_md5_checksum_file_no_hash_present():
    assert integrity.parse_md5_checksum_file("no hash here") is None


# --------------------------------------------------------------------------- extraction_structure


def test_extract_product_key_strips_version_and_date_suffix():
    assert (
        extraction_structure.extract_product_key(
            "RGEALTI_2-0_5M_ASC_LAMB93-IGN69_D073_2020-10-15"
        )
        == "RGEALTI"
    )
    assert (
        extraction_structure.extract_product_key(
            "ORTHOHR_1-0_IRC-0M15_JP2-E080_LAMB93_D075_2021-01-01"
        )
        == "ORTHOHR"
    )


def test_classify_by_numbered_folders_at_archive_root():
    """A delivery with the numbered role folders directly at the archive root."""
    paths = [
        "1_DONNEES_LIVRAISON_2022-07-00156/PRODUCT/tile_a.jp2",
        "1_DONNEES_LIVRAISON_2022-07-00156/PRODUCT/tile_a.tab",
        "2_METADONNEES_LIVRAISON_2022-07-00156/PRODUCT/readme.txt",
        "3_SUPPLEMENTS_LIVRAISON_2022-07-00156/PRODUCT/readme.txt",
        "LISEZ-MOI.pdf",
    ]
    result = extraction_structure.classify_archive_contents(paths)
    assert result[extraction_structure.ROLE_DATA] == [
        "1_DONNEES_LIVRAISON_2022-07-00156/PRODUCT/tile_a.jp2",
        "1_DONNEES_LIVRAISON_2022-07-00156/PRODUCT/tile_a.tab",
    ]
    assert result[extraction_structure.ROLE_METADATA] == [
        "2_METADONNEES_LIVRAISON_2022-07-00156/PRODUCT/readme.txt"
    ]
    assert result[extraction_structure.ROLE_SUPPLEMENTS] == [
        "3_SUPPLEMENTS_LIVRAISON_2022-07-00156/PRODUCT/readme.txt"
    ]
    assert result[extraction_structure.ROLE_UNCLASSIFIED] == ["LISEZ-MOI.pdf"]


def test_classify_by_numbered_folders_nested_deeper_under_product_subfolder():
    """A delivery where the numbered role folders sit one level deeper,
    under a product-name subfolder rather than at the archive root - the
    scan must find the pattern regardless of depth."""
    paths = [
        "ARCHIVE_NAME/PRODUCT/1_DONNEES_LIVRAISON_2021-10-00009/PRODUCT/tile_a.asc",
        "ARCHIVE_NAME/PRODUCT/1_DONNEES_LIVRAISON_2021-10-00009/PRODUCT/tile_a.md5",
        "ARCHIVE_NAME/PRODUCT/2_METADONNEES_LIVRAISON_2021-10-00009/PRODUCT/metadata.xml",
        "ARCHIVE_NAME/PRODUCT/3_SUPPLEMENTS_LIVRAISON_2021-10-00009/PRODUCT/tile_index.shp",
        "ARCHIVE_NAME/PRODUCT/LISEZ-MOI.pdf",
    ]
    result = extraction_structure.classify_archive_contents(paths)
    assert len(result[extraction_structure.ROLE_DATA]) == 2
    assert len(result[extraction_structure.ROLE_METADATA]) == 1
    assert len(result[extraction_structure.ROLE_SUPPLEMENTS]) == 1
    assert len(result[extraction_structure.ROLE_UNCLASSIFIED]) == 1


def test_classify_filters_out_non_data_artifacts_regardless_of_role():
    paths = [
        "1_DONNEES_LIVRAISON_x/PRODUCT/tile_a.jp2",
        "1_DONNEES_LIVRAISON_x/PRODUCT/._tile_a.jp2",
        "1_DONNEES_LIVRAISON_x/PRODUCT/.DS_Store",
    ]
    result = extraction_structure.classify_archive_contents(paths)
    assert result[extraction_structure.ROLE_DATA] == [
        "1_DONNEES_LIVRAISON_x/PRODUCT/tile_a.jp2"
    ]


def test_classify_falls_back_to_heuristic_when_no_numbered_folders_present():
    paths = [
        "DONNEES/tile_a.tif",
        "DOCUMENTATION/readme.txt",
        "unrelated/other.txt",
    ]
    result = extraction_structure.classify_archive_contents(paths)
    assert result[extraction_structure.ROLE_DATA] == ["DONNEES/tile_a.tif"]
    assert result[extraction_structure.ROLE_METADATA] == ["DOCUMENTATION/readme.txt"]
    assert result[extraction_structure.ROLE_UNCLASSIFIED] == ["unrelated/other.txt"]


def test_classify_uses_saved_product_override_when_no_numbered_folders_and_key_known(
    tmp_path,
):
    # First archive of this product: the heuristic alone won't recognize
    # "CUSTOM_DATA_FOLDER" (no DATA/DONNEES keyword match), so everything
    # lands as unclassified - simulate a user manually correcting this and
    # saving the correction as an override.
    corrected = {
        extraction_structure.ROLE_DATA: ["CUSTOM_DATA_FOLDER/tile_a.tif"],
        extraction_structure.ROLE_METADATA: [],
        extraction_structure.ROLE_SUPPLEMENTS: [],
        extraction_structure.ROLE_UNCLASSIFIED: ["readme.txt"],
    }
    extraction_structure.save_product_override(tmp_path, "MYPRODUCT", corrected)

    # A later archive of the same product, with different specific
    # filenames but the same top-level folder name, should now classify
    # correctly using the saved override.
    second_paths = ["CUSTOM_DATA_FOLDER/tile_b.tif", "other_readme.txt"]
    second_result = extraction_structure.classify_archive_contents(
        second_paths, product_key="MYPRODUCT", data_dir=tmp_path
    )
    assert second_result[extraction_structure.ROLE_DATA] == [
        "CUSTOM_DATA_FOLDER/tile_b.tif"
    ]


# --------------------------------------------------------------------------- mosaic


def test_looks_like_tileable_raster_set_true_for_multiple_same_extension(tmp_path):
    paths = [tmp_path / "a.tif", tmp_path / "b.tif", tmp_path / "c.tif"]
    assert mosaic.looks_like_tileable_raster_set(paths) is True


def test_looks_like_tileable_raster_set_false_for_single_file(tmp_path):
    assert mosaic.looks_like_tileable_raster_set([tmp_path / "a.tif"]) is False


def test_looks_like_tileable_raster_set_false_for_mixed_extensions(tmp_path):
    paths = [tmp_path / "a.tif", tmp_path / "b.jp2"]
    assert mosaic.looks_like_tileable_raster_set(paths) is False


def test_raster_tile_paths_filters_to_only_known_raster_extensions(tmp_path):
    """Needed so a mixed batch (data tiles alongside metadata/supplement
    files, e.g. extracted "keep everything" from one archive) can build
    a mosaic from only the actual raster tiles - passing a non-raster
    file straight to gdalbuildvrt would either fail or produce a broken
    mosaic."""
    paths = [
        tmp_path / "a.tif",
        tmp_path / "b.tif",
        tmp_path / "metadata.xml",
        tmp_path / "index.shp",
    ]
    assert mosaic.raster_tile_paths(paths) == [tmp_path / "a.tif", tmp_path / "b.tif"]


def test_raster_tile_paths_empty_when_nothing_matches(tmp_path):
    assert mosaic.raster_tile_paths([tmp_path / "readme.txt"]) == []


def test_mosaic_filename_is_predictable_and_lowercase():
    assert mosaic.mosaic_filename("RGEALTI") == "sigate_mosaic_rgealti.vrt"


def test_find_existing_mosaic_none_when_absent(tmp_path):
    assert mosaic.find_existing_mosaic(tmp_path, "RGEALTI") is None


def test_find_existing_mosaic_found_when_present(tmp_path):
    (tmp_path / "sigate_mosaic_rgealti.vrt").write_text("<VRTDataset/>")
    found = mosaic.find_existing_mosaic(tmp_path, "RGEALTI")
    assert found is not None
    assert found.name == "sigate_mosaic_rgealti.vrt"


def test_overview_path_matches_the_real_documented_artifact_shape(tmp_path):
    """docs/spec.md documents a real, confirmed user artifact:
    "dem73.vrt" + "dem73.vrt.ovr" - GDAL's own external-overview sidecar
    convention. This is the shape find_existing_mosaic/has_overviews
    need to recognize."""
    vrt_path = tmp_path / "dem73.vrt"
    assert mosaic.overview_path(vrt_path) == tmp_path / "dem73.vrt.ovr"


def test_has_overviews_false_when_no_sidecar_present(tmp_path):
    vrt_path = tmp_path / "sigate_mosaic_rgealti.vrt"
    vrt_path.write_text("<VRTDataset/>")
    assert mosaic.has_overviews(vrt_path) is False


def test_has_overviews_true_when_sidecar_present(tmp_path):
    vrt_path = tmp_path / "sigate_mosaic_rgealti.vrt"
    vrt_path.write_text("<VRTDataset/>")
    (tmp_path / "sigate_mosaic_rgealti.vrt.ovr").write_bytes(b"fake overview data")
    assert mosaic.has_overviews(vrt_path) is True


def test_build_overviews_invokes_gdaladdo_with_the_right_arguments(
    tmp_path, monkeypatch
):
    """gdaladdo itself isn't available in this environment to actually
    run (same real limitation build_vrt_mosaic has always had - no
    existing test exercises a real gdalbuildvrt call either), so this
    confirms the command it would run is correct, via a captured fake
    subprocess.run rather than a real binary."""
    captured = {}

    def fake_run(cmd, check=True, capture_output=True):
        captured["cmd"] = cmd

        class Result:
            pass

        return Result()

    monkeypatch.setattr(mosaic.subprocess, "run", fake_run)
    vrt_path = tmp_path / "sigate_mosaic_rgealti.vrt"

    result = mosaic.build_overviews(vrt_path, levels="2 4 8", resampling="gauss")

    assert captured["cmd"] == [
        "gdaladdo",
        "-ro",
        "-r",
        "gauss",
        str(vrt_path),
        "2",
        "4",
        "8",
    ]
    assert result == mosaic.overview_path(vrt_path)


def test_build_overviews_does_not_hardcode_lossy_compression(tmp_path, monkeypatch):
    """A mosaic built through this module could be elevation/DEM data
    (the real documented example is exactly that) - JPEG or any other
    lossy compression has no business being silently applied to
    elevation values, unlike the separate, imagery-specific standalone
    script this was adapted from."""
    captured = {}

    def fake_run(cmd, check=True, capture_output=True):
        captured["cmd"] = cmd

        class Result:
            pass

        return Result()

    monkeypatch.setattr(mosaic.subprocess, "run", fake_run)
    mosaic.build_overviews(tmp_path / "x.vrt")

    assert "JPEG" not in captured["cmd"]
    assert not any("COMPRESS" in str(arg) for arg in captured["cmd"])


def test_build_vrt_mosaic_logs_the_command_it_issues(tmp_path, monkeypatch):
    monkeypatch.setattr(mosaic.subprocess, "run", lambda *a, **k: None)
    logged = []
    tile_paths = [tmp_path / "a.tif", tmp_path / "b.tif"]

    mosaic.build_vrt_mosaic(tile_paths, tmp_path, "RGEALTI", log=logged.append)

    assert len(logged) == 1
    assert logged[0].startswith("+ gdalbuildvrt ")
    assert str(tile_paths[0]) in logged[0]
    assert str(tile_paths[1]) in logged[0]


def test_build_overviews_logs_the_command_it_issues(tmp_path, monkeypatch):
    monkeypatch.setattr(mosaic.subprocess, "run", lambda *a, **k: None)
    logged = []
    vrt_path = tmp_path / "sigate_mosaic_rgealti.vrt"

    mosaic.build_overviews(vrt_path, log=logged.append)

    assert len(logged) == 1
    assert logged[0].startswith("+ gdaladdo ")
    assert str(vrt_path) in logged[0]


def test_mosaic_functions_stay_silent_when_no_log_callback_given(tmp_path, monkeypatch):
    """The default (log=None) must be a genuine no-op - every existing
    caller that predates this feature didn't pass one."""
    monkeypatch.setattr(mosaic.subprocess, "run", lambda *a, **k: None)
    mosaic.build_vrt_mosaic([tmp_path / "a.tif"], tmp_path, "RGEALTI")
    mosaic.build_overviews(tmp_path / "x.vrt")


# --------------------------------------------------------------------------- download_items progress/cancellation


def _fake_download_fn(contents_by_filename):
    """A download_fn stand-in that writes canned content and forwards
    calls to progress_callback/should_continue - the real contract every
    download_fn is now expected to support."""

    def fn(url, dest_dir, filename, progress_callback=None, should_continue=None):
        if should_continue is not None and not should_continue():
            from sigate.gateways.base import DownloadCancelled

            raise DownloadCancelled(f"{filename} cancelled")
        data = contents_by_filename[filename]
        if progress_callback is not None:
            progress_callback(len(data), len(data))
        path = Path(dest_dir) / filename
        path.write_bytes(data)
        return path

    return fn


def test_download_items_calls_on_file_start_for_every_file_in_order(tmp_path):
    items = [
        pipeline.DownloadItem(url="https://x/a", filename="a.txt"),
        pipeline.DownloadItem(url="https://x/b", filename="b.txt"),
    ]
    starts = []
    pipeline.download_items(
        items,
        tmp_path,
        download_fn=_fake_download_fn({"a.txt": b"A", "b.txt": b"B"}),
        on_file_start=lambda index, total, item: starts.append(
            (index, total, item.filename)
        ),
    )
    assert starts == [(0, 2, "a.txt"), (1, 2, "b.txt")]


def test_download_items_forwards_progress_from_the_transport(tmp_path):
    items = [pipeline.DownloadItem(url="https://x/a", filename="a.txt")]
    progress_calls = []
    pipeline.download_items(
        items,
        tmp_path,
        download_fn=_fake_download_fn({"a.txt": b"hello"}),
        on_file_progress=lambda read, total: progress_calls.append((read, total)),
    )
    assert progress_calls == [(5, 5)]


def test_download_items_does_not_call_on_file_start_for_already_present_files(
    tmp_path,
):
    (tmp_path / "a.txt").write_bytes(b"already here")
    items = [pipeline.DownloadItem(url="https://x/a", filename="a.txt")]
    starts = []
    outcomes = pipeline.download_items(
        items,
        tmp_path,
        download_fn=_fake_download_fn({}),
        on_file_start=lambda index, total, item: starts.append(item.filename),
    )
    # already-present files are still announced (the caller's progress UI
    # should show them passing through), just never reach download_fn
    assert starts == ["a.txt"]
    assert outcomes[0].already_existed is True


def test_download_items_stops_before_starting_further_files_once_cancelled(tmp_path):
    items = [
        pipeline.DownloadItem(url="https://x/a", filename="a.txt"),
        pipeline.DownloadItem(url="https://x/b", filename="b.txt"),
        pipeline.DownloadItem(url="https://x/c", filename="c.txt"),
    ]
    starts = []
    outcomes = pipeline.download_items(
        items,
        tmp_path,
        download_fn=_fake_download_fn({"a.txt": b"A", "b.txt": b"B", "c.txt": b"C"}),
        on_file_start=lambda index, total, item: starts.append(item.filename),
        should_continue=lambda: len(starts) < 2,  # cancel right after the 2nd starts
    )
    assert starts == ["a.txt", "b.txt"]  # c.txt never even started
    assert [o.item.filename for o in outcomes] == ["a.txt", "b.txt"]


def test_download_items_reports_a_cancelled_outcome_for_the_in_flight_file(tmp_path):
    """When should_continue flips to False mid-file (as opposed to
    between files), the file actually in flight gets its own outcome
    with error="Cancelled" - not silently dropped, and not reported as a
    generic failure either."""
    items = [pipeline.DownloadItem(url="https://x/a", filename="a.txt")]

    def always_cancelling_download_fn(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        from sigate.gateways.base import DownloadCancelled

        raise DownloadCancelled("cancelled mid-transfer")

    outcomes = pipeline.download_items(
        items, tmp_path, download_fn=always_cancelling_download_fn
    )
    assert len(outcomes) == 1
    assert outcomes[0].error == "Cancelled"
    assert outcomes[0].succeeded is False


def test_atom_download_deletes_partial_file_and_raises_on_cancellation(
    tmp_path, monkeypatch
):
    """Regression/contract test at the real transport level (not the
    fake used above): gateways.atom.download must actually delete the
    partial file and raise DownloadCancelled, not just stop silently
    with a half-written file left behind looking complete."""
    from sigate.gateways import atom
    from sigate.gateways.base import DownloadCancelled

    class FakeResponse:
        def __init__(self):
            self.headers = {}
            self._chunks = [b"x" * 100, b"y" * 100, b"z" * 100]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return self._chunks.pop(0) if self._chunks else b""

    monkeypatch.setattr(atom, "urlopen", lambda *a, **k: FakeResponse())

    call_count = {"n": 0}

    def should_continue():
        call_count["n"] += 1
        return call_count["n"] <= 1  # allow the first chunk, cancel before the 2nd

    with pytest.raises(DownloadCancelled):
        atom.download(
            "https://x/test.bin",
            str(tmp_path),
            "test.bin",
            should_continue=should_continue,
        )
    assert not (tmp_path / "test.bin").exists()
