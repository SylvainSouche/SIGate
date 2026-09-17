"""
ui.authcfg - bridges SIGate's own AuthConfig model (sources.store) to
QGIS's real Authentication Configuration database, for gateway types
that hand off to a native QGIS data provider rather than fetching data
themselves.

Why this exists: the WM(T)S tab's final action is a handoff to QGIS's
own native "wms" data provider (see docs/spec.md §3) - SIGate builds a
connection string, but QGIS's own provider makes the actual HTTP
request. That means SIGate cannot inject a custom HTTP header into that
request directly; QGIS has no connection-string parameter for "add this
header". The only real mechanism QGIS's own providers support for this
is a stored Authentication Configuration ("authcfg") of method
"APIHeader", referenced by id in the connection string
(QgsDataSourceUri.setParam("authcfg", authcfg_id)) - QGIS's own provider
expands it into the real header just before the request. This module
provisions that authcfg from SIGate's own AuthConfig, so the rest of the
plugin (gateways.wmts_wms.build_qgis_wms_uri) only ever deals with a
plain string id, never the credential itself.

Idempotency matters here: this runs every time the WM(T)S tab loads a
connection, not once at setup. A deterministic authcfg id (derived from
the source key and gateway role via a stable hash - see
authcfg_id_for's own docstring for why it's a hash rather than a
human-readable concatenation) is reused across reloads instead of
creating a fresh, duplicate authcfg entry in the user's auth database on
every single tab load. The human-readable name QGIS's Auth Manager
actually displays ("SIGate - ign_fr (private)", set via setName() below)
is a completely separate field with no format restriction - only the id
itself has to fit QGIS's strict 7-character format.

A second, more fundamental correction, found directly from the user
observing the actual HTTP request: the "headerkey"/"headervalue" fix
above was itself still wrong, in a more basic way than just which key
names to use. QGIS's real APIHeader method doesn't use an indirection
scheme at all - it treats each stored config key as a literal HTTP
header NAME to send, with that key's own value as the header's value.
Storing "headerkey" -> "apikey" and "headervalue" -> "ign_scan_ws" (two
config entries, neither one a real header name) produced exactly what
was reported: two garbage HTTP headers literally named "headerkey" and
"headervalue" being sent, instead of the one real header actually
needed ("apikey: ign_scan_ws"). The real, correct call is
setConfig(header_name, header_value) directly - the header's own real
name IS the config key, not a fixed, generic key pointing at it.

Still genuinely unconfirmed, not yet ruled out even with the above fixed:
whether authcfg expansion is actually wired through for the "wms"
provider with this specific method - not every QGIS data provider
correctly wires authcfg expansion through for every auth method
(confirmed as a real bug for at least one other provider, in QGIS's own
issue tracker), so a live end-to-end request succeeding is still the
real confirmation this needs, not just the stored config no longer
showing red in the Auth Manager.
"""

import hashlib
from typing import Optional

# QGIS enforces a strict, documented format for authcfg ids: exactly 7
# characters, lowercase alphanumeric only (QGIS's own AUTHCFG_REGEX,
# ^([a-z0-9]{7})$ - confirmed directly against QGIS's own User Manual,
# QgsAuthMethodConfig/QgsAuthManager API docs, and enforced in its own
# source at both the UI layer (qgsauthconfigidedit.cpp, checking
# size()==7 and isAlphaNumeric()) and the auth manager itself
# (qgsauthmanager.cpp, matching AUTHCFG_REGEX when parsing authcfg=
# tokens out of a data source URI). This module's own id scheme
# originally ignored this entirely - a real, confirmed bug: a 21-
# character, underscore-containing id like "sigate_ign_fr_private"
# doesn't match the required pattern at all, meaning QGIS's own parsing
# of an authcfg= token built from it could fail outright, independent
# of (and likely a more fundamental problem than) the "headerkey" fix
# elsewhere in this module.
_AUTHCFG_ID_LENGTH = 7


