"""
Tests for sigate.gateways.wmts_export - the GeoTIFF-materializing clip
path for WMTS sources, ported from a standalone script. GDAL >= 3.11
(gdal raster clip) isn't available in this environment (same real
limitation already noted for download.mosaic's gdalbuildvrt/gdaladdo),
so every subprocess call is exercised via an injected fake `run` rather
than a real binary - these tests confirm the exact commands/XML
manipulation this module performs, not GDAL's own behavior.
"""

import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

from sigate.gateways import wmts_export


def _fake_gdal_version_result():
    return SimpleNamespace(
        stdout=b"GDAL 3.11.0, released 2025/05/01\n", stderr=b"", returncode=0
    )


def _stub_version_check(inner_run):
    """Wraps a test's own fake `run` so clip_wmts_area's upfront
    check_gdal_version call gets a plausible successful response,
    letting each test below keep asserting only on the *clip* command
    it actually cares about, unaffected by the version check itself."""

    def wrapped(cmd, **kwargs):
        if cmd[:2] == ["gdal", "--version"]:
            return _fake_gdal_version_result()
        return inner_run(cmd, **kwargs)

    return wrapped


def test_wmts_source_string_without_style():
    s = wmts_export.wmts_source_string("https://data.geopf.fr/wmts", "SOME_LAYER", "PM")
    assert s == (
        "WMTS:https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0&"
        "REQUEST=GetCapabilities,layer=SOME_LAYER,tilematrixset=PM"
    )


def test_wmts_source_string_with_style():
    s = wmts_export.wmts_source_string(
        "https://data.geopf.fr/wmts", "SOME_LAYER", "PM", style="normal"
    )
    assert s.endswith(",style=normal")


def test_wmts_source_string_uses_ampersand_when_caps_url_already_has_query():
    s = wmts_export.wmts_source_string("https://x/wmts?foo=bar", "L", "PM")
    assert "?foo=bar&SERVICE=WMTS" in s


def test_wmts_def_cache_filename_is_stable_and_lowercase():
    name = wmts_export.wmts_def_cache_filename(
        "GEOGRAPHICALGRIDSYSTEMS.MAPS.SCAN25TOUR.L93", "LAMB93_2.5m_3_16"
    )
    assert name == name.lower()
    assert name.startswith("wmts_def_")
    assert name.endswith(".xml")


def test_wmts_def_cache_filename_distinguishes_different_layers():
    a = wmts_export.wmts_def_cache_filename("LAYER_A", "PM")
    b = wmts_export.wmts_def_cache_filename("LAYER_B", "PM")
    assert a != b


def test_wmts_def_cache_filename_distinguishes_style():
    a = wmts_export.wmts_def_cache_filename("LAYER", "PM", style="normal")
    b = wmts_export.wmts_def_cache_filename("LAYER", "PM", style=None)
    assert a != b


def test_estimate_export_size_computes_correct_pixel_dimensions():
    """Matches the manual worked example: a 10km x 20km area at 0.2m
    resolution is 50,000 x 100,000 pixels."""
    estimate = wmts_export.estimate_export_size(
        bbox=(0.0, 0.0, 10_000.0, 20_000.0), resolution=0.2, band_count=3
    )
    assert estimate.width_px == 50_000
    assert estimate.height_px == 100_000
    assert estimate.raw_bytes == 50_000 * 100_000 * 3


def test_estimate_export_size_defaults_to_three_bands():
    estimate = wmts_export.estimate_export_size(
        bbox=(0.0, 0.0, 100.0, 100.0), resolution=1.0
    )
    assert estimate.raw_bytes == 100 * 100 * 3


def test_estimate_export_size_handles_bbox_given_in_either_corner_order():
    """bbox is (xmin, ymin, xmax, ymax), but callers work from real-world
    rectangles where min/max ordering per axis can't always be assumed -
    must not produce a negative or wrong pixel count either way."""
    forward = wmts_export.estimate_export_size(
        bbox=(0.0, 0.0, 100.0, 200.0), resolution=1.0
    )
    reversed_axes = wmts_export.estimate_export_size(
        bbox=(100.0, 200.0, 0.0, 0.0), resolution=1.0
    )
    assert forward.width_px == reversed_axes.width_px == 100
    assert forward.height_px == reversed_axes.height_px == 200


