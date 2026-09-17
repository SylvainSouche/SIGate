"""
ui.wfs_provider - registers SIGate's WFS category in QGIS's Data Source
Manager.
"""

from qgis.gui import QgsAbstractDataSourceWidget, QgsSourceSelectProvider
from qgis.PyQt.QtGui import QIcon

from .icons import plugin_icon
from .wfs_widget import WfsSourceSelectWidget

PROVIDER_KEY = "sigate_wfs"


class WfsSourceSelectProvider(QgsSourceSelectProvider):
    def providerKey(self) -> str:
        return PROVIDER_KEY

    def text(self) -> str:
        return "SIGate WFS"

    def toolTip(self) -> str:
        return "Browse WFS feature types and add them to the map"

    def icon(self) -> QIcon:
        return plugin_icon()

    def ordering(self) -> int:
        return 30002

    def createDataSourceWidget(
        self, parent, fl, widget_mode
    ) -> QgsAbstractDataSourceWidget:
        return WfsSourceSelectWidget(parent, fl, widget_mode)
