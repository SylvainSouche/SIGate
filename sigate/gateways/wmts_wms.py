"""
gateways.wmts_wms - discovering real layers from a WMTS server's own
GetCapabilities response, and connection-string construction for QGIS's
native "wms" data provider (which handles both WMS and WMTS through the
same URI format).

This gateway does not implement its own tile fetching or rendering: once
a layer is picked, QGIS's own native provider does the actual work of
serving tiles, since reimplementing that would only risk repeating
mistakes a client-side assumption can make (guessing an incorrect tile
matrix set identifier, or omitting a style parameter a layer actually
requires - both real mistakes made earlier in this project before this
was corrected). That does not extend to layer *discovery*, though - this
module does fetch and parse the server's real Contents section, so the
list of layers offered to a user reflects what the server actually
serves, not a fixed example baked into configuration.

Providing tile_matrix_set makes the resulting connection a WMTS one;
omitting it makes it a plain WMS connection.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode
from xml.etree import ElementTree as ET

from .atom import http_get as _default_fetch
from .base import GatewayCapabilities, urn_to_epsg
from .ogc_constants import (
    PARAM_LANG,
    PARAM_REQUEST,
    PARAM_SERVICE,
    PARAM_VERSION,
    REQUEST_GET_CAPABILITIES,
)

CAPABILITIES = GatewayCapabilities(
    supports_query=False,
    extent_flavor="coordinate",
    # Confirmed real on geo.admin.ch's own WMS and WMTS docs: ?lang=
    # (de/fr/it/en) is honoured on every request type, giving properly
    # translated layer titles/descriptions directly from the source -
    # no client-side translation needed at all for a source that
    # actually supports this. First gateway module to flip this to True
    # (see build_capabilities_url/wmts_get_capabilities's own lang
    # parameter) - the flag existed in GatewayCapabilities since this
    # project's base module was first written, but nothing had wired it
    # through to a real request until now.
    supports_locale=True,
    is_file_index=False,
)

_WMTS_VERSION = "1.0.0"
_SERVICE_WMTS = "WMTS"


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


# urn_to_epsg is imported above from gateways.base (moved there so
# gateways.wfs can reuse it too) - re-exported here since existing
# code/tests may still reference it as gateways.wmts_wms.urn_to_epsg.


_OGC_PIXEL_SIZE_METERS = 0.00028
"""OGC's standardized pixel-size assumption (WMTS/WMS spec), used to
convert a TileMatrix's own ScaleDenominator into a real ground/map-unit
resolution: resolution = scale_denominator * 0.00028. Applies uniformly
regardless of the tile matrix set's own CRS units (a projected, meter-
based CRS or a geographic, degree-based one both use this same
constant - it is the spec's own standardized assumption, not something
this module infers or varies per CRS)."""


@dataclass(frozen=True)
class WmtsTileMatrixLevel:
    identifier: str
    """The TileMatrix's own Identifier - passed directly as GDAL's
    tilematrix= connection-string parameter to select this exact level,
    since it is the server's own real, unambiguous identifier (unlike
    GDAL's separate zoom_level=, a 0-based index into GDAL's own ordered
    view of the levels, which is not guaranteed to line up with a
    tilematrixset's actual Identifier strings)."""
    resolution: float
    """Map units per pixel at this level (this tile matrix set's own CRS
    units), via OGC's standardized ScaleDenominator*0.28mm formula."""


@dataclass(frozen=True)
class WmtsTileMatrixSetInfo:
    identifier: str
    crs: str
    levels: List[WmtsTileMatrixLevel] = field(default_factory=list)
    """Every real TileMatrix level this tile matrix set declares, in
    whatever order the server's own GetCapabilities listed them (not
    necessarily coarsest-to-finest or vice versa) - empty if the
    server's Contents section had no usable TileMatrix/ScaleDenominator
    entries for it."""
    finest_resolution: Optional[float] = None
    """The smallest resolution (map units per pixel) among `levels` -
    None if `levels` is empty. This is the resolution GDAL's own WMTS
    driver uses by default when no further zoom-level selection is
    specified (as this project's own export path did not, before a
    specific level could be chosen), so it is a genuine, not just
    conservative, estimate of what an unconstrained clip produces."""


@dataclass(frozen=True)
class WmtsLayerInfo:
    identifier: str
    title: str
    styles: List[str] = field(default_factory=list)
    tilematrixset_links: List[str] = field(default_factory=list)
    formats: List[str] = field(default_factory=list)

    @property
    def default_style(self) -> str:
        return self.styles[0] if self.styles else ""

    @property
    def default_tilematrixset(self) -> Optional[str]:
        return self.tilematrixset_links[0] if self.tilematrixset_links else None

    @property
    def default_format(self) -> str:
        return self.formats[0] if self.formats else "image/png"


