"""
ui.download_progress_dialog - a modal "Downloading..." popup with two
progress bars (overall file count, current file's own byte progress),
backed by ui.download_task.DownloadItemsTask running the actual download
in the background via QgsTask.

Modal, but not blocking in the problematic sense: QDialog.exec() runs a
nested Qt event loop while it's showing, which keeps processing events
(including the background task's cross-thread signal delivery, and
Cancel button clicks) the whole time - QGIS's UI doesn't freeze the way
it did calling download.pipeline.download_items() directly on the main
thread. Modal only means the user can't interact with *other* windows
meanwhile, a deliberate choice (prevents confusing interaction with the
same tab mid-download) - the underlying work genuinely runs off-thread.

run_modal() is the entry point ui.download_flow actually calls - it
preserves that module's existing synchronous call shape
(items/central_repo/download_fn in, outcomes out) by blocking on
exec(), so the rest of download_flow's post-download logic (archive
extraction, mosaic building, add_layer calls - all real Qt calls that
must happen on the main thread) needed no restructuring into an async
continuation.
"""

from typing import Callable, List, Optional, Tuple

from pathlib import Path

from qgis.core import QgsApplication
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from sigate.download import pipeline

from .download_task import DownloadItemsTask


class DownloadProgressDialog(QDialog):
    def __init__(
        self,
        parent: Optional[QWidget],
        items: List[pipeline.DownloadItem],
        central_repo: Path,
        download_fn: Optional[Callable] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Downloading"))
        self.setModal(True)
        self.setMinimumWidth(420)

        self.outcomes: List[pipeline.DownloadOutcome] = []
        self.was_cancelled = False
        self._total = len(items)

        layout = QVBoxLayout(self)

        self.file_label = QLabel(self.tr("Preparing..."), self)
        layout.addWidget(self.file_label)
        self.overall_bar = QProgressBar(self)
        self.overall_bar.setRange(0, max(self._total, 1))
        self.overall_bar.setValue(0)
        self.overall_bar.setFormat(self.tr("File %v of %m"))
        layout.addWidget(self.overall_bar)

        self.byte_label = QLabel("", self)
        layout.addWidget(self.byte_label)
        self.file_bar = QProgressBar(self)
        self.file_bar.setRange(0, 100)
        self.file_bar.setValue(0)
        layout.addWidget(self.file_bar)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        button_box.rejected.connect(self._on_cancel_clicked)
        self._cancel_button = button_box.button(QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(button_box)

        self._task = DownloadItemsTask(items, central_repo, download_fn=download_fn)
        self._task.fileStarted.connect(self._on_file_started)
        self._task.fileProgress.connect(self._on_file_progress)
        self._task.finishedWithOutcomes.connect(self._on_finished)

    def start(self) -> None:
        """Hands the task to QGIS's own task manager - separate from
        __init__ so a caller (or a test) can connect additional signals
        before the task actually starts running."""
        QgsApplication.taskManager().addTask(self._task)

    # --------------------------------------------------------------- signal handlers

    def _on_file_started(self, index: int, total: int, filename: str) -> None:
        self.file_label.setText(
            self.tr("File {} of {}: {}").format(index + 1, total, filename)
        )
        self.overall_bar.setValue(index)
        self.file_bar.setRange(0, 100)
        self.file_bar.setValue(0)
        self.byte_label.setText("")

    def _on_file_progress(self, read: int, total: int) -> None:
        if total >= 0:
            pct = int(read * 100 / total) if total > 0 else 100
            self.file_bar.setRange(0, 100)
            self.file_bar.setValue(min(pct, 100))
            self.byte_label.setText(
                self.tr("{:.1f} / {:.1f} MB").format(read / 1e6, total / 1e6)
            )
        else:
            # Unknown total (no Content-Length from the server) - an
            # indeterminate/marquee bar is a more honest representation
            # than a percentage computed against nothing.
            self.file_bar.setRange(0, 0)
            self.byte_label.setText(self.tr("{:.1f} MB").format(read / 1e6))

    def _on_finished(self, outcomes: List[pipeline.DownloadOutcome]) -> None:
        self.outcomes = outcomes
        self.overall_bar.setValue(self._total)
        if self._task.exception is not None:
            from qgis.core import Qgis, QgsMessageLog

            QgsMessageLog.logMessage(
                f"Download task failed unexpectedly: {self._task.exception!r}",
                "SIGate",
                Qgis.MessageLevel.Critical,
            )
            QMessageBox.critical(
                self,
                self.tr("Download failed unexpectedly"),
                self.tr(
                    "The download stopped due to an unexpected error: {}\n\n"
                    "Any files already completed before this are still kept."
                ).format(self._task.exception),
            )
        self.accept()

    def _on_cancel_clicked(self) -> None:
        self.was_cancelled = True
        self._cancel_button.setEnabled(False)
        self.file_label.setText(self.tr("Cancelling..."))
        self._task.cancel()
        # Deliberately do not close the dialog here - wait for
        # _on_finished, which will still fire once run() actually stops
        # (should_continue is checked cooperatively, not preemptively),
        # so the caller gets accurate partial outcomes rather than a
        # dialog that closed before the in-flight file actually stopped.


def run_modal(
    parent: Optional[QWidget],
    items: List[pipeline.DownloadItem],
    central_repo: Path,
    download_fn: Optional[Callable] = None,
) -> Tuple[List[pipeline.DownloadOutcome], bool]:
    """Shows the progress dialog, blocks until the download finishes or
    is cancelled, and returns (outcomes, was_cancelled) - the real
    production implementation of the progress_runner collaborator
    ui.download_flow.run_download_and_add_layers accepts."""
    dialog = DownloadProgressDialog(
        parent, items, central_repo, download_fn=download_fn
    )
    dialog.start()
    dialog.exec()
    return dialog.outcomes, dialog.was_cancelled
