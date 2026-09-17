"""
SIGate - browse and download geospatial data from national mapping
agencies directly within QGIS's Data Source Manager.
"""


def classFactory(iface):
    from .plugin import SigatePlugin

    return SigatePlugin(iface)