def wmts_get_capabilities(
    base_url: str, fetch=None, lang: Optional[str] = None
) -> Tuple[List[WmtsLayerInfo], Dict[str, WmtsTileMatrixSetInfo]]:
    """Fetches and parses a real WMTS GetCapabilities response: every
    layer the server actually offers (with the style/tile-matrix-set/
    format options it actually declares), plus every tile matrix set
    definition (so a layer's declared tile-matrix-set can be resolved to
    its real CRS, rather than assumed). Namespace-agnostic, matching the
    same approach used throughout this project's other gateways, since
    real servers vary in which prefix they bind to the WMTS/OWS
    namespaces.

    lang, when given, is passed straight through to the request (see
    build_capabilities_url) - real, human-translated layer titles
    directly from the source for a server that honours it, rather than
    the client-side translation-memory mechanism this project also has
    (ui.translation) for sources with no native multi-language support
    of their own. Not every WMTS server recognizes this parameter; an
    unrecognized one is normally just ignored, so passing it
    unconditionally is safe even against a server that doesn't."""
    fetch = fetch or _default_fetch
    url = build_capabilities_url(base_url, lang=lang)
    raw = fetch(url)
    root = ET.fromstring(raw)

    contents = next((el for el in root if _local(el.tag) == "Contents"), None)
    if contents is None:
        return [], {}

    tilematrixsets: Dict[str, WmtsTileMatrixSetInfo] = {}
    layers: List[WmtsLayerInfo] = []

    for child in contents:
        local = _local(child.tag)
        if local == "TileMatrixSet":
            identifier = None
            crs = None
            levels: List[WmtsTileMatrixLevel] = []
            for sub in child:
                sub_local = _local(sub.tag)
                if sub_local == "Identifier" and identifier is None:
                    identifier = _text(sub)
                elif sub_local == "SupportedCRS" and crs is None:
                    text = _text(sub)
                    if text:
                        crs = urn_to_epsg(text)
                elif sub_local == "TileMatrix":
                    level_id_el = next(
                        (s for s in sub if _local(s.tag) == "Identifier"), None
                    )
                    scale_el = next(
                        (s for s in sub if _local(s.tag) == "ScaleDenominator"), None
                    )
                    level_id = _text(level_id_el)
                    scale_text = _text(scale_el)
                    if not level_id or not scale_text:
                        continue
                    try:
                        resolution = float(scale_text) * _OGC_PIXEL_SIZE_METERS
                    except ValueError:
                        continue
                    levels.append(
                        WmtsTileMatrixLevel(identifier=level_id, resolution=resolution)
                    )
            if identifier:
                finest_resolution = (
                    min(level.resolution for level in levels) if levels else None
                )
                tilematrixsets[identifier] = WmtsTileMatrixSetInfo(
                    identifier=identifier,
                    crs=crs or "EPSG:3857",
                    levels=levels,
                    finest_resolution=finest_resolution,
                )
        elif local == "Layer":
            identifier = None
            title = None
            styles: List[str] = []
            tms_links: List[str] = []
            formats: List[str] = []
            for sub in child:
                sub_local = _local(sub.tag)
                if sub_local == "Identifier" and identifier is None:
                    identifier = _text(sub)
                elif sub_local == "Title" and title is None:
                    title = _text(sub)
                elif sub_local == "Style":
                    for style_sub in sub:
                        if _local(style_sub.tag) == "Identifier":
                            styles.append(_text(style_sub))
                elif sub_local == "TileMatrixSetLink":
                    for link_sub in sub:
                        if _local(link_sub.tag) == "TileMatrixSet":
                            tms_links.append(_text(link_sub))
                elif sub_local == "Format":
                    formats.append(_text(sub))
            if identifier:
                layers.append(
                    WmtsLayerInfo(
                        identifier=identifier,
                        title=title or "",
                        styles=styles,
                        tilematrixset_links=tms_links,
                        formats=formats,
                    )
                )

    return layers, tilematrixsets


def build_capabilities_url(base_url: str, lang: Optional[str] = None) -> str:
    """Builds a plain GetCapabilities request URL for embedding as the
    `url` parameter of a QGIS provider connection string."""
    params = {
        PARAM_SERVICE: _SERVICE_WMTS,
        PARAM_VERSION: _WMTS_VERSION,
        PARAM_REQUEST: REQUEST_GET_CAPABILITIES,
    }
    if lang:
        params[PARAM_LANG] = lang
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{urlencode(params)}"


def build_qgis_wms_uri(
    base_url: str,
    layer: str,
    tile_matrix_set: Optional[str] = None,
    style: str = "",
    crs: str = "EPSG:3857",
    image_format: str = "image/png",
    authcfg: Optional[str] = None,
) -> str:
    """Builds a connection string for QGIS's native "wms" data provider.

    Only characters that would actually conflict with the outer
    key=value&key=value delimiter structure are escaped (literal '=' and
    '&' within a value) - ':', '/', and '?' are left as plain characters.
    This matches QGIS's own real connection-string convention exactly,
    confirmed directly against a working string QGIS itself produced for
    the same layer: a first version of this function used
    quote(value, safe='') (encode everything), which broke the
    connection - QGIS's own provider could not parse an embedded URL
    whose scheme/host/path had also been percent-encoded, not just its
    own query string's '='/'&'.

    authcfg, when given, is the id of a QGIS Authentication Configuration
    (stored in QGIS's own encrypted auth database, not in this plugin's
    own config) - added as a plain "authcfg" connection-string parameter,
    which QGIS's provider expands into real credentials just before the
    HTTP request, per QGIS's own authentication infrastructure convention
    (QgsDataSourceUri.setParam("authcfg", authcfg), used unexpanded).
    This module never sees or handles the actual credential itself - that
    stays inside QGIS's own auth manager throughout.
    """
    capabilities_url = build_capabilities_url(base_url)
    params = [
        ("crs", crs),
        ("format", image_format),
        ("layers", layer),
        ("styles", style),
    ]
    if tile_matrix_set:
        params.append(("tileMatrixSet", tile_matrix_set))
    if authcfg:
        params.append(("authcfg", authcfg))
    encoded_params = "&".join(
        f"{key}={quote(str(value), safe=':/?')}" for key, value in params
    )
    return f"{encoded_params}&url={quote(capabilities_url, safe=':/?')}"
