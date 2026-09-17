"""
ui.download_task - runs download.pipeline.download_items() as a real
QgsTask, so a batch download does not block QGIS's UI thread for its
entire duration. Concurrency (parallel connections) is a separate,
not-yet-built capability - files are downloaded one at a time, just off
the main thread, so QGIS itself stays responsive while that sequential
download runs.

QgsTask.run() executes on a worker thread; this class emits plain Qt
signals from within it (fileStarted/fileProgress/finishedWithOutcomes) -
safe to do despite the cross-thread call, since Qt queues signal
delivery to whichever thread the *receiver* lives in (the main thread,
for anything connected from ui.download_progress_dialog) rather than
executing the connected slot on the emitting thread directly. This is
the standard, documented Qt mechanism for this exact situation, not a
workaround specific to this module.
"""

from typing import List, Optional

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from sigate.download import pipeline


class DownloadItemsTask(QgsTask):
    """Wraps download.pipeline.download_items() as a cancellable
    background task. Construct, connect to the signals below, then hand
    to QgsApplication.taskManager().addTask() to actually start it -
    this class doesn't start itself, matching QgsTask's own convention
    (a task exists independently of being scheduled)."""

    # (file_index, total_files, filename) - 0-based index, right before
    # that file's own download begins.
    fileStarted = pyqtSignal(int, int, str)
    # (bytes_read, bytes_total) for the file currently in flight -
    # bytes_total is -1 if the server didn't report a Content-Length
    # (gateways.atom.download's own None, translated to an int-typed
    # signal-safe sentinel since PyQt signals can't carry Optional[int]
    # directly).
    fileProgress = pyqtSignal(int, int)
    # Emitted once, from finished() (called back on the main thread once
    # run() completes, per QgsTask's own documented contract) - carries
    # whatever outcomes were actually gathered, complete or partial.
    finishedWithOutcomes = pyqtSignal(list)

    def __init__(
        self,
        items: List[pipeline.DownloadItem],
        central_repo,
        download_fn=None,
    ) -> None:
        super().__init__(
            "SIGate download", QgsTask.Flag.CanCancel | QgsTask.Flag.CancelWithoutPrompt
        )
        self._items = items
        self._central_repo = central_repo
        self._download_fn = download_fn
        self.outcomes: List[pipeline.DownloadOutcome] = []
        self.exception: Optional[Exception] = None
        self._total = len(items)

    def run(self) -> bool:
        """Runs on a worker thread, per QgsTask's own contract. Returns
        True on success (even if some individual items failed - check
        self.outcomes for per-item results); returns False only if
        download_items itself raised, which download_items' own
        contract says it should not for a per-item failure (those become
        DownloadOutcome.error entries instead) - a True raise here means
        something genuinely unexpected happened."""
        try:
            self.outcomes = pipeline.download_items(
                self._items,
                self._central_repo,
                download_fn=self._download_fn,
                on_file_start=self._on_file_start,
                on_file_progress=self._on_file_progress,
                should_continue=lambda: not self.isCanceled(),
            )
        except Exception as e:  # pragma: no cover - genuinely unexpected
            self.exception = e
            return False
        return True

    def _on_file_start(
        self, index: int, total: int, item: pipeline.DownloadItem
    ) -> None:
        if total:
            self.setProgress(index * 100 / total)
        self.fileStarted.emit(index, total, item.filename)

    def _on_file_progress(self, read: int, total: Optional[int]) -> None:
        self.fileProgress.emit(read, total if total is not None else -1)

    def finished(self, result: bool) -> None:
        # Called on the main thread by QGIS's task manager once run()
        # returns - safe to touch Qt widgets from a slot connected to
        # this signal, unlike anything called directly from run() itself.
        self.finishedWithOutcomes.emit(self.outcomes)