def test_wmts_source_string_with_tilematrix():
    s = wmts_export.wmts_source_string(
        "https://data.geopf.fr/wmts", "SOME_LAYER", "PM", tilematrix="18"
    )
    assert s.endswith(",tilematrix=18")


def test_wmts_source_string_omits_tilematrix_when_not_given():
    s = wmts_export.wmts_source_string("https://data.geopf.fr/wmts", "L", "PM")
    assert "tilematrix=" not in s


def test_wmts_def_cache_filename_distinguishes_tilematrix():
    coarse = wmts_export.wmts_def_cache_filename("LAYER", "PM", tilematrix="10")
    fine = wmts_export.wmts_def_cache_filename("LAYER", "PM", tilematrix="18")
    no_choice = wmts_export.wmts_def_cache_filename("LAYER", "PM")
    assert len({coarse, fine, no_choice}) == 3


def test_ensure_wmts_def_passes_tilematrix_through_to_the_source_string(tmp_path):
    calls = []

    def fake_run(cmd, check=True, capture_output=True):
        calls.append(cmd)
        _fake_gdal_translate_wmts_def(cmd)

    def_path = str(tmp_path / "wmts_def_test.xml")
    wmts_export.ensure_wmts_def(
        def_path,
        "https://x/wmts",
        gdal_config=[],
        max_connections=2,
        layer="L",
        tilematrixset="PM",
        tilematrix="18",
        run=fake_run,
    )
    assert "tilematrix=18" in calls[0][1]  # the built WMTS: connection string


def test_build_gdal_http_headers_config():
    assert wmts_export.build_gdal_http_headers_config("apikey", "secret") == [
        "--config",
        "GDAL_HTTP_HEADERS",
        "apikey: secret",
    ]


def _fake_gdal_translate_wmts_def(cmd, check=True, capture_output=True):
    """Stands in for a real `gdal_translate ... -of WMTS` call - writes a
    minimal, valid WMTS XML definition to the output path, the way the
    real GDAL tool would."""
    out_path = cmd[-3]  # [..., source_string, out_path, "-of", "WMTS"]
    root = ET.Element("GDAL_WMTS")
    ET.ElementTree(root).write(out_path)


def test_ensure_wmts_def_generates_once_and_sets_max_connections(tmp_path):
    calls = []

    def fake_run(cmd, check=True, capture_output=True):
        calls.append(cmd)
        _fake_gdal_translate_wmts_def(cmd)

    def_path = str(tmp_path / "wmts_def_test.xml")
    result = wmts_export.ensure_wmts_def(
        def_path,
        "https://x/wmts",
        gdal_config=[],
        max_connections=4,
        layer="L",
        tilematrixset="PM",
        run=fake_run,
    )

    assert result == def_path
    assert len(calls) == 1
    assert calls[0][0] == "gdal_translate"

    tree = ET.parse(def_path)
    assert tree.getroot().find("MaxConnections").text == "4"


def test_ensure_wmts_def_reuses_existing_file_without_regenerating(tmp_path):
    def_path = tmp_path / "wmts_def_test.xml"
    root = ET.Element("GDAL_WMTS")
    ET.SubElement(root, "MaxConnections").text = "2"
    ET.ElementTree(root).write(def_path)

    def run_should_not_be_called(cmd, check=True, capture_output=True):
        raise AssertionError("must not regenerate an already-existing definition")

    result = wmts_export.ensure_wmts_def(
        str(def_path),
        "https://x/wmts",
        gdal_config=[],
        max_connections=2,  # same as what's already there
        layer="L",
        tilematrixset="PM",
        run=run_should_not_be_called,
    )
    assert result == str(def_path)


def test_ensure_wmts_def_updates_max_connections_on_an_existing_file(tmp_path):
    """The cache file itself isn't regenerated, but its MaxConnections
    value must still track what's actually being asked for now, not
    stay frozen at whatever it was the first time."""
    def_path = tmp_path / "wmts_def_test.xml"
    root = ET.Element("GDAL_WMTS")
    ET.SubElement(root, "MaxConnections").text = "2"
    ET.ElementTree(root).write(def_path)

    def run_should_not_be_called(cmd, check=True, capture_output=True):
        raise AssertionError("must not regenerate an already-existing definition")

    wmts_export.ensure_wmts_def(
        str(def_path),
        "https://x/wmts",
        gdal_config=[],
        max_connections=8,  # different from what's already there
        layer="L",
        tilematrixset="PM",
        run=run_should_not_be_called,
    )

    tree = ET.parse(def_path)
    assert tree.getroot().find("MaxConnections").text == "8"


