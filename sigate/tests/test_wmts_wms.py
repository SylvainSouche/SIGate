"""
Tests for sigate.gateways.wmts_wms - pure URI-construction and real
GetCapabilities-parsing logic, no Qt dependency.
"""

from urllib.parse import parse_qs, unquote

import pytest

from sigate.gateways import atom, wmts_wms


def _multi_layer_capabilities_xml():
    return b"""<?xml version="1.0"?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0" xmlns:ows="http://www.opengis.net/ows/1.1">
<Contents>
<Layer>
<ows:Title>Photographies aeriennes</ows:Title>
<ows:Identifier>ORTHOIMAGERY.ORTHOPHOTOS</ows:Identifier>
<Style isDefault="true"><ows:Identifier>normal</ows:Identifier></Style>
<Format>image/jpeg</Format>
<TileMatrixSetLink><TileMatrixSet>PM</TileMatrixSet></TileMatrixSetLink>
</Layer>
<Layer>
<ows:Title>Photographies aeriennes Lambert 93</ows:Title>
<ows:Identifier>HR.ORTHOIMAGERY.ORTHOPHOTOS.L93</ows:Identifier>
<Style isDefault="true"><ows:Identifier>normal</ows:Identifier></Style>
<Format>image/jpeg</Format>
<TileMatrixSetLink><TileMatrixSet>2154_10cm_10_20</TileMatrixSet></TileMatrixSetLink>
</Layer>
<TileMatrixSet>
<ows:Identifier>PM</ows:Identifier>
<ows:SupportedCRS>urn:ogc:def:crs:EPSG::3857</ows:SupportedCRS>
</TileMatrixSet>
<TileMatrixSet>
<ows:Identifier>2154_10cm_10_20</ows:Identifier>
<ows:SupportedCRS>urn:ogc:def:crs:EPSG::2154</ows:SupportedCRS>
</TileMatrixSet>
</Contents>
</Capabilities>"""


def test_urn_to_epsg_extracts_code_from_ogc_urn():
    assert wmts_wms.urn_to_epsg("urn:ogc:def:crs:EPSG::2154") == "EPSG:2154"


def test_urn_to_epsg_leaves_plain_code_unchanged():
    assert wmts_wms.urn_to_epsg("EPSG:3857") == "EPSG:3857"


