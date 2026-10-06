"""
ui.product_choice_dialog - asks which of a feature row's several
download links to fetch.

Some WFS file-index layers carry more than one download link per feature
(IGN's LiDAR HD metadata layer: url_mnt, url_mns, url_mnh and url_npl -
terrain, surface and canopy-height rasters plus the COPC point cloud),
which differ hugely in size, so the choice is the user's, not a default.
"""

from typing import Dict, List, Optional, Set

from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class ProductChoiceDialog(QDialog):
    def __init__(
        self,
        parent: Optional[QWidget],
        products: Dict[str, str],
        preselected: Set[str],
        feature_count: int,
    ) -> None:
        """products maps each url field name to a human label."""
        super().__init__(parent)
        self.setWindowTitle(self.tr("Choose what to download"))
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                self.tr(
                    "Each of the {} feature(s) carries several download links. "
                    "Tick the products to fetch for every feature - point "
                    "clouds are far larger than the rasters."
                ).format(feature_count),
                self,
            )
        )
        self._boxes: Dict[str, QCheckBox] = {}
        for field, label in products.items():
            box = QCheckBox(f"{label}  ({field})", self)
            box.setChecked(field in preselected)
            self._boxes[field] = box
            layout.addWidget(box)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    def chosen_fields(self) -> List[str]:
        return [field for field, box in self._boxes.items() if box.isChecked()]