def test_ensure_wmts_def_logs_the_command_when_generating(tmp_path):
    def_path = str(tmp_path / "wmts_def_test.xml")
    logged = []

    wmts_export.ensure_wmts_def(
        def_path,
        "https://x/wmts",
        gdal_config=[],
        max_connections=2,
        layer="L",
        tilematrixset="PM",
        run=_fake_gdal_translate_wmts_def,
        log=logged.append,
    )
    assert len(logged) == 1
    assert logged[0].startswith("+ gdal_translate ")


def test_clip_wmts_area_uses_two_step_pipeline_for_jpeg(tmp_path):
    calls = []

    def fake_run(cmd, check=True, capture_output=True):
        calls.append(cmd)
        if cmd[0] == "gdal":
            # the "clip" step must produce the scratch file the
            # translate step expects to read from
            scratch_path = cmd[-2]
            open(scratch_path, "wb").close()

    out_path = str(tmp_path / "out.tif")
    result = wmts_export.clip_wmts_area(
        "wmts_def.xml",
        bbox=(1.0, 2.0, 3.0, 4.0),
        out_path=out_path,
        tmp_dir=str(tmp_path),
        run=_stub_version_check(fake_run),
    )

    assert result == out_path
    assert len(calls) == 2
    clip_cmd, translate_cmd = calls
    assert clip_cmd[:3] == ["gdal", "raster", "clip"]
    assert "--bbox=1.0,2.0,3.0,4.0" in clip_cmd
    assert "--co=COMPRESS=DEFLATE" in clip_cmd
    assert translate_cmd[0] == "gdal_translate"
    assert "-mask" in translate_cmd and "4" in translate_cmd
    assert "GDAL_TIFF_INTERNAL_MASK" in translate_cmd
    assert translate_cmd[-1] == out_path


def test_clip_wmts_area_includes_bbox_crs_when_given(tmp_path):
    """The actual safety fix requested directly ("we must be careful not
    to mix crs between canvas extent and gdal_translate coordinates"):
    bbox_crs must reach the real gdal raster clip command as an explicit
    --bbox-crs flag, not be left for GDAL to assume matches the input
    dataset's own CRS."""
    calls = []

    def fake_run(cmd, check=True, capture_output=True):
        calls.append(cmd)

    wmts_export.clip_wmts_area(
        "wmts_def.xml",
        bbox=(1.0, 2.0, 3.0, 4.0),
        out_path=str(tmp_path / "out.tif"),
        bbox_crs="EPSG:3857",
        creation_options=["COMPRESS=DEFLATE"],
        run=_stub_version_check(fake_run),
    )
    assert "--bbox-crs=EPSG:3857" in calls[0]


def test_clip_wmts_area_omits_bbox_crs_when_not_given(tmp_path):
    calls = []

    def fake_run(cmd, check=True, capture_output=True):
        calls.append(cmd)

    wmts_export.clip_wmts_area(
        "wmts_def.xml",
        bbox=(1.0, 2.0, 3.0, 4.0),
        out_path=str(tmp_path / "out.tif"),
        creation_options=["COMPRESS=DEFLATE"],
        run=_stub_version_check(fake_run),
    )
    assert not any("--bbox-crs" in arg for arg in calls[0])


def test_clip_wmts_area_includes_bbox_crs_in_the_jpeg_pipelines_clip_step(tmp_path):
    calls = []

    def fake_run(cmd, check=True, capture_output=True):
        calls.append(cmd)
        if cmd[0] == "gdal":
            open(cmd[-2], "wb").close()

    wmts_export.clip_wmts_area(
        "wmts_def.xml",
        bbox=(1.0, 2.0, 3.0, 4.0),
        out_path=str(tmp_path / "out.tif"),
        bbox_crs="EPSG:2154",
        tmp_dir=str(tmp_path),
        run=_stub_version_check(fake_run),
    )
    clip_cmd = calls[0]
    assert "--bbox-crs=EPSG:2154" in clip_cmd


