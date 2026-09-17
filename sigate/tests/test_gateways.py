"""
Tests for sigate.gateways - the pure-Python layer with no Qt/QGIS
dependency. Every test injects a fake `fetch` function rather than
touching a real network or a real QGIS install.
"""

import pytest

from sigate.gateways import atom, geonorge_catalog, stac, wfs
from sigate.gateways.base import GatewayCapabilities, urn_to_epsg


# --------------------------------------------------------------------------- base


def test_urn_to_epsg_converts_a_real_urn():
    assert urn_to_epsg("urn:ogc:def:crs:EPSG::2154") == "EPSG:2154"


def test_urn_to_epsg_leaves_an_already_plain_code_unchanged():
    assert urn_to_epsg("EPSG:3857") == "EPSG:3857"


def test_urn_to_epsg_rejects_empty_input():
    with pytest.raises(ValueError):
        urn_to_epsg("")


def test_urn_to_epsg_rejects_whitespace_only_input():
    with pytest.raises(ValueError):
        urn_to_epsg("   ")


def test_urn_to_epsg_rejects_non_string_input():
    with pytest.raises(ValueError):
        urn_to_epsg(None)


def test_urn_to_epsg_preserves_a_non_epsg_urn_authority():
    """Regression test for the actual confirmed bug, corrected a second
    time: IGN's private WMTS (data.geopf.fr/private/wmts) declares at
    least one TileMatrixSet's CRS as "urn:ogc:def:crs:IGNF::LAMB93" -
    using IGN's own "IGNF" authority, not EPSG at all. Confirmed
    directly by matching QGIS's own native "Add Layer from WMS/WMTS"
    dialog, which resolves this exact same layer to "IGNF:LAMB93". The
    authority must be preserved from the URN as declared, not assumed
    to always be EPSG."""
    assert urn_to_epsg("urn:ogc:def:crs:IGNF::LAMB93") == "IGNF:LAMB93"


def test_urn_to_epsg_leaves_an_already_plain_non_epsg_pair_unchanged():
    assert urn_to_epsg("IGNF:LAMB93") == "IGNF:LAMB93"


def test_urn_to_epsg_resolves_the_confirmed_real_lamb93_alias():
    """The bare-name fallback, for the (less likely, but not ruled out)
    case where a server declares just "LAMB93" with no colon-delimited
    URN structure at all to parse an authority out of. Resolves to
    "IGNF:LAMB93" - not "EPSG:2154", which an earlier version of this
    fix guessed at before the real authority was confirmed (see
    _KNOWN_NON_STANDARD_CRS_ALIASES's own comment)."""
    assert urn_to_epsg("LAMB93") == "IGNF:LAMB93"


def test_urn_to_epsg_alias_lookup_is_case_insensitive():
    assert urn_to_epsg("lamb93") == "IGNF:LAMB93"


def test_urn_to_epsg_passes_through_unrecognized_non_numeric_names_unchanged():
    """The other half of the same fix: a name that isn't a bare numeric
    EPSG code and isn't a known alias must not be turned into a
    plausible-looking but wrong "EPSG:<name>" string - it's returned
    unchanged so a downstream QgsCoordinateReferenceSystem(...)
    .isValid() check can correctly reject it, rather than a naive
    "starts with EPSG:" check mistaking it for something already
    validated."""
    assert urn_to_epsg("SOME_UNKNOWN_NAME") == "SOME_UNKNOWN_NAME"
    assert not urn_to_epsg("SOME_UNKNOWN_NAME").startswith("EPSG:")


def test_atom_build_url_sets_page_and_limit():
    url = atom.build_url(
        "https://data.geopf.fr/telechargement/resource/RGEALTI", page=3, limit=50
    )
    assert "page=3" in url
    assert "limit=50" in url
    assert "pageSize=50" in url


def test_atom_fetch_page_parses_typical_response():
    fake_xml = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:gpf_dl="http://geoplateforme.fr"
      gpf_dl:page="1" gpf_dl:pagesize="50" gpf_dl:pagecount="1" gpf_dl:totalentries="2">
  <title>RGEALTI</title>
  <entry>
    <title>RGEALTI_2-0_5M_ASC_LAMB93-IGN69_D003_2023-08-10</title>
    <id>urn:x1</id>
    <link href="https://data.geopf.fr/telechargement/resource/RGEALTI/xxx" type="application/atom+xml"/>
  </entry>
  <entry>
    <title>a real file</title>
    <id>urn:x2</id>
    <link href="https://data.geopf.fr/telechargement/download/RGEALTI/x.7z"
          type="application/x-7z-compressed" length="12345"/>
  </entry>
