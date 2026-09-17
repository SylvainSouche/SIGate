"""
Tests for sigate.ui.download_task.DownloadItemsTask - exercised
directly (constructing it, calling run()/finished() as plain methods)
rather than only indirectly through ui.download_progress_dialog, which
is the only place this class had been exercised at all before this file
existed. run() itself contains no real threading - QgsTask's actual
worker-thread scheduling only happens via QgsApplication.taskManager().
addTask(), so calling it directly here is a safe, synchronous unit test
of its own logic.
"""

from pathlib import Path

from sigate.download.pipeline import DownloadItem, DownloadOutcome
from sigate.ui.download_task import DownloadItemsTask


def _make_task(items, download_fn=None, central_repo=Path(".")):
    return DownloadItemsTask(items, central_repo, download_fn=download_fn)


def test_run_returns_true_and_populates_outcomes_on_success(qgis_app, tmp_path):
    def fake_download(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        path = Path(dest_dir) / filename
        path.write_bytes(b"data")
        return path

    items = [DownloadItem(url="https://x/a.tif", filename="a.tif")]
    task = _make_task(items, download_fn=fake_download, central_repo=tmp_path)

    result = task.run()

    assert result is True
    assert task.exception is None
    assert len(task.outcomes) == 1
    assert task.outcomes[0].succeeded is True


def test_run_returns_false_and_sets_exception_on_a_genuinely_unexpected_failure(
    qgis_app, monkeypatch
):
    """Regression test for a real gap found during review: this
    exception was set but never read anywhere at all before it was
    wired into ui.download_progress_dialog's own _on_finished. Confirmed
    here at the source: run() must actually set it, not just swallow the
    unexpected failure silently."""
    import sigate.ui.download_task as download_task_module

    def raising_download_items(*args, **kwargs):
        raise RuntimeError("genuinely unexpected failure")

    monkeypatch.setattr(
        download_task_module.pipeline, "download_items", raising_download_items
    )

    task = _make_task([DownloadItem(url="https://x/a.tif", filename="a.tif")])
    result = task.run()

    assert result is False
    assert isinstance(task.exception, RuntimeError)
    assert task.outcomes == []


def test_run_never_calls_download_fn_when_cancelled_before_starting(qgis_app):
    """Cancelling before the loop ever reaches should_continue()'s first
    check means the loop breaks immediately with no outcome appended at
    all for that item - a "Cancelled" DownloadOutcome is only produced
    when download_fn itself raises DownloadCancelled mid-transfer, a
    genuinely different case (checked separately below)."""

    def download_fn_should_not_be_called(*a, **k):
        raise AssertionError("must not download anything once cancelled")

    task = _make_task(
        [DownloadItem(url="https://x/a.tif", filename="a.tif")],
        download_fn=download_fn_should_not_be_called,
    )
    task.cancel()

    result = task.run()

    assert result is True  # cancellation is not treated as an unexpected failure
    assert task.outcomes == []


def test_run_reports_a_cancelled_outcome_when_cancelled_mid_transfer(qgis_app):
    """The other cancellation case: should_continue() only turns False
    *during* download_fn's own call (mid-transfer) - download_fn is
    expected to raise DownloadCancelled itself when it notices, per
    gateways.atom.download's own real behavior, which produces a real
    "Cancelled" DownloadOutcome rather than an empty list."""
    from sigate.gateways.base import DownloadCancelled

    def download_fn_raises_cancelled(
        url, dest_dir, filename, progress_callback=None, should_continue=None
    ):
        raise DownloadCancelled("cancelled mid-transfer")

    task = _make_task(
        [DownloadItem(url="https://x/a.tif", filename="a.tif")],
        download_fn=download_fn_raises_cancelled,
    )

    result = task.run()

    assert result is True
    assert len(task.outcomes) == 1
    assert task.outcomes[0].error == "Cancelled"


def test_on_file_start_emits_file_started_with_zero_based_index_and_filename(
    qgis_app,
):
    task = _make_task([])
    captured = []
    task.fileStarted.connect(
        lambda index, total, filename: captured.append((index, total, filename))
    )

    task._on_file_start(1, 3, DownloadItem(url="https://x/b.tif", filename="b.tif"))

    assert captured == [(1, 3, "b.tif")]


def test_on_file_progress_translates_none_total_to_negative_one_sentinel(qgis_app):
    """PyQt signals can't carry Optional[int] directly - gateways.atom.
    download's own None (no Content-Length from the server) must arrive
    on the other end of this signal as -1, a real, deliberate sentinel
    value, not silently coerced to 0 or dropped."""
    task = _make_task([])
    captured = []
    task.fileProgress.connect(lambda read, total: captured.append((read, total)))

    task._on_file_progress(512, None)

    assert captured == [(512, -1)]


def test_on_file_progress_passes_through_a_real_known_total(qgis_app):
    task = _make_task([])
    captured = []
    task.fileProgress.connect(lambda read, total: captured.append((read, total)))

    task._on_file_progress(512, 1024)

    assert captured == [(512, 1024)]


def test_finished_emits_finished_with_outcomes_carrying_whatever_was_gathered(
    qgis_app,
):
    task = _make_task([])
    outcome = DownloadOutcome(
        item=DownloadItem(url="https://x/a.tif", filename="a.tif"),
        path=Path("a.tif"),
        already_existed=False,
    )
    task.outcomes = [outcome]
    captured = []
    task.finishedWithOutcomes.connect(lambda outcomes: captured.append(outcomes))

    task.finished(True)

    assert captured == [[outcome]]


def test_finished_emits_even_when_outcomes_is_empty_after_an_unexpected_failure(
    qgis_app,
):
    """finished() must not assume run() succeeded - it fires from
    QgsTask's own contract regardless of run()'s return value, and must
    still emit (with whatever partial/empty outcomes exist) so a
    listener like DownloadProgressDialog can close cleanly and report
    the failure, rather than hanging waiting for a signal that never
    comes."""
    task = _make_task([])
    task.exception = RuntimeError("boom")
    captured = []
    task.finishedWithOutcomes.connect(lambda outcomes: captured.append(outcomes))

    task.finished(False)

    assert captured == [[]]