def test_http_get_merges_extra_headers(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"ok"

    def fake_urlopen(req, timeout=None, context=None):
        captured["headers"] = dict(req.headers)
        return FakeResponse()

    monkeypatch.setattr(atom, "urlopen", fake_urlopen)
    result = atom.http_get("https://x/test", extra_headers={"Apikey": "secret"})
    assert result == b"ok"
    # urllib.Request title-cases header names it's given.
    assert captured["headers"]["Apikey"] == "secret"


def test_fetch_with_header_attaches_header_via_http_get(monkeypatch):
    captured = {}

    def fake_http_get(url, timeout=30, extra_headers=None):
        captured["url"] = url
        captured["extra_headers"] = extra_headers
        return b"<Capabilities/>"

    monkeypatch.setattr(atom, "http_get", fake_http_get)
    fetch = atom.fetch_with_header("apikey", "ign_scan_ws")
    result = fetch("https://data.geopf.fr/private/wmts?SERVICE=WMTS")
    assert result == b"<Capabilities/>"
    assert captured["extra_headers"] == {"apikey": "ign_scan_ws"}


def test_wmts_get_capabilities_finds_every_layer():
    layers, _ = wmts_wms.wmts_get_capabilities(
        "https://x/wmts", fetch=lambda url: _multi_layer_capabilities_xml()
    )
    identifiers = {layer.identifier for layer in layers}
    assert identifiers == {
        "ORTHOIMAGERY.ORTHOPHOTOS",
        "HR.ORTHOIMAGERY.ORTHOPHOTOS.L93",
    }


def test_wmts_get_capabilities_finds_every_tilematrixset_with_correct_crs():
    _, tilematrixsets = wmts_wms.wmts_get_capabilities(
        "https://x/wmts", fetch=lambda url: _multi_layer_capabilities_xml()
    )
    assert tilematrixsets["PM"].crs == "EPSG:3857"
    assert tilematrixsets["2154_10cm_10_20"].crs == "EPSG:2154"


def test_wmts_get_capabilities_finest_resolution_is_none_without_tile_matrix_levels():
    """The existing fixture's TileMatrixSet elements declare no
    TileMatrix children at all (a real, common shape - many
    GetCapabilities responses list levels; this fixture predates that
    parsing) - must not crash or fabricate a value."""
    _, tilematrixsets = wmts_wms.wmts_get_capabilities(
        "https://x/wmts", fetch=lambda url: _multi_layer_capabilities_xml()
    )
    assert tilematrixsets["PM"].finest_resolution is None


def _capabilities_xml_with_tile_matrix_levels():
    return b"""<?xml version="1.0"?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0" xmlns:ows="http://www.opengis.net/ows/1.1">
<Contents>
<Layer>
<ows:Title>Photographies aeriennes</ows:Title>
<ows:Identifier>ORTHOIMAGERY.ORTHOPHOTOS</ows:Identifier>
<Style isDefault="true"><ows:Identifier>normal</ows:Identifier></Style>
<Format>image/jpeg</Format>
<TileMatrixSetLink><TileMatrixSet>PM</TileMatrixSet></TileMatrixSetLink>
</Layer>
<TileMatrixSet>
<ows:Identifier>PM</ows:Identifier>
<ows:SupportedCRS>urn:ogc:def:crs:EPSG::3857</ows:SupportedCRS>
<TileMatrix>
<ows:Identifier>0</ows:Identifier>
<ScaleDenominator>559082264.029</ScaleDenominator>
</TileMatrix>
<TileMatrix>
<ows:Identifier>18</ows:Identifier>
<ScaleDenominator>2132.729530836</ScaleDenominator>
</TileMatrix>
<TileMatrix>
<ows:Identifier>19</ows:Identifier>
<ScaleDenominator>1066.364765418</ScaleDenominator>
</TileMatrix>
</TileMatrixSet>
</Contents>
</Capabilities>"""


def test_wmts_get_capabilities_finest_resolution_picks_the_smallest_scale_denominator():
    """Finest resolution (smallest ground distance per pixel) comes from
    the *smallest* ScaleDenominator among the levels, not the first one
    listed or the largest - real GetCapabilities responses don't
    guarantee level ordering."""
    _, tilematrixsets = wmts_wms.wmts_get_capabilities(
        "https://x/wmts",
        fetch=lambda url: _capabilities_xml_with_tile_matrix_levels(),
    )
    expected = 1066.364765418 * wmts_wms._OGC_PIXEL_SIZE_METERS
    assert tilematrixsets["PM"].finest_resolution == pytest.approx(expected)


def test_wmts_get_capabilities_retains_every_level_with_its_own_identifier():
    """Needed to let a user choose among real zoom levels, not just the
    finest one - each level's own TileMatrix Identifier must be kept
    (this is what GDAL's tilematrix= connection-string parameter
    actually expects, not an index)."""
    _, tilematrixsets = wmts_wms.wmts_get_capabilities(
        "https://x/wmts",
        fetch=lambda url: _capabilities_xml_with_tile_matrix_levels(),
    )
    levels = tilematrixsets["PM"].levels
    assert {level.identifier for level in levels} == {"0", "18", "19"}
    by_id = {level.identifier: level.resolution for level in levels}
    assert by_id["0"] == pytest.approx(559082264.029 * wmts_wms._OGC_PIXEL_SIZE_METERS)
    assert by_id["19"] == pytest.approx(
        1066.364765418 * wmts_wms._OGC_PIXEL_SIZE_METERS
    )


def test_wmts_get_capabilities_levels_is_empty_without_tile_matrix_children():
    _, tilematrixsets = wmts_wms.wmts_get_capabilities(
        "https://x/wmts", fetch=lambda url: _multi_layer_capabilities_xml()
    )
    assert tilematrixsets["PM"].levels == []


def test_wmts_get_capabilities_finest_resolution_ignores_unparseable_scale_denominator():
    xml = _capabilities_xml_with_tile_matrix_levels().replace(
        b"<ScaleDenominator>1066.364765418</ScaleDenominator>",
        b"<ScaleDenominator>not-a-number</ScaleDenominator>",
    )
    _, tilematrixsets = wmts_wms.wmts_get_capabilities(
        "https://x/wmts", fetch=lambda url: xml
    )
    # falls back to the next-finest valid level rather than crashing
    expected = 2132.729530836 * wmts_wms._OGC_PIXEL_SIZE_METERS
    assert tilematrixsets["PM"].finest_resolution == pytest.approx(expected)


def test_wmts_get_capabilities_layer_defaults_reflect_declared_values():
    layers, _ = wmts_wms.wmts_get_capabilities(
        "https://x/wmts", fetch=lambda url: _multi_layer_capabilities_xml()
    )
    hr_layer = next(
        layer
        for layer in layers
        if layer.identifier == "HR.ORTHOIMAGERY.ORTHOPHOTOS.L93"
    )
    assert hr_layer.default_style == "normal"
    assert hr_layer.default_tilematrixset == "2154_10cm_10_20"
    assert hr_layer.default_format == "image/jpeg"


def test_wmts_get_capabilities_returns_empty_when_no_contents_section():
    empty_doc = b'<Capabilities xmlns="http://www.opengis.net/wmts/1.0"></Capabilities>'
    layers, tilematrixsets = wmts_wms.wmts_get_capabilities(
        "https://x/wmts", fetch=lambda url: empty_doc
    )
    assert layers == []
    assert tilematrixsets == {}


def test_build_qgis_wms_uri_matches_real_qgis_encoding_convention():
    """Regression test for a real bug: an earlier version of this function
    used quote(value, safe='') (encode everything), which percent-encoded
    ':' and '/' even in simple values like "EPSG:3857" and "image/jpeg",
    and encoded the embedded url's scheme/host/path along with its query
    string. QGIS's own native connection string for the identical layer
    left ':', '/', '?' as literal characters throughout, only escaping
    '=' and '&' where they would otherwise collide with the outer
    key=value&key=value structure - confirmed by direct comparison
    against a real, working string QGIS itself produced."""
    uri = wmts_wms.build_qgis_wms_uri(
        "https://data.geopf.fr/wmts",
        layer="HR.ORTHOIMAGERY.ORTHOPHOTOS",
        tile_matrix_set="PM_6_19",
        style="normal",
        crs="EPSG:3857",
        image_format="image/jpeg",
    )
    assert "crs=EPSG:3857" in uri
    assert "format=image/jpeg" in uri
    assert (
        "url=https://data.geopf.fr/wmts?SERVICE%3DWMTS%26VERSION%3D1.0.0%26REQUEST%3DGetCapabilities"
        in uri
    )
    # confirm nothing is doubly- or over-encoded
    assert "%3A" not in uri
    assert "%2F" not in uri


def test_build_capabilities_url_appends_query_when_none_present():
    url = wmts_wms.build_capabilities_url("https://data.geopf.fr/wmts")
    assert url.startswith("https://data.geopf.fr/wmts?")
    assert "SERVICE=WMTS" in url
    assert "REQUEST=GetCapabilities" in url


def test_build_capabilities_url_omits_lang_when_not_given():
    url = wmts_wms.build_capabilities_url("https://wmts.geo.admin.ch")
    assert "lang" not in url.lower()


def test_build_capabilities_url_includes_lang_when_given():
    url = wmts_wms.build_capabilities_url("https://wmts.geo.admin.ch", lang="fr")
    assert "lang=fr" in url


def test_wmts_get_capabilities_passes_lang_through_to_the_request():
    """Confirms the lang parameter actually reaches the URL that gets
    fetched, not just build_capabilities_url in isolation - real,
    human-translated layer titles directly from a source that supports
    this only happen if the parameter survives all the way to the real
    HTTP call."""
    captured_urls = []

    def fetch(url):
        captured_urls.append(url)
        return b'<Capabilities xmlns="x"><Contents/></Capabilities>'

    wmts_wms.wmts_get_capabilities("https://wmts.geo.admin.ch", fetch=fetch, lang="it")
    assert "lang=it" in captured_urls[0]


def test_build_qgis_wms_uri_includes_tile_matrix_set_for_wmts():
    uri = wmts_wms.build_qgis_wms_uri(
        "https://data.geopf.fr/wmts",
        layer="HR.ORTHOIMAGERY.ORTHOPHOTOS.L93",
        tile_matrix_set="2154_10cm_10_20",
        style="normal",
        crs="EPSG:2154",
        image_format="image/jpeg",
    )
    # The uri is a flat "key=value&key=value" string with each value
    # percent-encoded - parse and decode it as such.
    parts = {k: unquote(v) for k, v in (pair.split("=", 1) for pair in uri.split("&"))}
    assert parts["layers"] == "HR.ORTHOIMAGERY.ORTHOPHOTOS.L93"
    assert parts["styles"] == "normal"
    assert parts["tileMatrixSet"] == "2154_10cm_10_20"
    assert parts["crs"] == "EPSG:2154"
    assert parts["format"] == "image/jpeg"

    embedded_url = parts["url"]
    assert embedded_url.startswith("https://data.geopf.fr/wmts?")
    embedded_params = parse_qs(embedded_url.split("?", 1)[1])
    assert embedded_params["SERVICE"] == ["WMTS"]
    assert embedded_params["REQUEST"] == ["GetCapabilities"]


def test_build_qgis_wms_uri_omits_tile_matrix_set_when_not_given():
    uri = wmts_wms.build_qgis_wms_uri("https://example.test/wms", layer="some_layer")
    assert "tileMatrixSet" not in uri


def test_build_qgis_wms_uri_omits_authcfg_when_not_given():
    uri = wmts_wms.build_qgis_wms_uri("https://example.test/wms", layer="some_layer")
    assert "authcfg" not in uri


def test_build_qgis_wms_uri_includes_authcfg_unencoded_when_given():
    """authcfg is a QGIS-generated identifier, always plain alphanumeric -
    it must appear exactly as given, not re-encoded, and QGIS's provider
    expands it into real credentials at request time (this module never
    sees the credential itself)."""
    uri = wmts_wms.build_qgis_wms_uri(
        "https://data.geopf.fr/private/wmts",
        layer="ORTHOIMAGERY.ORTHOPHOTOS.SPOT5",
        style="normal",
        crs="EPSG:3857",
        authcfg="sigateAK1",
    )
    parts = {k: unquote(v) for k, v in (pair.split("=", 1) for pair in uri.split("&"))}
    assert parts["authcfg"] == "sigateAK1"


def test_wmts_wms_capabilities_declares_coordinate_extent():
    assert wmts_wms.CAPABILITIES.extent_flavor == "coordinate"
    assert wmts_wms.CAPABILITIES.supports_query is False


def test_wmts_wms_capabilities_declares_locale_support():
    """Confirmed real on geo.admin.ch's own docs (?lang= honoured on
    every WMS/WMTS request) - the first gateway to actually flip this
    flag, now that build_capabilities_url/wmts_get_capabilities
    implement it."""
    assert wmts_wms.CAPABILITIES.supports_locale is True


# --- plain WMS --------------------------------------------------------------

WMS_130 = b"""<?xml version="1.0"?>
<WMS_Capabilities version="1.3.0" xmlns="http://www.opengis.net/wms">
<Capability>
 <Request><GetMap><Format>image/gif</Format><Format>image/jpeg</Format>
  <Format>image/png</Format></GetMap></Request>
 <Layer>
  <Title>Root</Title>
  <CRS>EPSG:2056</CRS><CRS>EPSG:4326</CRS>
  <Layer><Title>Group without a name</Title>
   <Layer queryable="1"><Name>lawine</Name><Title>Lawine</Title>
    <CRS>EPSG:3857</CRS><Style><Name>default</Name></Style></Layer>
   <Layer><Name>sturz</Name><Title>Sturz</Title></Layer>
  </Layer>
  <Layer><Name>top</Name><Title>Top level</Title></Layer>
 </Layer>
</Capability></WMS_Capabilities>"""


def test_wms_capabilities_lists_named_layers_with_inherited_crs_and_png_first():
    from sigate.gateways.wmts_wms import wms_get_capabilities

    seen = []

    def fetch(url):
        seen.append(url)
        return WMS_130

    layers = {
        layer.identifier: layer
        for layer in wms_get_capabilities("https://x.test/wms", fetch)
    }
    assert set(layers) == {"lawine", "sturz", "top"}
    assert "SERVICE=WMS" in seen[0] and "VERSION=1.3.0" in seen[0]
    # inherited from the root, plus its own
    assert layers["lawine"].crs == ["EPSG:2056", "EPSG:4326", "EPSG:3857"]
    assert layers["sturz"].crs == ["EPSG:2056", "EPSG:4326"]
    assert layers["lawine"].styles == ["default"]
    assert layers["lawine"].formats[0] == "image/png"
    assert layers["lawine"].default_format == "image/png"
    assert layers["lawine"].default_tilematrixset is None
    # the unnamed group's title is kept in front of the child's
    assert layers["lawine"].title == "Group without a name / Lawine"


def test_wms_capabilities_raises_on_a_service_exception():
    import pytest

    from sigate.gateways.wmts_wms import wms_get_capabilities

    body = b"<ServiceExceptionReport><ServiceException>nope</ServiceException></ServiceExceptionReport>"
    with pytest.raises(ValueError, match="nope"):
        wms_get_capabilities("https://x.test/wms", lambda url: body)


def test_wms_choose_crs_prefers_web_mercator_then_lonlat_and_skips_invalid():
    from sigate.gateways.wmts_wms import wms_choose_crs

    assert wms_choose_crs(["EPSG:2056", "EPSG:4326", "EPSG:3857"]) == "EPSG:3857"
    assert wms_choose_crs(["EPSG:2056", "EPSG:4326"]) == "EPSG:4326"
    assert wms_choose_crs(["EPSG:2056", "CRS:84"]) == "EPSG:2056"
    assert wms_choose_crs(["EPSG:2056"], lambda c: c != "EPSG:2056") == "EPSG:3857"
    assert wms_choose_crs([]) == "EPSG:3857"


def test_wms_plain_url_drops_ogc_parameters_but_keeps_others():
    from sigate.gateways.wmts_wms import wms_plain_url

    assert (
        wms_plain_url(
            "https://x.test/cgi?MAP=/a/b.map&SERVICE=WMS&request=GetCapabilities"
        )
        == "https://x.test/cgi?MAP=%2Fa%2Fb.map"
    )
    assert wms_plain_url("https://x.test/wms") == "https://x.test/wms"


def test_build_qgis_wms_uri_for_plain_wms_uses_the_bare_endpoint():
    from sigate.gateways.wmts_wms import build_qgis_wms_uri

    uri = build_qgis_wms_uri(
        "https://x.test/wms?SERVICE=WMS", "lawine", crs="EPSG:4326", wms=True
    )
    assert "tileMatrixSet" not in uri
    assert "url=https://x.test/wms" in uri
    assert "SERVICE=WMTS" not in uri