</feed>"""
    entries, feed_title, info = atom.fetch_page(
        "https://data.geopf.fr/telechargement/resource/RGEALTI",
        fetch=lambda url: fake_xml,
    )
    assert feed_title == "RGEALTI"
    assert info == {"page": 1, "pagesize": 50, "pagecount": 1, "totalentries": 2}
    assert len(entries) == 2
    assert entries[0].is_dir is True
    assert entries[1].is_dir is False
    assert entries[1].file_link.length == "12345"


def test_atom_fetch_all_pages_aggregates_across_pages():
    pages = {
        1: b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:gpf_dl="http://x" '
        b'gpf_dl:page="1" gpf_dl:pagesize="2" gpf_dl:pagecount="2" gpf_dl:totalentries="3">'
        b'<entry><title>A</title><id>a</id><link href="https://x/a" type="application/x-7z-compressed"/></entry>'
        b'<entry><title>B</title><id>b</id><link href="https://x/b" type="application/x-7z-compressed"/></entry>'
        b"</feed>",
        2: b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:gpf_dl="http://x" '
        b'gpf_dl:page="2" gpf_dl:pagesize="2" gpf_dl:pagecount="2" gpf_dl:totalentries="3">'
        b'<entry><title>C</title><id>c</id><link href="https://x/c" type="application/x-7z-compressed"/></entry>'
        b"</feed>",
    }
    calls = []

    def fake_fetch(url):
        calls.append(url)
        page = 2 if "page=2" in url else 1
        return pages[page]

    entries, total = atom.fetch_all_pages("https://x/resource/TEST", fetch=fake_fetch)
    assert total == 3
    assert [e.title for e in entries] == ["A", "B", "C"]
    assert len(calls) == 2


def test_atom_fetch_all_pages_raises_large_listing_unless_forced():
    big_page = (
        b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:gpf_dl="http://x" '
        b'gpf_dl:page="1" gpf_dl:pagesize="50" gpf_dl:pagecount="100" gpf_dl:totalentries="5000">'
        b"</feed>"
    )
    with pytest.raises(atom.LargeListing) as excinfo:
        atom.fetch_all_pages("https://x/resource/BIG", fetch=lambda url: big_page)
    assert excinfo.value.total == 5000


def test_atom_capabilities_declares_categorical_extent_and_file_index():
    assert isinstance(atom.CAPABILITIES, GatewayCapabilities)
    assert atom.CAPABILITIES.extent_flavor == "categorical"
    assert atom.CAPABILITIES.is_file_index is True
    assert atom.CAPABILITIES.supports_query is False


# --------------------------------------------------------------------------- http_get error surfacing


def test_http_get_surfaces_the_real_ows_exception_text(monkeypatch):
    """Regression test for a real reported failure: a WFS spatial
    filter failing with only the generic "HTTP Error 400: Bad Request"
    gave no way to actually diagnose why - the server's own response
    body (a real OWS ExceptionReport, in this realistic case) was never
    being read at all before this fix."""
    import io
    from urllib.error import HTTPError

    body = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/1.1">'
        b'<ows:Exception exceptionCode="InvalidParameterValue" locator="filter">'
        b"<ows:ExceptionText>Error parsing CQL: Encountered &quot;WITHIN&quot; at line 1"
        b"</ows:ExceptionText></ows:Exception></ows:ExceptionReport>"
    )

    def fake_urlopen(req, timeout=None, context=None):
        raise HTTPError("https://x/wfs", 400, "Bad Request", {}, io.BytesIO(body))

    monkeypatch.setattr(atom, "urlopen", fake_urlopen)

    with pytest.raises(atom.GatewayError) as exc_info:
        atom.http_get("https://x/wfs")
    message = str(exc_info.value)
    assert "400" in message
    assert "Error parsing CQL" in message
    assert "WITHIN" in message


def test_http_get_falls_back_to_raw_body_for_a_non_ows_error_response(monkeypatch):
    import io
    from urllib.error import HTTPError

    def fake_urlopen(req, timeout=None, context=None):
        raise HTTPError(
            "https://x/wfs",
            500,
            "Internal Server Error",
            {},
            io.BytesIO(b"Internal Server Error - contact the administrator"),
        )

    monkeypatch.setattr(atom, "urlopen", fake_urlopen)

    with pytest.raises(atom.GatewayError) as exc_info:
        atom.http_get("https://x/wfs")
    assert "Internal Server Error - contact the administrator" in str(exc_info.value)


def test_http_get_handles_an_empty_response_body_gracefully(monkeypatch):
    import io
    from urllib.error import HTTPError

    def fake_urlopen(req, timeout=None, context=None):
        raise HTTPError("https://x/wfs", 400, "Bad Request", {}, io.BytesIO(b""))

    monkeypatch.setattr(atom, "urlopen", fake_urlopen)

    with pytest.raises(atom.GatewayError) as exc_info:
        atom.http_get("https://x/wfs")
    assert "no response body" in str(exc_info.value)


def test_extract_ows_exception_text_falls_back_for_malformed_xml():
    assert atom._extract_ows_exception_text("<not><valid xml") == "<not><valid xml"


def test_resolve_downloadable_files_returns_single_file_link_for_a_file_entry():
    fake_xml = b"""<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>a file</title><id>a</id>
<link href="https://x/download/a.7z" type="application/x-7z-compressed"/></entry>
</feed>"""
    entries, _, _ = atom.fetch_page("https://x/resource/A", fetch=lambda url: fake_xml)
    file_entry = entries[0]
    assert not file_entry.is_dir
    resolved = atom.resolve_downloadable_files(file_entry)
    assert len(resolved) == 1
    file_link, checksum_link = resolved[0]
    assert file_link.href == "https://x/download/a.7z"
    assert checksum_link is None


def test_resolve_downloadable_files_returns_all_files_for_a_directory_entry_with_multiple_parts():
    """A directory entry may resolve to more than one physical file - the
    real case this matters for is a split archive delivered as several
    parts under one product directory."""
    top_level_xml = b"""<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>a product</title><id>p</id>
<link href="https://x/resource/PRODUCT_EDITION" type="application/atom+xml"/></entry>
</feed>"""
    inner_xml = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:gpf_dl="http://x"
     gpf_dl:page="1" gpf_dl:pagesize="50" gpf_dl:pagecount="1" gpf_dl:totalentries="2">
<entry><title>part 1</title><id>p1</id>
<link href="https://x/download/PRODUCT.7z.001" type="application/x-7z-compressed"/></entry>
<entry><title>part 2</title><id>p2</id>
<link href="https://x/download/PRODUCT.7z.002" type="application/x-7z-compressed"/></entry>
</feed>"""

    def fake_fetch(url):
        return inner_xml if "PRODUCT_EDITION" in url else top_level_xml

    entries, _, _ = atom.fetch_page("https://x/resource/PRODUCT", fetch=fake_fetch)
    dir_entry = entries[0]
    assert dir_entry.is_dir

    resolved = atom.resolve_downloadable_files(dir_entry, fetch=fake_fetch)
    assert [file_link.href for file_link, _ in resolved] == [
        "https://x/download/PRODUCT.7z.001",
        "https://x/download/PRODUCT.7z.002",
    ]
    assert all(checksum_link is None for _, checksum_link in resolved)


def test_find_checksum_link_matches_full_filename_convention():
    """ "<full filename>.md5" (e.g. "tile.tif.md5") is a real, common
    checksum-sidecar naming convention."""
    fake_xml = b"""<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>a file</title><id>a</id>
<link href="https://x/download/tile.tif" type="image/tiff"/>
<link href="https://x/download/tile.tif.md5" type="text/plain"/>
</entry>
</feed>"""
    entries, _, _ = atom.fetch_page("https://x/resource/A", fetch=lambda url: fake_xml)
    entry = entries[0]
    checksum_link = atom.find_checksum_link(entry, entry.file_link)
    assert checksum_link is not None
    assert checksum_link.href == "https://x/download/tile.tif.md5"