def authcfg_id_for(source_key: str, gateway_role: str) -> str:
    """The deterministic authcfg id this source/gateway pairing always
    uses, so repeated calls find and reuse the same entry rather than
    creating a new one each time (see this module's own docstring on
    why that determinism matters).

    A stable hash (not source_key/gateway_role concatenated directly,
    which produced this module's own real, confirmed id-format bug -
    see the comment on _AUTHCFG_ID_LENGTH) truncated to the first 7 hex
    characters - hex digits (0-9a-f) are already a strict subset of
    QGIS's required [a-z0-9] alphabet, so no further transformation is
    needed to satisfy the format. Collisions between two different
    (source_key, gateway_role) pairs are theoretically possible (7 hex
    characters is a real, finite space, ~268 million values) but not a
    practical concern for the small, curated set of sources/gateways
    this plugin actually ships - not worth a collision-detection
    mechanism for that."""
    digest = hashlib.sha256(f"{source_key}:{gateway_role}".encode("utf-8")).hexdigest()
    return digest[:_AUTHCFG_ID_LENGTH]


def ensure_authcfg_for_gateway(
    source_key: str,
    gateway_role: str,
    auth_config,
    auth_manager=None,
    config_factory=None,
) -> Optional[str]:
    """Ensures a QGIS Authentication Configuration exists for the given
    AuthConfig (sources.store.AuthConfig) and returns its id, or None if
    no authentication is required (auth_config.kind == "none") or the
    kind isn't one this function knows how to provision.

    auth_manager and config_factory are injectable for testing without a
    live QGIS auth database - the real caller leaves both as None to use
    QgsApplication.authManager() and QgsAuthMethodConfig respectively;
    tests supply plain fakes instead, the same injected-collaborator
    pattern gateways/ already uses for its fetch function.
    """
    if auth_config is None or auth_config.kind == "none":
        return None
    if not source_key or not gateway_role:
        raise ValueError("source_key and gateway_role must both be non-empty")
    if auth_config.kind != "apikey_header":
        # "token" (fetched/refreshed) auth isn't a static QGIS authcfg
        # shape and isn't handled here yet.
        return None

    if auth_manager is None:
        from qgis.core import QgsApplication

        auth_manager = QgsApplication.authManager()
    if config_factory is None:
        from qgis.core import QgsAuthMethodConfig as config_factory

    cfg_id = authcfg_id_for(source_key, gateway_role)

    existing = config_factory()
    auth_manager.loadAuthenticationConfig(cfg_id, existing, True)
    if existing.id():
        # Already provisioned (from an earlier tab load, or a previous
        # QGIS session) - reuse it rather than creating a duplicate.
        # Refresh its stored value in case the user edited the source
        # config's apikey value since it was first provisioned.
        # Not defensively clearing any previous key here: this fix
        # shipped alongside authcfg_id_for's own id-format correction,
        # which means every existing user's next call already lands on
        # a brand new id their old (buggy) entry was never stored
        # under - the "reuse" branch below simply won't be reached for
        # any config still carrying the old scheme's stale keys, since
        # nothing was ever provisioned under the new hash-based id yet.
        # A header NAME changing on an already-correctly-provisioned
        # config is a separate, much rarer case this doesn't handle
        # (source configs aren't user-editable in the current UI, so
        # header_name itself effectively never changes in practice -
        # only the credential value does, which this does handle
        # correctly by just overwriting the same key each time).
        existing.setConfig(auth_config.header_name or "", auth_config.value or "")
        auth_manager.updateAuthenticationConfig(existing)
        return cfg_id

    new_config = config_factory()
    new_config.setId(cfg_id)
    new_config.setName(f"SIGate - {source_key} ({gateway_role})")
    new_config.setMethod("APIHeader")
    new_config.setConfig(auth_config.header_name or "", auth_config.value or "")
    if not new_config.isValid():
        return None
    auth_manager.storeAuthenticationConfig(new_config)
    return cfg_id
