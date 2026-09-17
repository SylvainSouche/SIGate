"""
Tests for sigate.ui.authcfg - the AuthConfig-to-QGIS-authcfg provisioning
logic. Uses fake auth_manager/config_factory collaborators (the same
injected-collaborator pattern gateways/ uses for its fetch function), so
this runs without a live QGIS auth database.
"""

import pytest

from sigate.sources.store import AuthConfig
from sigate.ui import authcfg


class FakeAuthMethodConfig:
    """Stands in for QgsAuthMethodConfig: a plain config-key map plus the
    handful of methods ensure_authcfg_for_gateway actually calls."""

    def __init__(self):
        self._id = None
        self._name = None
        self._method = None
        self._config = {}

    def id(self):
        return self._id

    def setId(self, value):
        self._id = value

    def setName(self, value):
        self._name = value

    def setMethod(self, value):
        self._method = value

    def setConfig(self, key, value):
        self._config[key] = value

    def isValid(self):
        # Real QGIS validity rules are method-specific; here, "has both
        # a method and at least one config value set" is a close enough
        # stand-in to exercise the isValid()-checking branch.
        return self._method is not None and bool(self._config)


class FakeAuthManager:
    """Stands in for QgsApplication.authManager(): a plain dict-backed
    store keyed by authcfg id, matching the load/store/update methods
    ensure_authcfg_for_gateway actually calls."""

    def __init__(self):
        self._store = {}

    def loadAuthenticationConfig(self, cfg_id, mconfig, full):
        stored = self._store.get(cfg_id)
        if stored is not None:
            mconfig._id = stored._id
            mconfig._name = stored._name
            mconfig._method = stored._method
            mconfig._config = dict(stored._config)

    def storeAuthenticationConfig(self, config):
        self._store[config.id()] = config

    def updateAuthenticationConfig(self, config):
        self._store[config.id()] = config


def test_authcfg_id_for_is_deterministic():
    first = authcfg.authcfg_id_for("ign_fr", "private")
    second = authcfg.authcfg_id_for("ign_fr", "private")
    assert first == second


def test_authcfg_id_for_produces_a_real_valid_qgis_authcfg_id():
    """Regression test for a real, confirmed bug: QGIS enforces a
    strict, documented format for authcfg ids - exactly 7 characters,
    lowercase alphanumeric only (QGIS's own AUTHCFG_REGEX,
    ^([a-z0-9]{7})$, confirmed directly against QGIS's own User Manual,
    API docs, and source code). This module's original id scheme
    ("sigate_" + source_key + "_" + gateway_role) ignored this entirely
    - a real 21-character, underscore-containing id like
    "sigate_ign_fr_private" doesn't match the required pattern at all."""
    import re

    cfg_id = authcfg.authcfg_id_for("ign_fr", "private")
    assert re.fullmatch(r"[a-z0-9]{7}", cfg_id), (
        f"{cfg_id!r} does not match QGIS's own required authcfg id format"
    )


def test_authcfg_id_for_differs_between_different_source_gateway_pairs():
    ign_private = authcfg.authcfg_id_for("ign_fr", "private")
    ign_public = authcfg.authcfg_id_for("ign_fr", "public")
    swisstopo_private = authcfg.authcfg_id_for("swisstopo_ch", "private")
    assert len({ign_private, ign_public, swisstopo_private}) == 3


def test_ensure_authcfg_returns_none_for_no_auth():
    manager = FakeAuthManager()
    result = authcfg.ensure_authcfg_for_gateway(
        "ign_fr",
        "public",
        AuthConfig(kind="none"),
        auth_manager=manager,
        config_factory=FakeAuthMethodConfig,
    )
    assert result is None
    assert manager._store == {}


def test_ensure_authcfg_returns_none_for_unhandled_kind():
    manager = FakeAuthManager()
    result = authcfg.ensure_authcfg_for_gateway(
        "ign_fr",
        "private",
        AuthConfig(kind="token"),
        auth_manager=manager,
        config_factory=FakeAuthMethodConfig,
    )
    assert result is None