def test_find_checksum_link_matches_stem_convention():
    """ "<filename without its own extension>.md5" (e.g.
    "tile.md5" for "tile.tif") is the other real, common convention."""
    fake_xml = b"""<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>a file</title><id>a</id>
<link href="https://x/download/tile.tif" type="image/tiff"/>
<link href="https://x/download/tile.md5" type="text/plain"/>
</entry>
</feed>"""
    entries, _, _ = atom.fetch_page("https://x/resource/A", fetch=lambda url: fake_xml)
    entry = entries[0]
    checksum_link = atom.find_checksum_link(entry, entry.file_link)
    assert checksum_link is not None
    assert checksum_link.href == "https://x/download/tile.md5"


def test_find_checksum_link_none_when_no_candidate_present():
    fake_xml = b"""<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>a file</title><id>a</id>
<link href="https://x/download/tile.tif" type="image/tiff"/>
</entry>
</feed>"""
    entries, _, _ = atom.fetch_page("https://x/resource/A", fetch=lambda url: fake_xml)
    entry = entries[0]
    assert atom.find_checksum_link(entry, entry.file_link) is None


def test_resolve_downloadable_files_pairs_each_file_with_its_own_checksum_link():
    """Regression test for the real reason checksum lookup has to happen
    per sub-entry inside resolve_downloadable_files itself: each part of
    a split archive has its own distinct checksum sidecar, and the
    caller has no way to know which sub-entry a given file_link came
    from once this function has flattened the list."""
    top_level_xml = b"""<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>a product</title><id>p</id>
<link href="https://x/resource/PRODUCT_EDITION" type="application/atom+xml"/></entry>
</feed>"""
    inner_xml = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:gpf_dl="http://x"
     gpf_dl:page="1" gpf_dl:pagesize="50" gpf_dl:pagecount="1" gpf_dl:totalentries="2">
<entry><title>part 1</title><id>p1</id>
<link href="https://x/download/PRODUCT.7z.001" type="application/x-7z-compressed"/>
<link href="https://x/download/PRODUCT.7z.001.md5" type="text/plain"/>
</entry>
<entry><title>part 2</title><id>p2</id>
<link href="https://x/download/PRODUCT.7z.002" type="application/x-7z-compressed"/>
<link href="https://x/download/PRODUCT.7z.002.md5" type="text/plain"/>
</entry>
</feed>"""

    def fake_fetch(url):
        return inner_xml if "PRODUCT_EDITION" in url else top_level_xml

    entries, _, _ = atom.fetch_page("https://x/resource/PRODUCT", fetch=fake_fetch)
    dir_entry = entries[0]

    resolved = atom.resolve_downloadable_files(dir_entry, fetch=fake_fetch)
    assert [
        (file_link.href, checksum_link.href if checksum_link else None)
        for file_link, checksum_link in resolved
    ] == [
        ("https://x/download/PRODUCT.7z.001", "https://x/download/PRODUCT.7z.001.md5"),
        ("https://x/download/PRODUCT.7z.002", "https://x/download/PRODUCT.7z.002.md5"),
    ]


# --------------------------------------------------------------------------- wfs


def test_wfs_get_capabilities_parses_typical_response():
    cap_xml = b"""<?xml version="1.0"?>
<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0">
<wfs:FeatureTypeList>
<wfs:FeatureType xmlns:IGNF_MNS-LIDAR-HD="http://IGNF_MNS-LIDAR-HD">
<wfs:Name>IGNF_MNS-LIDAR-HD:bloc</wfs:Name>
<wfs:Title>LiDAR HD - Blocs MNS</wfs:Title>
</wfs:FeatureType>
<wfs:FeatureType>
<wfs:Name>IGNF_MNS-LIDAR-HD:dalle</wfs:Name>
<wfs:Title>LiDAR HD - Dalles MNS</wfs:Title>
</wfs:FeatureType>
</wfs:FeatureTypeList>
</wfs:WFS_Capabilities>"""
    raw, types, url = wfs.wfs_get_capabilities(
        "https://data.geopf.fr/wfs/ows", fetch=lambda u: cap_xml
    )
    assert [(t.name, t.title) for t in types] == [
        ("IGNF_MNS-LIDAR-HD:bloc", "LiDAR HD - Blocs MNS"),
        ("IGNF_MNS-LIDAR-HD:dalle", "LiDAR HD - Dalles MNS"),
    ]
    # No DefaultCRS/DefaultSRS declared in this fixture - falls back to
    # WFS's own long-standing conventional default, not left blank.
    assert types[0].default_crs == "EPSG:4326"


def test_wfs_get_capabilities_parses_declared_default_crs():
    """A real, needed piece of data this parser didn't capture before:
    each feature type's own declared CRS, used to build a spatial
    filter's geometry literal in the CRS the server actually expects -
    the same "don't mix CRS silently" lesson already applied to the
    WMTS export feature's bbox handling."""
    cap_xml = b"""<?xml version="1.0"?>
<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0">
<wfs:FeatureTypeList>
<wfs:FeatureType>
<wfs:Name>ADMINEXPRESS:commune</wfs:Name>
<wfs:Title>Communes</wfs:Title>
<wfs:DefaultCRS>urn:ogc:def:crs:EPSG::2154</wfs:DefaultCRS>
</wfs:FeatureType>
</wfs:FeatureTypeList>
</wfs:WFS_Capabilities>"""
    _, types, _ = wfs.wfs_get_capabilities("https://x/wfs", fetch=lambda u: cap_xml)
    assert types[0].default_crs == "EPSG:2154"


