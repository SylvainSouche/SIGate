"""
ui.wmts_zoom_level_dialog - lets the user pick among a WMTS layer's real
zoom levels for "Export clipped area as GeoTIFF", showing each level's
own resolution and an estimated output size for the current view. Only
shown when more than one real zoom level actually exists for the
selected layer's tilematrixset
(gateways.wmts_wms.WmtsTileMatrixSetInfo.levels) - a tilematrixset with
a single level (or none parseable) skips this dialog entirely and falls
back to the simpler size-warning-threshold check already in place for
that case.

Choosing a level here does more than change a size estimate: the chosen
identifier is passed through as GDAL's own tilematrix= connection-string
parameter (gateways.wmts_export.wmts_source_string), which genuinely
constrains what GDAL fetches - not merely a display estimate followed by
downsampling the same full-resolution fetch afterward.
"""

from typing import List, Optional, Tuple

from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from sigate.gateways.wmts_export import estimate_export_size
from sigate.gateways.wmts_wms import WmtsTileMatrixLevel


class ZoomLevelChoiceDialog(QDialog):
    def __init__(
        self,
        parent: Optional[QWidget],
        levels: List[WmtsTileMatrixLevel],
        bbox: Tuple[float, float, float, float],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Choose export resolution"))
        self.setModal(True)
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                self.tr(
                    "This layer offers more than one zoom level for the "
                    "current view. Pick one - finer levels produce larger, "
                    "more detailed files."
                ),
                self,
            )
        )

        self._combo = QComboBox(self)
        # Coarsest first, finest last - so the default selection (finest)
        # is the last entry, and scanning top-to-bottom shows size
        # growing in the expected direction.
        for level in sorted(levels, key=lambda lv: -lv.resolution):
            estimate = estimate_export_size(bbox, level.resolution)
            self._combo.addItem(
                self.tr(
                    "{} \u2014 {:.3g} units/px \u2014 {} x {} px \u2014 "
                    "~{:.2f} GB uncompressed"
                ).format(
                    level.identifier,
                    level.resolution,
                    estimate.width_px,
                    estimate.height_px,
                    estimate.raw_bytes / 1024**3,
                ),
                level.identifier,
            )
        if self._combo.count():
            self._combo.setCurrentIndex(self._combo.count() - 1)  # finest, by default
        layout.addWidget(self._combo)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def chosen_identifier(self) -> Optional[str]:
        return self._combo.currentData()


def choose_zoom_level(
    parent: Optional[QWidget],
    levels: List[WmtsTileMatrixLevel],
    bbox: Tuple[float, float, float, float],
) -> Optional[str]:
    """Shows the picker and returns the chosen level's own TileMatrix
    identifier, or None if the user cancelled."""
    dialog = ZoomLevelChoiceDialog(parent, levels, bbox)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.chosen_identifier()
