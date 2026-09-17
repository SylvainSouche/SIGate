"""
ui.wmts_export_dialog - a modal "Exporting..." popup for the WM(T)S
tab's clip-to-GeoTIFF export, backed by ui.wmts_export_task's
WmtsExportTask running the actual GDAL commands in the background.

Deliberately simpler than ui.download_progress_dialog's two bars: GDAL's
CLI tools don't expose per-byte progress the way a streamed HTTP
download does, so this shows a single indeterminate/marquee bar plus a
status label naming whichever command last started, rather than a
fabricated percentage.
"""

from typing import Optional, Tuple

from qgis.core import QgsApplication
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from .wmts_export_task import WmtsExportTask


class WmtsExportDialog(QDialog):
    def __init__(self, parent: Optional[QWidget], **task_kwargs) -> None:
        """task_kwargs is forwarded directly to WmtsExportTask's own
        constructor - see that class for the full set of required
        keyword arguments (def_path, caps_url, gdal_config,
        max_connections, layer, tilematrixset, style, bbox, out_path,
        and the optional bbox_crs/creation_options/tmp_dir)."""
        super().__init__(parent)
        self.setWindowTitle(self.tr("Exporting clipped area"))
        self.setModal(True)
        self.setMinimumWidth(440)

        self.succeeded = False
        self.exception: Optional[Exception] = None
        self.was_cancelled = False

        layout = QVBoxLayout(self)
        self.status_label = QLabel(self.tr("Preparing..."), self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 0)  # indeterminate - see module docstring
        layout.addWidget(self.progress_bar)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        button_box.rejected.connect(self._on_cancel_clicked)
        self._cancel_button = button_box.button(QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(button_box)

        self._task = WmtsExportTask(**task_kwargs)
        self._task.logMessage.connect(self.status_label.setText)
        self._task.finishedExport.connect(self._on_finished)

    def start(self) -> None:
        """Hands the task to QGIS's own task manager, actually starting
        it - separate from __init__ so a caller can connect additional
        signals first if needed."""
        QgsApplication.taskManager().addTask(self._task)

    def _on_finished(self) -> None:
        self.succeeded = self._task.exception is None
        self.exception = self._task.exception
        self.accept()

    def _on_cancel_clicked(self) -> None:
        self.was_cancelled = True
        self._cancel_button.setEnabled(False)
        self.status_label.setText(
            self.tr(
                "Cancelling - a GDAL command already in progress will still "
                "run to completion; nothing further will start after it."
            )
        )
        self._task.cancel()


def run_modal(
    parent: Optional[QWidget], **task_kwargs
) -> Tuple[bool, Optional[Exception], bool]:
    """Shows the export dialog, blocks until it finishes or is
    cancelled.

    Args:
        parent: the owning widget, or None.
        **task_kwargs: forwarded to WmtsExportTask - see
            WmtsExportDialog.__init__ for the full required set.

    Returns:
        (succeeded, exception, was_cancelled). exception is the real
        raised exception on failure, not just its string form; None on
        success. was_cancelled can be True together with succeeded=False
        (cancelled before any file was produced) - callers that need to
        distinguish "cancelled" from "genuinely failed" should check
        was_cancelled first.
    """
    dialog = WmtsExportDialog(parent, **task_kwargs)
    dialog.start()
    dialog.exec()
    return dialog.succeeded, dialog.exception, dialog.was_cancelled