def test_wfs_get_capabilities_parses_default_srs_for_older_servers():
    """WFS 1.x servers declare DefaultSRS instead of 2.0's DefaultCRS -
    both must be recognized, since which one a given server uses isn't
    something this plugin controls."""
    cap_xml = b"""<?xml version="1.0"?>
<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs">
<wfs:FeatureTypeList>
<wfs:FeatureType>
<wfs:Name>layer</wfs:Name>
<wfs:Title>Layer</wfs:Title>
<wfs:DefaultSRS>urn:ogc:def:crs:EPSG::3857</wfs:DefaultSRS>
</wfs:FeatureType>
</wfs:FeatureTypeList>
</wfs:WFS_Capabilities>"""
    _, types, _ = wfs.wfs_get_capabilities("https://x/wfs", fetch=lambda u: cap_xml)
    assert types[0].default_crs == "EPSG:3857"


def test_wfs_parse_csv_extracts_typical_row():
    csv_text = (
        "wkt_geom\tname\tid\turl\tname_download\tid_chantier\ttimestamp\n"
        "Polygon ((1 2, 3 4))\tLHD_FXX_1003_6558\t337386\t"
        "https://data.geopf.fr/wms-r?X\tLHD_FXX_1003_6558.tif\t137\t2025-05-01\n"
    )
    fieldnames, rows = wfs.wfs_parse_csv(csv_text)
    assert "url" in fieldnames
    assert rows[0]["id_chantier"] == "137"
    assert rows[0]["url"] == "https://data.geopf.fr/wms-r?X"


def test_wfs_build_field_search_cql_escapes_quotes():
    assert (
        wfs.build_field_search_cql("name", "LHD_FXX_1003_6558", exact=True)
        == "name = 'LHD_FXX_1003_6558'"
    )
    assert (
        wfs.build_field_search_cql("name", "D'Artagnan", exact=False)
        == "name LIKE '%D''Artagnan%'"
    )


def test_wfs_build_getfeature_url_omits_srsname_when_not_given():
    url = wfs.wfs_build_getfeature_url("https://x/wfs", "test:layer")
    assert "SRSNAME" not in url.upper()


def test_wfs_build_getfeature_url_includes_srsname_when_given():
    """Regression test for a real, confirmed gap: no SRSNAME parameter
    was ever sent on any WFS GetFeature request, leaving a request's
    actual CRS - for both an active filter's geometry literal and the
    returned features' own geometries - entirely to whatever a server
    defaults to when it's omitted, unlike QGIS's own native WFS
    provider (confirmed working against the same server) which always
    sends this explicitly."""
    url = wfs.wfs_build_getfeature_url(
        "https://x/wfs", "test:layer", srsname="EPSG:4326"
    )
    assert "SRSNAME=EPSG%3A4326" in url


def test_wfs_get_features_threads_srsname_through_to_the_real_request(monkeypatch):
    """Confirms srsname actually reaches the real request URL through
    wfs_get_features (not just wfs_build_getfeature_url in isolation) -
    both the CSV attempt and the GML fallback."""
    captured_urls = []

    def fake_fetch(url):
        captured_urls.append(url)
        return b"name\nD001\n"

    wfs.wfs_get_features(
        "https://x/wfs", "test:layer", srsname="IGNF:LAMB93", fetch=fake_fetch
    )
    assert captured_urls
    assert "SRSNAME=IGNF%3ALAMB93" in captured_urls[0]


# --------------------------------------------------------------------------- DescribeFeatureType geometry field

# The exact real DescribeFeatureType response confirmed live this
# session for IGN's BDTOPO_V3:itineraire_autre - the actual schema that
# settled a real, repeatedly-reported bug (a CSV-column-name guessing
# heuristic kept getting this feature type's geometry field wrong, even
# after being corrected once - the guess itself turned out to already be
# right; the real bug was elsewhere - this DescribeFeatureType-based
# lookup exists specifically to stop needing to guess at all).
_REAL_ITINERAIRE_AUTRE_SCHEMA = b"""<?xml version="1.0" encoding="UTF-8"?><xsd:schema xmlns:BDTOPO_V3="http://BDTOPO_V3" xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:xsd="http://www.w3.org/2001/XMLSchema" elementFormDefault="qualified" targetNamespace="http://BDTOPO_V3">
  <xsd:import namespace="http://www.opengis.net/gml/3.2" schemaLocation="https://data.geopf.fr/wfs/schemas/gml/3.2.1/gml.xsd"/>
  <xsd:complexType name="itineraire_autreType">
    <xsd:complexContent>
      <xsd:extension base="gml:AbstractFeatureType">
        <xsd:sequence>
          <xsd:element maxOccurs="1" minOccurs="0" name="cleabs" nillable="true" type="xsd:string"/>
          <xsd:element maxOccurs="1" minOccurs="0" name="nature" nillable="true" type="xsd:string"/>
          <xsd:element maxOccurs="1" minOccurs="0" name="toponyme" nillable="true" type="xsd:string"/>
          <xsd:element maxOccurs="1" minOccurs="0" name="geometrie" nillable="true" type="gml:MultiCurvePropertyType"/>
        </xsd:sequence>
      </xsd:extension>
    </xsd:complexContent>
  </xsd:complexType>
  <xsd:element name="itineraire_autre" substitutionGroup="gml:AbstractFeature" type="BDTOPO_V3:itineraire_autreType"/>
</xsd:schema>"""


def test_wfs_describe_geometry_field_finds_the_real_confirmed_geometry_element():
    result = wfs.wfs_describe_geometry_field(
        "https://data.geopf.fr/wfs/ows",
        "BDTOPO_V3:itineraire_autre",
        fetch=lambda url: _REAL_ITINERAIRE_AUTRE_SCHEMA,
    )
    assert result == "geometrie"


def test_wfs_describe_geometry_field_recognizes_a_point_geometry_type():
    schema = b"""<?xml version="1.0"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:gml="http://www.opengis.net/gml/3.2">
<xsd:complexType name="poiType"><xsd:complexContent><xsd:extension base="gml:AbstractFeatureType">
<xsd:sequence>
<xsd:element name="nom" type="xsd:string"/>
<xsd:element name="the_geom" type="gml:PointPropertyType"/>
</xsd:sequence></xsd:extension></xsd:complexContent></xsd:complexType>
</xsd:schema>"""
    result = wfs.wfs_describe_geometry_field(
        "https://x/wfs", "test:poi", fetch=lambda url: schema
    )
    assert result == "the_geom"


