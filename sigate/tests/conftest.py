"""
Session-wide pytest fixtures. Tests that need a running QGIS application
(anything importing qgis.gui and constructing real widgets) depend on the
qgis_app fixture below, which initializes exactly one headless
QgsApplication for the whole test session.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qgis_app():
    from qgis.core import QgsApplication

    app = QgsApplication([], False)
    app.initQgis()
    yield app
    app.exitQgis()
