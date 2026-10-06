"""
ui.arcgis_rest_provider - registers SIGate's ArcGIS REST category in
QGIS's Data Source Manager.
"""

from qgis.gui import QgsAbstractDataSourceWidget, QgsSourceSelectProvider
from qgis.PyQt.QtGui import QIcon

from .arcgis_rest_widget import ArcGisRestSourceSelectWidget
from .icons import plugin_icon

PROVIDER_KEY = "sigate_arcgis_rest"


class ArcGisRestSourceSelectProvider(QgsSourceSelectProvider):
    def providerKey(self) -> str:
        return PROVIDER_KEY

    def text(self) -> str:
        return "SIGate ArcGIS REST"

    def toolTip(self) -> str:
        return "Browse ArcGIS REST service layers, filter them and add them to the map"

    def icon(self) -> QIcon:
        return plugin_icon()

    def ordering(self) -> int:
        return 30003

    def createDataSourceWidget(
        self, parent, fl, widget_mode
    ) -> QgsAbstractDataSourceWidget:
        return ArcGisRestSourceSelectWidget(parent, fl, widget_mode)