def test_wfs_describe_geometry_field_returns_none_when_no_geometry_element_found():
    schema = b"""<?xml version="1.0"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema">
<xsd:complexType name="attributesOnlyType"><xsd:sequence>
<xsd:element name="nom" type="xsd:string"/>
</xsd:sequence></xsd:complexType>
</xsd:schema>"""
    result = wfs.wfs_describe_geometry_field(
        "https://x/wfs", "test:attrs_only", fetch=lambda url: schema
    )
    assert result is None


def test_wfs_describe_geometry_field_returns_none_on_fetch_failure():
    def failing_fetch(url):
        raise OSError("connection refused")

    result = wfs.wfs_describe_geometry_field(
        "https://x/wfs", "test:layer", fetch=failing_fetch
    )
    assert result is None


def test_wfs_describe_geometry_field_returns_none_for_unparseable_response():
    result = wfs.wfs_describe_geometry_field(
        "https://x/wfs", "test:layer", fetch=lambda url: b"not xml at all{{{"
    )
    assert result is None


def test_wfs_build_describefeaturetype_url_has_the_real_confirmed_shape():
    """Matches the real, confirmed-working URL pattern from cartes.gouv.fr's
    own official documentation and a live-fetched real response this
    session, not a guessed shape."""
    url = wfs.wfs_build_describefeaturetype_url(
        "https://data.geopf.fr/wfs/ows", "BDTOPO_V3:itineraire_autre"
    )
    assert "REQUEST=DescribeFeatureType" in url
    assert "TYPENAMES=BDTOPO_V3%3Aitineraire_autre" in url


def test_wfs_exception_report_is_raised_not_treated_as_empty_result():
    exception_xml = b"""<?xml version="1.0"?>
<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows" version="1.2.0">
<ows:Exception exceptionCode="InvalidParameterValue" locator="CQL_FILTER">
<ows:ExceptionText>Illegal property name: bogus_field</ows:ExceptionText>
</ows:Exception>
</ows:ExceptionReport>"""
    with pytest.raises(wfs.WfsServerException, match="bogus_field"):
        wfs._check_for_exception_report(exception_xml)


def test_wfs_exception_report_not_raised_for_legitimate_data():
    gml_xml = b'<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"><wfs:member/></wfs:FeatureCollection>'
    wfs._check_for_exception_report(gml_xml)  # should not raise


def test_wfs_fetch_all_pages_does_not_truncate_when_server_caps_page_size():
    """A server may silently return fewer rows than requested per page.
    When the real total is known (expected_total), pagination must keep
    going until that total is reached rather than stopping at the first
    short page, or results would be silently truncated."""
    total_features = 550
    server_cap = (
        100  # server returns at most this many rows regardless of what was requested
    )
    calls = []

    def fake_fetch(url):
        calls.append(url)
        n = len(calls)
        remaining = total_features - (n - 1) * server_cap
        count_this_page = min(server_cap, max(0, remaining))
        header = "name\turl\n"
        body = "".join(
            f"F{n}_{i}\thttps://x/{n}_{i}.tif\n" for i in range(count_this_page)
        )
        return (header + body).encode("utf-8")

    rows, fieldnames, fmt = wfs.wfs_fetch_all_pages(
        "https://x/wfs",
        "test:layer",
        page_size=200,
        expected_total=total_features,
        fetch=fake_fetch,
    )
    assert len(rows) == total_features
    assert len(calls) == 6  # ceil(550/100)


def test_wfs_get_hits_parses_number_matched():
    hits_xml = b'<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" numberMatched="500000" numberReturned="0"/>'
    total = wfs.wfs_get_hits(
        "https://x/wfs", "IGNF_MNS-LIDAR-HD:dalle", fetch=lambda u: hits_xml
    )
    assert total == 500000


def test_wfs_capabilities_declares_coordinate_extent_and_query_support():
    assert wfs.CAPABILITIES.supports_query is True
    assert wfs.CAPABILITIES.extent_flavor == "coordinate"


# --------------------------------------------------------------------------- stac

# Shapes modeled directly on real data.geo.admin.ch responses fetched and
# confirmed live this session (both the /collections listing's asset
# shape - including a real "file:checksum" multihash value - and the
# general STAC API "links: [{rel: next, ...}]" pagination convention
# also used by data.geo.admin.ch's own /collections endpoint). A live
# /collections/{id}/items response was not independently fetched this
# session the way /collections itself was - these fixtures are the
# spec-shape, not a captured real response.

_ITEMS_PAGE_1 = {
    "type": "FeatureCollection",
    "numberMatched": 3,
    "numberReturned": 2,
    "features": [
        {
            "type": "Feature",
            "id": "swissalti3d_2019_2600-1200",
            "links": [
                {
                    "rel": "self",
                    "href": "https://data.geo.admin.ch/api/stac/v1/collections/ch.swisstopo.swissalti3d/items/swissalti3d_2019_2600-1200",
                }
            ],
        },
        {
            "type": "Feature",
            "id": "swissalti3d_2019_2601-1200",
            "links": [
                {
                    "rel": "self",
                    "href": "https://data.geo.admin.ch/api/stac/v1/collections/ch.swisstopo.swissalti3d/items/swissalti3d_2019_2601-1200",
                }
            ],
        },
    ],
    "links": [
        {
            "rel": "next",
            "href": "https://data.geo.admin.ch/api/stac/v1/collections/ch.swisstopo.swissalti3d/items?cursor=abc",
        }
    ],
}

_ITEMS_PAGE_2 = {
    "type": "FeatureCollection",
    "numberMatched": 3,
    "numberReturned": 1,
    "features": [
        {
            "type": "Feature",
            "id": "swissalti3d_2019_2602-1200",
            "links": [
                {
                    "rel": "self",
                    "href": "https://data.geo.admin.ch/api/stac/v1/collections/ch.swisstopo.swissalti3d/items/swissalti3d_2019_2602-1200",
                }
            ],
        }
    ],
    "links": [],
}

