"""
Tests for sigate.ui.wmts_export_task.WmtsExportTask - exercised
directly, the same way tests/test_download_task.py exercises its
sibling class, rather than only indirectly through
ui.wmts_export_dialog. run() itself contains no real threading -
QgsTask's actual worker-thread scheduling only happens via
QgsApplication.taskManager().addTask().
"""

from sigate.ui.wmts_export_task import WmtsExportTask


def _make_task(**overrides):
    kwargs = dict(
        def_path="/tmp/def.xml",
        caps_url="https://x/wmts",
        gdal_config=[],
        max_connections=2,
        layer="LAYER",
        tilematrixset="PM",
        style=None,
        bbox=(1.0, 2.0, 3.0, 4.0),
        out_path="/tmp/out.tif",
    )
    kwargs.update(overrides)
    return WmtsExportTask(**kwargs)


def test_run_calls_both_gateway_steps_in_order_and_returns_true_on_success(
    qgis_app, monkeypatch
):
    import sigate.ui.wmts_export_task as task_module

    calls = []

    def fake_ensure_wmts_def(*args, **kwargs):
        calls.append(("ensure_wmts_def", args, kwargs))
        return "source.xml"

    def fake_clip_wmts_area(*args, **kwargs):
        calls.append(("clip_wmts_area", args, kwargs))
        return "/tmp/out.tif"

    monkeypatch.setattr(
        task_module.wmts_export, "ensure_wmts_def", fake_ensure_wmts_def
    )
    monkeypatch.setattr(task_module.wmts_export, "clip_wmts_area", fake_clip_wmts_area)

    task = _make_task()
    result = task.run()

    assert result is True
    assert task.exception is None
    assert [c[0] for c in calls] == ["ensure_wmts_def", "clip_wmts_area"]
    # clip_wmts_area must receive the *source* returned by ensure_wmts_def,
    # not the raw def_path - confirms the two steps are genuinely chained,
    # not just both called independently.
    assert calls[1][1][0] == "source.xml"


def test_run_threads_tilematrix_and_bbox_crs_through_to_the_real_calls(
    qgis_app, monkeypatch
):
    """Both parameters are real, load-bearing arguments (tilematrix
    genuinely constrains what GDAL fetches; bbox_crs is the fix for a
    real reported CRS-mixing risk) - a regression here would silently
    stop either one from actually reaching GDAL while every other test
    still passed."""
    import sigate.ui.wmts_export_task as task_module

    captured = {}

    def fake_ensure_wmts_def(
        def_path,
        caps_url,
        gdal_config,
        max_connections,
        layer,
        tilematrixset,
        style=None,
        tilematrix=None,
        run=None,
        log=None,
    ):
        captured["tilematrix"] = tilematrix
        return "source.xml"

    def fake_clip_wmts_area(source, bbox, out_path, bbox_crs=None, **kwargs):
        captured["bbox_crs"] = bbox_crs
        return out_path

    monkeypatch.setattr(
        task_module.wmts_export, "ensure_wmts_def", fake_ensure_wmts_def
    )
    monkeypatch.setattr(task_module.wmts_export, "clip_wmts_area", fake_clip_wmts_area)

    task = _make_task(tilematrix="18", bbox_crs="EPSG:3857")
    task.run()

    assert captured["tilematrix"] == "18"
    assert captured["bbox_crs"] == "EPSG:3857"


def test_run_returns_false_and_sets_exception_when_ensure_wmts_def_raises(
    qgis_app, monkeypatch
):
    import sigate.ui.wmts_export_task as task_module

    def failing_ensure_wmts_def(*args, **kwargs):
        raise RuntimeError("gdal_translate failed")

    def clip_should_not_be_called(*args, **kwargs):
        raise AssertionError("must not clip if the definition step failed")

    monkeypatch.setattr(
        task_module.wmts_export, "ensure_wmts_def", failing_ensure_wmts_def
    )
    monkeypatch.setattr(
        task_module.wmts_export, "clip_wmts_area", clip_should_not_be_called
    )

    task = _make_task()
    result = task.run()

    assert result is False
    assert isinstance(task.exception, RuntimeError)
    assert "gdal_translate failed" in str(task.exception)


def test_run_returns_false_and_sets_exception_when_clip_wmts_area_raises(
    qgis_app, monkeypatch
):
    import sigate.ui.wmts_export_task as task_module

    monkeypatch.setattr(
        task_module.wmts_export, "ensure_wmts_def", lambda *a, **k: "source.xml"
    )

    def failing_clip(*args, **kwargs):
        raise RuntimeError("gdal raster clip failed")

    monkeypatch.setattr(task_module.wmts_export, "clip_wmts_area", failing_clip)

    task = _make_task()
    result = task.run()

    assert result is False
    assert isinstance(task.exception, RuntimeError)
    assert "gdal raster clip failed" in str(task.exception)


def test_run_skips_the_clip_step_when_cancelled_between_the_two_steps(
    qgis_app, monkeypatch
):
    """Documented, real limitation: cooperative cancellation here is
    only checked between the two possible GDAL steps, never mid-command
    (GDAL's own CLI tools offer no clean interrupt mechanism). Confirmed
    here that the check genuinely does prevent the second step from
    starting, at least."""
    import sigate.ui.wmts_export_task as task_module

    task = _make_task()

    def fake_ensure_wmts_def(*args, **kwargs):
        task.cancel()  # simulates cancellation arriving during step 1
        return "source.xml"

    def clip_should_not_be_called(*args, **kwargs):
        raise AssertionError("must not start the clip step once cancelled")

    monkeypatch.setattr(
        task_module.wmts_export, "ensure_wmts_def", fake_ensure_wmts_def
    )
    monkeypatch.setattr(
        task_module.wmts_export, "clip_wmts_area", clip_should_not_be_called
    )

    result = task.run()

    assert result is False
    assert task.exception is None  # a clean cancellation, not an error


def test_log_emits_the_log_message_signal(qgis_app):
    task = _make_task()
    captured = []
    task.logMessage.connect(lambda message: captured.append(message))

    task._log("+ gdal raster clip ...")

    assert captured == ["+ gdal raster clip ..."]


def test_finished_emits_finished_export_regardless_of_result(qgis_app):
    task = _make_task()
    captured = []
    task.finishedExport.connect(lambda: captured.append(True))

    task.finished(False)  # even on failure, the signal must still fire

    assert captured == [True]