def test_clip_wmts_area_defaults_tmp_dir_to_the_real_os_temp_dir(tmp_path, monkeypatch):
    """Regression test for a real reported failure ("read only /tmp...").
    An earlier version defaulted tmp_dir to "." (the process's current
    working directory), which for a GUI application like QGIS is
    frequently somewhere unrelated to user data and not guaranteed
    writable at all. Confirmed here by monkeypatching
    tempfile.gettempdir() itself and checking the scratch directory
    that TemporaryDirectory actually receives as its `dir` argument is
    exactly that, not "." and not anything else."""
    import tempfile as tempfile_module

    fake_os_tmp = str(tmp_path / "fake_os_tmp")
    Path(fake_os_tmp).mkdir()
    monkeypatch.setattr(tempfile_module, "gettempdir", lambda: fake_os_tmp)

    captured_dirs = []
    real_temporary_directory = tempfile_module.TemporaryDirectory

    class RecordingTemporaryDirectory(real_temporary_directory):
        def __init__(self, *a, **k):
            captured_dirs.append(k.get("dir"))
            super().__init__(*a, **k)

    monkeypatch.setattr(
        wmts_export.tempfile, "TemporaryDirectory", RecordingTemporaryDirectory
    )

    def fake_run(cmd, check=True, capture_output=True):
        if cmd[0] == "gdal":
            open(cmd[-2], "wb").close()

    wmts_export.clip_wmts_area(
        "wmts_def.xml",
        bbox=(1.0, 2.0, 3.0, 4.0),
        out_path=str(tmp_path / "out.tif"),
        run=_stub_version_check(fake_run),
        # tmp_dir deliberately not given - this is the default path
    )

    assert captured_dirs == [fake_os_tmp]


def test_clip_wmts_area_uses_single_step_for_non_jpeg_creation_options(tmp_path):
    calls = []

    def fake_run(cmd, check=True, capture_output=True):
        calls.append(cmd)

    out_path = str(tmp_path / "out.tif")
    wmts_export.clip_wmts_area(
        "wmts_def.xml",
        bbox=(1.0, 2.0, 3.0, 4.0),
        out_path=out_path,
        creation_options=["COMPRESS=DEFLATE", "TILED=YES"],
        run=_stub_version_check(fake_run),
    )

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[:3] == ["gdal", "raster", "clip"]
    assert "--co=COMPRESS=DEFLATE" in cmd
    assert "--co=COMPRESS=JPEG" not in cmd
    assert cmd[-2] == out_path


def test_clip_wmts_area_includes_gdal_http_headers_config_when_given(tmp_path):
    calls = []

    def fake_run(cmd, check=True, capture_output=True):
        calls.append(cmd)

    wmts_export.clip_wmts_area(
        "wmts_def.xml",
        bbox=(1.0, 2.0, 3.0, 4.0),
        out_path=str(tmp_path / "out.tif"),
        creation_options=["COMPRESS=DEFLATE"],
        gdal_config=["--config", "GDAL_HTTP_HEADERS", "apikey: secret"],
        run=_stub_version_check(fake_run),
    )
    assert "--config" in calls[0]
    assert "apikey: secret" in calls[0]


def test_clip_wmts_area_logs_both_commands_for_the_jpeg_pipeline(tmp_path):
    def fake_run(cmd, check=True, capture_output=True):
        if cmd[0] == "gdal":
            open(cmd[-2], "wb").close()

    logged = []
    wmts_export.clip_wmts_area(
        "wmts_def.xml",
        bbox=(1.0, 2.0, 3.0, 4.0),
        out_path=str(tmp_path / "out.tif"),
        tmp_dir=str(tmp_path),
        run=_stub_version_check(fake_run),
        log=logged.append,
    )
    assert len(logged) == 2
    assert logged[0].startswith("+ gdal raster clip")
    assert logged[1].startswith("+ gdal_translate")


# --------------------------------------------------------------------------- error surfacing + version check