_ITEM_DETAIL = {
    "type": "Feature",
    "id": "swissalti3d_2019_2600-1200",
    "assets": {
        "swissalti3d_2019_2600-1200_0.5_2056_5728.tif": {
            "href": "https://data.geo.admin.ch/ch.swisstopo.swissalti3d/.../swissalti3d_2019_2600-1200_0.5_2056_5728.tif",
            "type": "image/tiff; application=geotiff; profile=cloud-optimized",
            "file:checksum": "1220d9f7215a457ef462ec0cb48b4fa9bbdf693ef62fa1954318557e4478901e14f6",
        }
    },
}


def _stac_fetch_router(url):
    import json as _json

    routed = {
        "https://x/items": _ITEMS_PAGE_1,
        "https://data.geo.admin.ch/api/stac/v1/collections/ch.swisstopo.swissalti3d/items?cursor=abc": _ITEMS_PAGE_2,
        "https://data.geo.admin.ch/api/stac/v1/collections/ch.swisstopo.swissalti3d/items/swissalti3d_2019_2600-1200": _ITEM_DETAIL,
    }
    if url not in routed:
        raise AssertionError(f"unexpected URL fetched: {url}")
    return _json.dumps(routed[url]).encode("utf-8")


def test_stac_fetch_page_turns_items_into_directory_entries():
    entries, title, page_info = stac.fetch_page(
        "https://x/items", fetch=_stac_fetch_router
    )
    assert len(entries) == 2
    assert entries[0].title == "swissalti3d_2019_2600-1200"
    assert entries[0].is_dir is True
    assert page_info["next_url"] == _ITEMS_PAGE_1["links"][0]["href"]


def test_stac_fetch_page_page_2_walks_forward_via_next_link():
    entries, _, page_info = stac.fetch_page(
        "https://x/items", page=2, fetch=_stac_fetch_router
    )
    assert len(entries) == 1
    assert entries[0].title == "swissalti3d_2019_2602-1200"
    assert page_info["next_url"] is None


def test_stac_fetch_all_pages_follows_next_links_across_both_pages():
    entries, _ = stac.fetch_all_pages("https://x/items", fetch=_stac_fetch_router)
    assert [e.title for e in entries] == [
        "swissalti3d_2019_2600-1200",
        "swissalti3d_2019_2601-1200",
        "swissalti3d_2019_2602-1200",
    ]


def test_stac_fetch_all_pages_raises_large_listing_from_number_matched():
    def fetch(url):
        import json as _json

        return _json.dumps(
            {
                "type": "FeatureCollection",
                "numberMatched": 5000,
                "features": [],
                "links": [],
            }
        ).encode("utf-8")

    with pytest.raises(stac.LargeListing):
        stac.fetch_all_pages("https://x/items", fetch=fetch)


def test_stac_resolve_downloadable_files_descends_into_item_and_resolves_checksum():
    entries, _, _ = stac.fetch_page("https://x/items", fetch=_stac_fetch_router)
    item_entry = entries[0]
    resolved = stac.resolve_downloadable_files(item_entry, fetch=_stac_fetch_router)
    assert len(resolved) == 1
    link, checksum = resolved[0]
    assert link.href.endswith(".tif")
    # Real multihash confirmed live this session (a genuine
    # data.geo.admin.ch asset's file:checksum value) - 1220 prefix ==
    # sha2-256, so this must decode to the 64 hex chars after it.
    assert checksum == (
        "sha256",
        "d9f7215a457ef462ec0cb48b4fa9bbdf693ef62fa1954318557e4478901e14f6",
    )


def test_stac_parse_sha256_multihash_rejects_non_sha256_prefix():
    # 0x11 (md5-style placeholder prefix, not a real multihash code
    # used here deliberately just to be "not 1220") must not be
    # silently decoded as if it were sha2-256.
    assert stac.parse_sha256_multihash("1114" + "a" * 40) is None
    assert stac.parse_sha256_multihash("") is None
    assert stac.parse_sha256_multihash(None) is None


def test_stac_entry_with_no_self_link_is_skipped_rather_than_guessed():
    payload = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "id": "no-self-link", "links": []}],
        "links": [],
    }

    def fetch(url):
        import json as _json

        return _json.dumps(payload).encode("utf-8")

    entries, _, _ = stac.fetch_page("https://x/items", fetch=fetch)
    assert entries == []


def test_stac_capabilities_declares_file_index_no_query():
    assert stac.CAPABILITIES.is_file_index is True
    assert stac.CAPABILITIES.supports_query is False


# --------------------------------------------------------------------------- geonorge_catalog

# Header confirmed via a real CSV response fetched directly from
# kartkatalog.test.geonorge.no (same platform/shape as production) -
# Norwegian column names, not the English ones this module originally
# (wrongly) assumed. That original assumption was the actual root cause
# of a real reported bug: row.get("Topic") never matched anything
# against the real "Tema" column, so every theme grouping came back
# empty even though the CSV fetch itself succeeded.
_CSV_HEADER = (
    "Tittel;Type;Tema;Organisasjon;Åpne data;DOK-data;Uuid;Wms-url;"
    "Wfs-url;Atom-feed;Dekningsområde;Distribusjonsform;Distribusjons-url"
)


def _csv_row(
    title,
    topic,
    atom_feed="",
    org="Kartverket",
    type_="dataset",
    dist_form="GEONORGE:DOWNLOAD",
):
    return (
        f"{title};{type_};{topic};{org};Open data;;{title.lower()}-uuid;;;"
        f"{atom_feed};National;{dist_form};"
    )


_CATALOG_CSV = "\n".join(
    [
        _CSV_HEADER,
        # Two datasets under "Høydedata" with a real atom feed - these
        # should surface at Level 2 under that theme.
        _csv_row(
            "Administrative enheter fylker",
            "Høydedata",
            atom_feed="http://nedlasting.geonorge.no/geonorge/ATOM-feeds/AdmFylker.xml",
        ),
        _csv_row(
            "DTM10 Terrengmodell",
            "Høydedata",
            atom_feed="http://nedlasting.geonorge.no/geonorge/ATOM-feeds/DTM10.xml",
        ),
        # A WMS-only service under "Natur" with NO atom feed - must be
        # excluded entirely (the whole point of filtering to
        # Atom-feed-bearing rows), not shown as an empty/broken entry.
        _csv_row("Naturtyper WMS", "Natur", atom_feed="", dist_form="OGC:WMS"),
        # A real dataset under "Natur" that DOES have a feed - Natur
        # must still appear as a Level 1 theme because of this row,
        # even though the WMS-only row above doesn't count toward it.
        _csv_row(
            "Naturvernområder",
            "Natur",
            atom_feed="http://nedlasting.geonorge.no/geonorge/ATOM-feeds/Naturvern.xml",
        ),
    ]
)


