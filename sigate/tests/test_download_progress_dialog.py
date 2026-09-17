"""
Tests for sigate.ui.download_progress_dialog - exercises the dialog's
own display-update logic directly (calling _on_file_started/
_on_file_progress/_on_finished as plain methods) rather than through a
real, scheduled QgsTask emitting across threads - that path needs a live
task manager actually running a background thread and pumping the Qt
event loop to deliver the cross-thread signal, which isn't something
this suite can drive deterministically. What's covered here - given a
signal fired with certain values, did the dialog update its widgets
correctly - is the part that's actually at risk of a bug; the
cross-thread delivery mechanism itself is standard, well-established Qt
behaviour, not something this project's own code could get wrong.
"""


def _make_dialog(qgis_app, tmp_path, n_items=3):
    from sigate.download.pipeline import DownloadItem
    from sigate.ui.download_progress_dialog import DownloadProgressDialog

    items = [
        DownloadItem(url=f"https://x/{i}", filename=f"f{i}.txt") for i in range(n_items)
    ]
    # Never call .start() in these tests - that would actually schedule
    # a real QgsTask, which is exactly the part deliberately not
    # exercised here (see module docstring).
    return DownloadProgressDialog(None, items, tmp_path, download_fn=None)


def test_overall_bar_range_matches_item_count(qgis_app, tmp_path):
    dialog = _make_dialog(qgis_app, tmp_path, n_items=5)
    assert dialog.overall_bar.minimum() == 0
    assert dialog.overall_bar.maximum() == 5


def test_on_finished_reports_a_genuinely_unexpected_task_exception(
    qgis_app, tmp_path, monkeypatch
):
    """Regression test for a real gap found during review:
    DownloadItemsTask.exception (set when download_items() itself raises,
    as opposed to a per-item failure) was never checked anywhere - the
    dialog would silently close showing an empty/partial result with no
    indication anything had gone wrong at all."""
    from qgis.PyQt.QtWidgets import QMessageBox

    shown = []
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        staticmethod(lambda *a, **k: shown.append(a[2] if len(a) > 2 else "")),
    )

    dialog = _make_dialog(qgis_app, tmp_path, n_items=2)
    dialog._task.exception = RuntimeError("boom")

    dialog._on_finished([])

    assert len(shown) == 1
    assert "boom" in shown[0]
    assert dialog.result() == 1  # still closes (QDialog.Accepted)


def test_on_finished_does_not_warn_when_task_has_no_exception(qgis_app, tmp_path):
    dialog = _make_dialog(qgis_app, tmp_path, n_items=2)
    assert dialog._task.exception is None
    dialog._on_finished([])  # must not raise or attempt to show anything
    assert dialog.result() == 1
    """A zero-length range would make the bar meaningless (or divide by
    zero elsewhere) - shouldn't come up in practice (run_download_and_
    add_layers already returns early for an empty item list), but the
    dialog itself shouldn't misbehave if constructed with one anyway."""
    dialog = _make_dialog(qgis_app, tmp_path, n_items=0)
    assert dialog.overall_bar.maximum() == 1


def test_file_started_updates_label_and_overall_bar(qgis_app, tmp_path):
    dialog = _make_dialog(qgis_app, tmp_path, n_items=3)
    dialog._on_file_started(1, 3, "b.tif")
    assert "File 2 of 3" in dialog.file_label.text()
    assert "b.tif" in dialog.file_label.text()
    assert dialog.overall_bar.value() == 1
    assert dialog.file_bar.value() == 0  # reset for the new file


def test_file_progress_with_known_total_shows_percentage_and_mb(qgis_app, tmp_path):
    dialog = _make_dialog(qgis_app, tmp_path)
    dialog._on_file_progress(50_000_000, 100_000_000)
    assert dialog.file_bar.minimum() == 0
    assert dialog.file_bar.maximum() == 100
    assert dialog.file_bar.value() == 50
    assert "50.0" in dialog.byte_label.text()
    assert "100.0" in dialog.byte_label.text()


