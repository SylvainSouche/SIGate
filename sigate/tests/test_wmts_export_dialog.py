"""
Tests for sigate.ui.wmts_export_dialog.WmtsExportDialog - exercised
directly (constructing it, calling its private signal handlers as plain
methods, the same way tests/test_download_progress_dialog.py exercises
its sibling class) rather than only through the real task manager, which
would require actually running GDAL commands.
"""

_TASK_KWARGS = dict(
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


def _make_dialog(qgis_app):
    from sigate.ui.wmts_export_dialog import WmtsExportDialog

    return WmtsExportDialog(None, **_TASK_KWARGS)


def test_construction_sets_initial_state(qgis_app):
    dialog = _make_dialog(qgis_app)

    assert dialog.succeeded is False
    assert dialog.exception is None
    assert dialog.was_cancelled is False
    assert dialog.status_label.text() == "Preparing..."
    # indeterminate range, per the module's own documented choice
    # (GDAL's CLI tools expose no per-byte progress to show a real one)
    assert dialog.progress_bar.minimum() == 0
    assert dialog.progress_bar.maximum() == 0


def test_on_finished_marks_succeeded_and_closes_when_task_has_no_exception(qgis_app):
    dialog = _make_dialog(qgis_app)
    dialog._task.exception = None

    dialog._on_finished()

    assert dialog.succeeded is True
    assert dialog.exception is None
    assert dialog.result() == 1  # QDialog.Accepted


def test_on_finished_marks_failed_but_still_closes_when_task_raised(qgis_app):
    """The dialog must still close on failure, not hang open - the
    caller (run_modal) is expected to report the real exception itself,
    not leave a stuck modal dialog on screen."""
    dialog = _make_dialog(qgis_app)
    dialog._task.exception = RuntimeError("gdal raster clip failed")

    dialog._on_finished()

    assert dialog.succeeded is False
    assert isinstance(dialog.exception, RuntimeError)
    assert dialog.result() == 1  # still closes


def test_on_cancel_clicked_sets_was_cancelled_and_disables_the_button(qgis_app):
    dialog = _make_dialog(qgis_app)

    dialog._on_cancel_clicked()

    assert dialog.was_cancelled is True
    assert dialog._cancel_button.isEnabled() is False
    assert "Cancelling" in dialog.status_label.text()


def test_on_cancel_clicked_actually_cancels_the_underlying_task(qgis_app):
    dialog = _make_dialog(qgis_app)

    dialog._on_cancel_clicked()

    assert dialog._task.isCanceled() is True


def test_log_message_signal_updates_the_status_label(qgis_app):
    """Confirms the real wiring (self._task.logMessage.connect(self.
    status_label.setText)) actually works, not just that both pieces
    exist independently."""
    dialog = _make_dialog(qgis_app)

    dialog._task.logMessage.emit("+ gdal raster clip ...")

    assert dialog.status_label.text() == "+ gdal raster clip ..."


def test_run_modal_returns_task_state_after_a_synchronous_finish(qgis_app, monkeypatch):
    """run_modal's own contract (succeeded, exception, was_cancelled) -
    exercised by short-circuiting start() to call _on_finished()
    directly instead of scheduling the task for real, avoiding an actual
    GDAL invocation and an actual blocking exec() call in a headless
    test."""
    import sigate.ui.wmts_export_dialog as dialog_module

    def fake_start(self):
        self._task.exception = None
        self._on_finished()

    def fake_exec(self):
        return None

    monkeypatch.setattr(dialog_module.WmtsExportDialog, "start", fake_start)
    monkeypatch.setattr(dialog_module.WmtsExportDialog, "exec", fake_exec)

    succeeded, exception, was_cancelled = dialog_module.run_modal(None, **_TASK_KWARGS)

    assert succeeded is True
    assert exception is None
    assert was_cancelled is False


def test_run_modal_reports_a_real_exception_on_failure(qgis_app, monkeypatch):
    import sigate.ui.wmts_export_dialog as dialog_module

    def fake_start(self):
        self._task.exception = RuntimeError("boom")
        self._on_finished()

    def fake_exec(self):
        return None

    monkeypatch.setattr(dialog_module.WmtsExportDialog, "start", fake_start)
    monkeypatch.setattr(dialog_module.WmtsExportDialog, "exec", fake_exec)

    succeeded, exception, was_cancelled = dialog_module.run_modal(None, **_TASK_KWARGS)

    assert succeeded is False
    assert isinstance(exception, RuntimeError)
    assert was_cancelled is False