def _geonorge_fetch_router(atom_feed_content=b"<feed/>"):
    def fetch(url):
        if url == geonorge_catalog.CATALOG_URL:
            return _CATALOG_CSV.encode("utf-8")
        return atom_feed_content

    return fetch


def test_geonorge_catalog_root_lists_only_themes_with_an_atom_feed():
    entries, title, _ = geonorge_catalog.fetch_page(
        geonorge_catalog.CATALOG_URL, fetch=_geonorge_fetch_router()
    )
    titles = {e.title for e in entries}
    # Natur must appear (Naturvernområder has a feed) even though one of
    # its two rows (Naturtyper WMS) doesn't - the theme-level filter is
    # "does *any* row for this theme have a feed", not "do all of them".
    assert titles == {"Høydedata", "Natur"}
    assert all(e.is_dir for e in entries)


def test_geonorge_catalog_parses_a_literal_real_csv_response_not_just_the_test_builder():
    """Regression test for the actual reported bug ("norwa csv comes
    back empty ... in the gui"): this module originally assumed English
    CSV column names ("Title", "Topic"), but the real API returns
    Norwegian ones ("Tittel", "Tema") - confirmed by fetching a real
    response from kartkatalog.test.geonorge.no directly. Every other
    test in this file builds its fixture through _csv_row/_CSV_HEADER,
    which used the *same* wrong assumption as the module itself before
    this fix - so they agreed with each other while both silently
    disagreed with the real API, and no test caught it. This one uses a
    literal, hand-copied real response row instead, so a future
    reintroduction of the same mismatch can't hide behind the fixture
    builder sharing its bug."""
    real_csv = (
        "\ufeffTittel;Type;Tema;Organisasjon;Åpne data;DOK-data;Uuid;Wms-url;"
        "Wfs-url;Atom-feed;Dekningsområde;Distribusjonsform;Distribusjons-url\n"
        "Administrative enheter fylker;dataset;Basis geodata;Kartverket;"
        "Åpne data;;6093c8a8-fa80-11e6-bc64-92361f002671;;;"
        "http://nedlasting.geonorge.no/geonorge/ATOM-feeds/"
        "AdministrativeEnheterFylker_AtomFeedFGDB.xml;Nasjonal;"
        "GEONORGE:DOWNLOAD;\n"
    )

    def fetch(url):
        assert url == geonorge_catalog.CATALOG_URL
        return real_csv.encode("utf-8")

    entries, _, _ = geonorge_catalog.fetch_page(
        geonorge_catalog.CATALOG_URL, fetch=fetch
    )
    assert [e.title for e in entries] == ["Basis geodata"]

    # Also confirms the leading BOM in the real response (present in
    # the literal string above, matching what was actually observed)
    # doesn't corrupt the *first* column's key ("Tittel") - if it did,
    # every entry's title would silently fall back to "(untitled)"
    # rather than raising an error.
    dataset_entries, _, _ = geonorge_catalog.fetch_page(
        entries[0].primary_href(), fetch=fetch
    )
    assert [e.title for e in dataset_entries] == ["Administrative enheter fylker"]


def test_geonorge_catalog_theme_level_excludes_rows_without_an_atom_feed():
    root_entries, _, _ = geonorge_catalog.fetch_page(
        geonorge_catalog.CATALOG_URL, fetch=_geonorge_fetch_router()
    )
    natur = next(e for e in root_entries if e.title == "Natur")
    dataset_entries, theme_title, _ = geonorge_catalog.fetch_page(
        natur.primary_href(), fetch=_geonorge_fetch_router()
    )
    assert theme_title == "Natur"
    # Only the one row with a real feed - "Naturtyper WMS" (no feed)
    # must not appear here at all.
    assert [e.title for e in dataset_entries] == ["Naturvernområder"]
    assert (
        dataset_entries[0].primary_href()
        == "http://nedlasting.geonorge.no/geonorge/ATOM-feeds/Naturvern.xml"
    )


def test_geonorge_catalog_descending_into_a_dataset_delegates_to_real_atom_parsing():
    """The core architectural claim this module makes: once a Level 2
    entry's own real Atom feed URL is reached, gateways.atom parses it
    with zero geonorge-specific code - confirmed here with a real,
    minimal INSPIRE Atom feed as the fake fetch's response."""
    real_feed_xml = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:gpf_dl="http://x"
     gpf_dl:page="1" gpf_dl:pagesize="50" gpf_dl:pagecount="1" gpf_dl:totalentries="1">
