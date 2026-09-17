"""
ui.bulk_listing_provider - registers SIGate's Bulk Listing category in
QGIS's Data Source Manager.
"""

from qgis.gui import QgsAbstractDataSourceWidget, QgsSourceSelectProvider
from qgis.PyQt.QtGui import QIcon

from .bulk_listing_widget import BulkListingSourceSelectWidget
from .icons import plugin_icon

PROVIDER_KEY = "sigate_bulk_listing"


class BulkListingSourceSelectProvider(QgsSourceSelectProvider):
    def providerKey(self) -> str:
        return PROVIDER_KEY

    def text(self) -> str:
        return "SIGate Bulk Download"

    def toolTip(self) -> str:
        return "Browse and download bulk data archives"

    def icon(self) -> QIcon:
        return plugin_icon()

    def ordering(self) -> int:
        return 30001

    def createDataSourceWidget(
        self, parent, fl, widget_mode
    ) -> QgsAbstractDataSourceWidget:
        return BulkListingSourceSelectWidget(parent, fl, widget_mode)