def test_ensure_authcfg_creates_new_config_with_correct_method_and_header():
    """header_name="apikey", value="ign_scan_ws" here isn't an arbitrary
    example - it's IGN's own real, documented transitional key for
    their private WMTS (data.geopf.fr/private/wmts), confirmed real
    both from public IGN community discussion and, later, directly from
    a real working QGIS-native config compared side by side against
    this project's own provisioned one.

    That comparison first led to a "headerkey"/"headervalue" fix (an
    indirection scheme), which itself turned out to still be wrong - a
    real reported symptom (two literal garbage HTTP headers, "headerkey:
    apikey" and "headervalue: ign_scan_ws", instead of the one real
    header actually needed) showed QGIS's real APIHeader method has no
    indirection at all: it treats each stored config key as a literal
    header name to send. This test checks the actual, final, corrected
    shape - the header's own real name is the config key directly."""
    manager = FakeAuthManager()
    auth_config = AuthConfig(
        kind="apikey_header", header_name="apikey", value="ign_scan_ws"
    )
    cfg_id = authcfg.ensure_authcfg_for_gateway(
        "ign_fr",
        "private",
        auth_config,
        auth_manager=manager,
        config_factory=FakeAuthMethodConfig,
    )
    assert cfg_id == authcfg.authcfg_id_for("ign_fr", "private")
    stored = manager._store[cfg_id]
    assert stored._method == "APIHeader"
    assert stored._config == {"apikey": "ign_scan_ws"}


def test_ensure_authcfg_reuses_existing_entry_rather_than_duplicating():
    manager = FakeAuthManager()
    auth_config = AuthConfig(
        kind="apikey_header", header_name="apikey", value="ign_scan_ws"
    )
    first_id = authcfg.ensure_authcfg_for_gateway(
        "ign_fr",
        "private",
        auth_config,
        auth_manager=manager,
        config_factory=FakeAuthMethodConfig,
    )
    assert len(manager._store) == 1
    second_id = authcfg.ensure_authcfg_for_gateway(
        "ign_fr",
        "private",
        auth_config,
        auth_manager=manager,
        config_factory=FakeAuthMethodConfig,
    )
    assert second_id == first_id
    assert len(manager._store) == 1  # no duplicate entry created


def test_ensure_authcfg_refreshes_value_on_existing_entry_if_changed():
    manager = FakeAuthManager()
    original = AuthConfig(kind="apikey_header", header_name="apikey", value="old_key")
    cfg_id = authcfg.ensure_authcfg_for_gateway(
        "ign_fr",
        "private",
        original,
        auth_manager=manager,
        config_factory=FakeAuthMethodConfig,
    )
    updated = AuthConfig(kind="apikey_header", header_name="apikey", value="new_key")
    authcfg.ensure_authcfg_for_gateway(
        "ign_fr",
        "private",
        updated,
        auth_manager=manager,
        config_factory=FakeAuthMethodConfig,
    )
    assert manager._store[cfg_id]._config["apikey"] == "new_key"


def test_ensure_authcfg_rejects_empty_source_key():
    manager = FakeAuthManager()
    with pytest.raises(ValueError):
        authcfg.ensure_authcfg_for_gateway(
            "",
            "public",
            AuthConfig(kind="apikey_header", header_name="apikey", value="k"),
            auth_manager=manager,
            config_factory=FakeAuthMethodConfig,
        )


def test_ensure_authcfg_rejects_empty_gateway_role():
    manager = FakeAuthManager()
    with pytest.raises(ValueError):
        authcfg.ensure_authcfg_for_gateway(
            "ign_fr",
            "",
            AuthConfig(kind="apikey_header", header_name="apikey", value="k"),
            auth_manager=manager,
            config_factory=FakeAuthMethodConfig,
        )