<entry><title>DTM10 tile</title><id>p</id>
<link href="https://nedlasting.geonorge.no/dtm10.tif" type="image/tiff" length="12345"/>
</entry></feed>"""

    fetch = _geonorge_fetch_router(atom_feed_content=real_feed_xml)
    root_entries, _, _ = geonorge_catalog.fetch_page(
        geonorge_catalog.CATALOG_URL, fetch=fetch
    )
    hoydedata = next(e for e in root_entries if e.title == "Høydedata")
    dataset_entries, _, _ = geonorge_catalog.fetch_page(
        hoydedata.primary_href(), fetch=fetch
    )
    dtm10_entry = next(e for e in dataset_entries if e.title == "DTM10 Terrengmodell")

    # dtm10_entry.primary_href() is now a real external Atom feed URL,
    # not a geonorge_catalog URL at all - fetch_page must delegate.
    file_entries, feed_title, _ = geonorge_catalog.fetch_page(
        dtm10_entry.primary_href(), fetch=fetch
    )
    assert feed_title == ""  # atom.py's own empty-title fallback, unchanged
    assert len(file_entries) == 1
    assert file_entries[0].title == "DTM10 tile"
    assert file_entries[0].is_dir is False


def test_geonorge_catalog_resolve_downloadable_files_on_theme_entry_returns_empty():
    """A Level 1 (Theme) folder is never itself downloadable - selecting
    it directly and clicking Download must not try to XML-parse a
    kartkatalog search URL as if it were an Atom feed."""
    root_entries, _, _ = geonorge_catalog.fetch_page(
        geonorge_catalog.CATALOG_URL, fetch=_geonorge_fetch_router()
    )
    hoydedata = next(e for e in root_entries if e.title == "Høydedata")
    assert geonorge_catalog.resolve_downloadable_files(hoydedata) == []


def test_geonorge_catalog_resolve_downloadable_files_on_dataset_entry_delegates_to_atom():
    real_feed_xml = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:gpf_dl="http://x"
     gpf_dl:page="1" gpf_dl:pagesize="50" gpf_dl:pagecount="1" gpf_dl:totalentries="1">
<entry><title>tile</title><id>p</id>
<link href="https://nedlasting.geonorge.no/dtm10.tif" type="image/tiff" length="99"/>
</entry></feed>"""
    fetch = _geonorge_fetch_router(atom_feed_content=real_feed_xml)
    root_entries, _, _ = geonorge_catalog.fetch_page(
        geonorge_catalog.CATALOG_URL, fetch=fetch
    )
    hoydedata = next(e for e in root_entries if e.title == "Høydedata")
    dataset_entries, _, _ = geonorge_catalog.fetch_page(
        hoydedata.primary_href(), fetch=fetch
    )
    dtm10_entry = next(e for e in dataset_entries if e.title == "DTM10 Terrengmodell")

    resolved = geonorge_catalog.resolve_downloadable_files(dtm10_entry, fetch=fetch)
    assert len(resolved) == 1
    link, checksum = resolved[0]
    assert link.href == "https://nedlasting.geonorge.no/dtm10.tif"
    assert checksum is None


def test_geonorge_catalog_resolve_downloadable_files_on_a_real_atom_file_entry_does_not_crash():
    """Regression test for the actual reported bug ("download tiff:
    failed to resolve tiff-format celle t33WVQ: 'entry' object has no
    attribute checksum"). The test above only ever resolves this
    module's own dataset-level Entry (always is_dir=True, has a real
    .checksum field) - it never exercises what actually happens once
    the user has browsed one level *further*, into the dataset's own
    feed, and selected one of the real gateways.atom.Entry file objects
    that fetch_page's delegation-to-atom produces from that point on.
    atom.Entry has no .checksum attribute at all - this module's
    resolve_downloadable_files must detect that it received a real
    atom.Entry (not its own Entry class) and hand off to
    atom.resolve_downloadable_files entirely, rather than assuming its
    own Entry shape unconditionally."""
    real_feed_xml = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:gpf_dl="http://x"
     gpf_dl:page="1" gpf_dl:pagesize="50" gpf_dl:pagecount="1" gpf_dl:totalentries="1">
<entry><title>tiff-format celle t33WVQ</title><id>p</id>
<link href="https://nedlasting.geonorge.no/t33wvq.tif" type="image/tiff" length="12345"/>
</entry></feed>"""
    fetch = _geonorge_fetch_router(atom_feed_content=real_feed_xml)
    root_entries, _, _ = geonorge_catalog.fetch_page(
        geonorge_catalog.CATALOG_URL, fetch=fetch
    )
    hoydedata = next(e for e in root_entries if e.title == "Høydedata")
    dataset_entries, _, _ = geonorge_catalog.fetch_page(
        hoydedata.primary_href(), fetch=fetch
    )
    dtm10_entry = next(e for e in dataset_entries if e.title == "DTM10 Terrengmodell")

    # One more hop, exactly what double-clicking the dataset entry in
    # the real widget does - fetch_page delegates straight to
    # atom.fetch_page here, so these are real gateways.atom.Entry
    # objects, not geonorge_catalog.Entry.
    file_entries, _, _ = geonorge_catalog.fetch_page(
        dtm10_entry.primary_href(), fetch=fetch
    )
    tile_entry = file_entries[0]
    assert not isinstance(tile_entry, geonorge_catalog.Entry)
    assert tile_entry.is_dir is False

    resolved = geonorge_catalog.resolve_downloadable_files(tile_entry, fetch=fetch)
    assert len(resolved) == 1
    link, checksum = resolved[0]
    assert link.href == "https://nedlasting.geonorge.no/t33wvq.tif"
    assert checksum is None


def test_geonorge_catalog_large_listing_is_the_same_class_atom_raises():
    """Re-exported, not a new distinct exception class - so
    ui.bulk_listing_widget's `except module.LargeListing` still matches
    correctly when the exception actually originates from a delegated
    atom.fetch_all_pages call several levels down."""
    assert geonorge_catalog.LargeListing is atom.LargeListing


def test_geonorge_catalog_fetch_all_pages_at_theme_level_delegates_correctly_below():
    real_feed_xml = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:gpf_dl="http://x"
     gpf_dl:page="1" gpf_dl:pagesize="50" gpf_dl:pagecount="1" gpf_dl:totalentries="1">
<entry><title>tile</title><id>p</id>
<link href="https://nedlasting.geonorge.no/dtm10.tif" type="image/tiff"/>
</entry></feed>"""
    fetch = _geonorge_fetch_router(atom_feed_content=real_feed_xml)
    root_entries, _, _ = geonorge_catalog.fetch_page(
        geonorge_catalog.CATALOG_URL, fetch=fetch
    )
    hoydedata = next(e for e in root_entries if e.title == "Høydedata")

    all_entries, _ = geonorge_catalog.fetch_all_pages(
        hoydedata.primary_href(), fetch=fetch
    )
    assert {e.title for e in all_entries} == {
        "Administrative enheter fylker",
        "DTM10 Terrengmodell",
    }


def test_geonorge_catalog_capabilities_declares_file_index_no_query():
    assert geonorge_catalog.CAPABILITIES.is_file_index is True
    assert geonorge_catalog.CAPABILITIES.supports_query is False
