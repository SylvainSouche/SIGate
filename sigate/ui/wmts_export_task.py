"""
ui.wmts_export_task - runs gateways.wmts_export's ensure_wmts_def +
clip_wmts_area as a real QgsTask, so materializing a clipped GeoTIFF
doesn't block QGIS's UI thread for however long the GDAL CLI tools take
- the same real reason ui.download_task exists for downloads.

Unlike ui.download_task, true cooperative cancellation isn't wired in
here: gateways.wmts_export shells out to GDAL's own CLI tools
(subprocess.run(..., check=True)), which offers no clean, safe way to
interrupt a command already running partway through, short of killing
the process outright - not attempted here. cancel() on this task only
prevents starting the *next* of the two possible GDAL steps if the
first hasn't finished yet; a step already running will run to
completion regardless.

logMessage is emitted for both GDAL commands this task issues (each
already logged to QgsMessageLog's "SIGate" tab too, from within this
same emission, matching the convention established for downloads/
archive extraction/mosaic building) - a real Qt signal, not a plain
callback invoked directly from the worker thread, since a caller (the
owning dialog) needs to update a widget from it, which is only safe via
Qt's own cross-thread signal delivery.
"""

from typing import List, Optional, Tuple

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from sigate.gateways import wmts_export


class WmtsExportTask(QgsTask):
    finishedExport = pyqtSignal()
    logMessage = pyqtSignal(str)

    def __init__(
        self,
        def_path: str,
        caps_url: str,
        gdal_config: List[str],
        max_connections: int,
        layer: str,
        tilematrixset: str,
        style: Optional[str],
        bbox: Tuple[float, float, float, float],
        out_path: str,
        bbox_crs: Optional[str] = None,
        tilematrix: Optional[str] = None,
        creation_options: Optional[List[str]] = None,
        tmp_dir: Optional[str] = None,
    ) -> None:
        super().__init__("SIGate WMTS export", QgsTask.Flag.CanCancel)
        self._def_path = def_path
        self._caps_url = caps_url
        self._gdal_config = gdal_config
        self._max_connections = max_connections
        self._layer = layer
        self._tilematrixset = tilematrixset
        self._style = style
        self._bbox = bbox
        self._bbox_crs = bbox_crs
        self._tilematrix = tilematrix
        self._out_path = out_path
        self._creation_options = creation_options
        self._tmp_dir = tmp_dir
        self.exception: Optional[Exception] = None

    def _log(self, message: str) -> None:
        self.logMessage.emit(message)
        from qgis.core import Qgis, QgsMessageLog

        QgsMessageLog.logMessage(message, "SIGate", Qgis.MessageLevel.Info)

    def run(self) -> bool:
        try:
            source = wmts_export.ensure_wmts_def(
                self._def_path,
                self._caps_url,
                self._gdal_config,
                self._max_connections,
                self._layer,
                self._tilematrixset,
                style=self._style,
                tilematrix=self._tilematrix,
                log=self._log,
            )
            if self.isCanceled():
                return False
            wmts_export.clip_wmts_area(
                source,
                self._bbox,
                self._out_path,
                bbox_crs=self._bbox_crs,
                creation_options=self._creation_options,
                tmp_dir=self._tmp_dir,
                gdal_config=self._gdal_config,
                log=self._log,
            )
        except Exception as e:  # noqa: BLE001 - reported via .exception, not raised here
            self.exception = e
            return False
        return True

    def finished(self, result: bool) -> None:
        # Called on the main thread by QGIS's task manager once run()
        # returns - safe to touch Qt widgets from a slot connected to
        # this signal, unlike anything called directly from run() itself.
        self.finishedExport.emit()