def test_gdal_command_error_includes_real_stderr_not_the_generic_message():
    """Regression test for the actual reported failure ("Could not
    export the clipped area: Command '[...]' returned non-zero exit
    status 1.") - the real GDAL error text was always captured
    (capture_output=True) but never surfaced anywhere up the call
    chain. _run must now raise something whose message actually
    contains it."""
    import subprocess

    def failing_run(cmd, check=True, capture_output=True):
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=cmd,
            output=b"",
            stderr=b"ERROR 1: WMTS driver: no such TileMatrixSet 'PM'\n",
        )

    try:
        wmts_export._run(
            ["gdal", "raster", "clip", "in.xml", "out.tif"], run=failing_run
        )
        assert False, "expected GdalCommandError"
    except wmts_export.GdalCommandError as e:
        assert "no such TileMatrixSet" in str(e)
        assert "no such TileMatrixSet" in e.stderr


def test_gdal_command_error_falls_back_to_stdout_when_stderr_is_empty():
    """Some CLI tools write diagnostics to stdout instead of stderr -
    the real error text must still surface either way."""
    import subprocess

    def failing_run(cmd, check=True, capture_output=True):
        raise subprocess.CalledProcessError(
            returncode=1, cmd=cmd, output=b"diagnostic on stdout instead", stderr=b""
        )

    try:
        wmts_export._run(["gdal_translate"], run=failing_run)
        assert False, "expected GdalCommandError"
    except wmts_export.GdalCommandError as e:
        assert "diagnostic on stdout instead" in str(e)


def test_check_gdal_version_succeeds_for_a_new_enough_version():
    def fake_run(cmd, capture_output=True, check=True):
        return SimpleNamespace(stdout=b"GDAL 3.11.2, released 2025/06/01\n")

    assert wmts_export.check_gdal_version(run=fake_run) == (3, 11)


def test_check_gdal_version_accepts_a_newer_major_version_too():
    def fake_run(cmd, capture_output=True, check=True):
        return SimpleNamespace(stdout=b"GDAL 4.0.0, released 2027/01/01\n")

    assert wmts_export.check_gdal_version(run=fake_run) == (4, 0)


def test_check_gdal_version_raises_for_a_too_old_version():
    def fake_run(cmd, capture_output=True, check=True):
        return SimpleNamespace(stdout=b"GDAL 3.6.2, released 2023/01/02\n")

    try:
        wmts_export.check_gdal_version(run=fake_run)
        assert False, "expected GdalVersionTooOld"
    except wmts_export.GdalVersionTooOld as e:
        assert e.found_version == "3.6"
        assert "3.6" in str(e)
        assert "3.11" in str(e)


def test_check_gdal_version_raises_with_a_clear_message_when_gdal_is_missing():
    def fake_run(cmd, capture_output=True, check=True):
        raise FileNotFoundError("No such file or directory: 'gdal'")

    try:
        wmts_export.check_gdal_version(run=fake_run)
        assert False, "expected GdalVersionTooOld"
    except wmts_export.GdalVersionTooOld as e:
        assert e.found_version is None


def test_check_gdal_version_raises_when_output_is_unparseable():
    def fake_run(cmd, capture_output=True, check=True):
        return SimpleNamespace(stdout=b"not a version string at all")

    try:
        wmts_export.check_gdal_version(run=fake_run)
        assert False, "expected GdalVersionTooOld"
    except wmts_export.GdalVersionTooOld as e:
        assert e.found_version is None


def test_clip_wmts_area_raises_clearly_when_gdal_is_too_old(tmp_path):
    """End-to-end: clip_wmts_area itself must fail with the clear
    version message, not attempt the clip and fail cryptically."""

    def fake_run(cmd, capture_output=True, check=True):
        if cmd[:2] == ["gdal", "--version"]:
            return SimpleNamespace(stdout=b"GDAL 3.8.0, released 2024/01/01\n")
        raise AssertionError("must not attempt the actual clip on a too-old GDAL")

    try:
        wmts_export.clip_wmts_area(
            "wmts_def.xml",
            bbox=(1.0, 2.0, 3.0, 4.0),
            out_path=str(tmp_path / "out.tif"),
            run=fake_run,
        )
        assert False, "expected GdalVersionTooOld"
    except wmts_export.GdalVersionTooOld:
        pass