def test_file_progress_with_unknown_total_is_indeterminate(qgis_app, tmp_path):
    """total=-1 is this signal's own sentinel for "the server didn't
    report a Content-Length" (see download_task's docstring) - a
    percentage against nothing would be misleading, so the bar switches
    to Qt's own indeterminate/marquee mode instead (a 0,0 range)."""
    dialog = _make_dialog(qgis_app, tmp_path)
    dialog._on_file_progress(12_300_000, -1)
    assert dialog.file_bar.minimum() == 0
    assert dialog.file_bar.maximum() == 0
    assert "12.3" in dialog.byte_label.text()
    assert "/" not in dialog.byte_label.text()  # no "of total" - there isn't one


def test_file_progress_caps_percentage_at_100(qgis_app, tmp_path):
    """A server that lied about Content-Length (rare, but real servers
    do this) shouldn't be able to push the bar past its own range."""
    dialog = _make_dialog(qgis_app, tmp_path)
    dialog._on_file_progress(150, 100)
    assert dialog.file_bar.value() == 100


def test_finished_sets_outcomes_and_completes_overall_bar(qgis_app, tmp_path):
    from sigate.download.pipeline import DownloadItem, DownloadOutcome

    dialog = _make_dialog(qgis_app, tmp_path, n_items=2)
    outcomes = [
        DownloadOutcome(
            item=DownloadItem(url="https://x/0", filename="f0.txt"),
            path=tmp_path / "f0.txt",
            already_existed=False,
        )
    ]
    dialog._on_finished(outcomes)
    assert dialog.outcomes == outcomes
    assert dialog.overall_bar.value() == 2
    assert dialog.result() == 1  # QDialog.Accepted


def test_cancel_disables_the_cancel_button_and_updates_the_label(qgis_app, tmp_path):
    """Confirms the button is disabled and the label changes immediately
    on Cancel, without needing the task to actually be running (a real
    Cancel click before start() would otherwise raise, since there'd be
    no task to call .cancel() on) - task.cancel() itself is stubbed out
    here for exactly that reason."""
    dialog = _make_dialog(qgis_app, tmp_path)
    dialog._task.cancel = lambda: None
    dialog._on_cancel_clicked()
    assert dialog.was_cancelled is True
    assert not dialog._cancel_button.isEnabled()
    assert "Cancelling" in dialog.file_label.text()
    # the dialog itself must NOT have closed yet - only _on_finished does
    # that, once the task actually stops (see the method's own docstring)
    assert dialog.result() == 0  # QDialog.Rejected/no result yet, not closed


def test_run_modal_uses_the_dialog_and_returns_its_outcomes(
    qgis_app, tmp_path, monkeypatch
):
    """run_modal's own wiring (construct, start, exec, return
    dialog.outcomes/was_cancelled) - exercised with exec() and start()
    both stubbed out, since a real exec() would block on user
    interaction and a real start() would schedule a genuine background
    task, neither of which this test drives."""
    import sigate.ui.download_progress_dialog as module

    captured = {}

    class FakeDialog:
        def __init__(self, parent, items, central_repo, download_fn=None):
            captured["items"] = items
            captured["central_repo"] = central_repo
            self.outcomes = ["fake-outcome"]
            self.was_cancelled = True

        def start(self):
            captured["started"] = True

        def exec(self):
            captured["exec_called"] = True

    monkeypatch.setattr(module, "DownloadProgressDialog", FakeDialog)

    from sigate.download.pipeline import DownloadItem

    items = [DownloadItem(url="https://x/a", filename="a.txt")]
    outcomes, was_cancelled = module.run_modal(None, items, tmp_path)

    assert captured["items"] == items
    assert captured["started"] is True
    assert captured["exec_called"] is True
    assert outcomes == ["fake-outcome"]
    assert was_cancelled is True
