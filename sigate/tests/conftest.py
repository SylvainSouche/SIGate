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


@pytest.fixture(autouse=True)
def reset_country_filter(request):
    """The connection managers remember the last country picked, in the
    real QGIS settings - without a reset, one test's pick would filter
    every later widget's connection list. Only for tests that already
    run a QgsApplication (QgsSettings needs one)."""
    if "qgis_app" in request.fixturenames:
        request.getfixturevalue("qgis_app")
        from sigate.ui import settings as sigate_settings

        sigate_settings.set_last_country_filter(None)
        sigate_settings.set_last_organisation_filter(None)
    yield
