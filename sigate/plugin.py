"""
plugin - the main SIGate plugin class: registers and unregisters SIGate's
Data Source Manager categories on plugin load and unload.
"""

from qgis.gui import QgsGui

from .ui.bulk_listing_provider import BulkListingSourceSelectProvider
from .ui.wfs_provider import WfsSourceSelectProvider
from .ui.wmts_wms_provider import WmtsWmsSourceSelectProvider


class SigatePlugin:
    def __init__(self, iface):
        self.iface = iface
        self._providers = []

    def initGui(self):
        registry = QgsGui.sourceSelectProviderRegistry()
        for provider in (
            WmtsWmsSourceSelectProvider(),
            BulkListingSourceSelectProvider(),
            WfsSourceSelectProvider(),
        ):
            registry.addProvider(provider)
            self._providers.append(provider)

    def unload(self):
        registry = QgsGui.sourceSelectProviderRegistry()
        for provider in self._providers:
            registry.removeProvider(provider)
        self._providers = []
