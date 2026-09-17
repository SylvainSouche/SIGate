"""
ui.wmts_wms_provider - registers SIGate's WM(T)S category in QGIS's Data
Source Manager.
"""

from qgis.gui import QgsAbstractDataSourceWidget, QgsSourceSelectProvider
from qgis.PyQt.QtGui import QIcon

from .icons import plugin_icon
from .wmts_wms_widget import WmtsWmsSourceSelectWidget

PROVIDER_KEY = "sigate_wmts_wms"


class WmtsWmsSourceSelectProvider(QgsSourceSelectProvider):
    def providerKey(self) -> str:
        return PROVIDER_KEY

    def text(self) -> str:
        return "SIGate WMS/WMTS"

    def toolTip(self) -> str:
        return "Browse configured WMS/WMTS layers and add them to the map"

    def icon(self) -> QIcon:
        return plugin_icon()

    def ordering(self) -> int:
        return 30000

    def createDataSourceWidget(
        self, parent, fl, widget_mode
    ) -> QgsAbstractDataSourceWidget:
        return WmtsWmsSourceSelectWidget(parent, fl, widget_mode)
