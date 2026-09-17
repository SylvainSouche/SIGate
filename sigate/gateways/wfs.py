"""
gateways.wfs - WFS gateway.

Namespace-agnostic throughout (matches elements by local tag name,
ignoring whatever namespace prefix a given server binds), since real WFS
servers vary in which prefix they use for the same elements.
"""

import csv
import io
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from .atom import http_get as _default_http_get
from .base import GatewayCapabilities, GatewayError, urn_to_epsg
from .ogc_constants import (
    ATTR_NUMBER_MATCHED,
    ATTR_NUMBER_OF_FEATURES,
    ELEM_DEFAULT_CRS,
    ELEM_DEFAULT_SRS,
    ELEM_EXCEPTION_REPORT,
    ELEM_EXCEPTION_TEXT,
    ELEM_FEATURE_TYPE,
    ELEM_NAME,
    ELEM_SERVICE_EXCEPTION,
    ELEM_SERVICE_EXCEPTION_REPORT,
    ELEM_TITLE,
    OUTPUT_FORMAT_CSV,
    PARAM_BBOX,
    PARAM_COUNT,
    PARAM_CQL_FILTER,
    PARAM_OUTPUTFORMAT,
    PARAM_REQUEST,
    PARAM_RESULTTYPE,
    PARAM_SERVICE,
    PARAM_SRSNAME,
    PARAM_STARTINDEX,
    PARAM_TYPENAMES,
    PARAM_VERSION,
    REQUEST_DESCRIBE_FEATURE_TYPE,
    REQUEST_GET_CAPABILITIES,
    REQUEST_GET_FEATURE,
    RESULT_TYPE_HITS,
    SERVICE_WFS,
    WFS_VERSION_2_0_0,
)

CAPABILITIES = GatewayCapabilities(
    supports_query=True,
    extent_flavor="coordinate",
    supports_locale=False,
    is_file_index=False,  # the vector-layer use pattern is the default; a file-index use pattern is a per-source exception
)


class WfsServerException(GatewayError):
    """Raised when a WFS response is actually an OWS/WFS ExceptionReport
    rather than real data - e.g. an unsupported query parameter, or a
    field name that doesn't exist. Checking for this explicitly matters:
    without it, a rejected request can otherwise parse as an empty result
    set rather than surfacing the actual reason for the rejection."""


def _local(tag):
    return tag.split("}")[-1]


def _text(el):
    return el.text.strip() if el is not None and el.text else None


def wfs_build_url(base_url, params):
    parts = urlsplit(base_url)
    qs = parse_qs(parts.query)
    for k, v in params.items():
        qs[k] = [str(v)]
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(qs, doseq=True), "")
    )


@dataclass(frozen=True)
class WfsFeatureTypeInfo:
    name: str
    title: str
    default_crs: str = "EPSG:4326"
    """The feature type's own declared CRS (WFS 2.0's DefaultCRS, or
    DefaultSRS on a WFS 1.x server), converted from its URN form (e.g.
    "urn:ogc:def:crs:EPSG::4326") to plain "EPSG:4326". Real WFS
    capabilities documents always declare this per feature type -
    genuinely needed (not just nice to have) for building a spatial
    filter's geometry literal in the CRS the server actually expects,
    the same "don't mix CRS silently" lesson already learned building
    the WMTS export feature's own bbox handling. Falls back to
    EPSG:4326 (WFS's own long-standing conventional default) only if a
    feature type's capabilities entry is missing this element entirely -
    not silently assumed when it's actually declared."""


def wfs_get_capabilities(base_url, fetch=None):
    """Fetches WFS GetCapabilities. Returns
    (raw_bytes, [WfsFeatureTypeInfo, ...], full_url)."""
    fetch = fetch or _default_http_get
    url = wfs_build_url(
        base_url,
        {
            PARAM_SERVICE: SERVICE_WFS,
            PARAM_VERSION: WFS_VERSION_2_0_0,
            PARAM_REQUEST: REQUEST_GET_CAPABILITIES,
        },
    )
    raw = fetch(url)
    root = ET.fromstring(raw)
    types = []
    for ft in root.iter():
        if _local(ft.tag) != ELEM_FEATURE_TYPE:
            continue
        name = title = default_crs_raw = None
        for child in ft:
            local = _local(child.tag)
            if local == ELEM_NAME and name is None:
                name = (child.text or "").strip()
            elif local == ELEM_TITLE and title is None:
                title = (child.text or "").strip()
            elif (
                local in (ELEM_DEFAULT_CRS, ELEM_DEFAULT_SRS)
                and default_crs_raw is None
            ):
                default_crs_raw = (child.text or "").strip()
        if name:
            default_crs = (
                urn_to_epsg(default_crs_raw) if default_crs_raw else "EPSG:4326"
            )
            types.append(
                WfsFeatureTypeInfo(
                    name=name, title=title or "", default_crs=default_crs
                )
            )
    return raw, types, url


def wfs_build_getfeature_url(
    base_url,
    typename,
    count=200,
    start_index=0,
    bbox=None,
    output_format=None,
    cql_filter=None,
    srsname=None,
):
    params = {
        PARAM_SERVICE: SERVICE_WFS,
        PARAM_VERSION: WFS_VERSION_2_0_0,
        PARAM_REQUEST: REQUEST_GET_FEATURE,
        PARAM_TYPENAMES: typename,
        PARAM_COUNT: count,
    }
    if start_index:
        params[PARAM_STARTINDEX] = start_index
    if bbox:
        params[PARAM_BBOX] = bbox
    if output_format:
        params[PARAM_OUTPUTFORMAT] = output_format
    if cql_filter:
        # CQL_FILTER is a GeoServer vendor extension, not core OGC WFS -
        # not guaranteed to work on every WFS server.
        params[PARAM_CQL_FILTER] = cql_filter
    if srsname:
        # Real, confirmed gap this closes: with no SRSNAME at all, a
        # request's actual CRS - for both the geometry literal inside
        # cql_filter and the returned features' own geometries - is
        # left entirely to whatever a server defaults to when it's
        # omitted, which isn't reliably the feature type's own declared
        # DefaultCRS/DefaultSRS (GeoServer's CQL_FILTER parsing in
        # particular is known to sometimes fall back to a dataset's
        # underlying storage CRS instead, independent of what's
        # declared for WFS output purposes). QGIS's own native WFS
        # provider always sends this explicitly - not sending it here
        # was a real, meaningful difference from a client already known
        # to work correctly against the same server.
        params[PARAM_SRSNAME] = srsname
    return wfs_build_url(base_url, params)


# The real, standard GML 3.2 property types a geometry-valued element's
# own "type" attribute can have in a DescribeFeatureType schema (the
# authoritative OGC vocabulary for this, not a guess) - confirmed
# directly against a real DescribeFeatureType response fetched live this
# session for IGN's BDTOPO_V3:itineraire_autre
# ("type=\"gml:MultiCurvePropertyType\"" for its own real geometry
# field, "geometrie"). Matched by local name only (see this module's own
# namespace-agnostic convention) - a server could bind some other prefix
# to the GML namespace, though "gml" itself is near-universal in
# practice.
_GML_GEOMETRY_PROPERTY_TYPES = frozenset(
    {
        "GeometryPropertyType",
        "PointPropertyType",
        "CurvePropertyType",
        "LineStringPropertyType",
        "SurfacePropertyType",
        "PolygonPropertyType",
        "MultiPointPropertyType",
        "MultiCurvePropertyType",
        "MultiLineStringPropertyType",
        "MultiSurfacePropertyType",
        "MultiPolygonPropertyType",
        "MultiGeometryPropertyType",
    }
)


def wfs_build_describefeaturetype_url(base_url, typename):
    params = {
        PARAM_SERVICE: SERVICE_WFS,
        PARAM_VERSION: WFS_VERSION_2_0_0,
        PARAM_REQUEST: REQUEST_DESCRIBE_FEATURE_TYPE,
        PARAM_TYPENAMES: typename,
    }
    return wfs_build_url(base_url, params)


def wfs_describe_geometry_field(base_url, typename, fetch=None):
    """The real, standard, authoritative way to know a feature type's
    actual geometry field name: DescribeFeatureType, not a guess over
    commonly-seen column names. Real WFS clients (QGIS's own native
    provider included) use exactly this, never CSV-column-name
    heuristics - this project's own earlier approach
    (ui.wfs_widget.guess_geometry_field) was a real, confirmed source of
    repeated bugs precisely because it wasn't this.

    Fetches the feature type's own XML Schema and returns the name of
    whichever element's own type is a real GML geometry property type
    (gml:PointPropertyType, gml:MultiCurvePropertyType, ...) - the
    schema's own explicit, unambiguous answer, not an inference. Returns
    None if the request fails, the response isn't parseable XML, or (a
    real possibility - a feature type with no geometry at all, or one
    using a GML version/shape this function doesn't recognize) no
    matching element is found - callers should treat None as "fall back
    to a guess", not as an error to surface."""
    fetch = fetch or _default_http_get
    url = wfs_build_describefeaturetype_url(base_url, typename)
    try:
        raw = fetch(url)
    except Exception:
        return None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    for element in root.iter():
        if _local(element.tag) != "element":
            continue
        type_attr = element.get("type")
        name_attr = element.get("name")
        if not type_attr or not name_attr:
            continue
        # type_attr is a QName like "gml:MultiCurvePropertyType" - only
        # the local part (after any prefix) is checked, matching this
        # module's own namespace-agnostic convention.
        local_type = type_attr.split(":")[-1]
        if local_type in _GML_GEOMETRY_PROPERTY_TYPES:
            return name_attr
    return None


def build_field_search_cql(field, value, exact=True):
    """Builds a simple CQL_FILTER expression for searching one field.
    Single quotes in the value are escaped SQL-style (doubled) so a value
    containing one doesn't break the filter syntax."""
    escaped = value.replace("'", "''")
    if exact:
        return f"{field} = '{escaped}'"
    return f"{field} LIKE '%{escaped}%'"


def _check_for_exception_report(raw_bytes):
    try:
        root = ET.fromstring(raw_bytes)
    except ET.ParseError:
        return
    if _local(root.tag) in (ELEM_EXCEPTION_REPORT, ELEM_SERVICE_EXCEPTION_REPORT):
        texts = [
            (_text(el) or "").strip()
            for el in root.iter()
            if _local(el.tag) in (ELEM_EXCEPTION_TEXT, ELEM_SERVICE_EXCEPTION)
        ]
        message = (
            "; ".join(t for t in texts if t)
            or "server returned an exception with no readable text"
        )
        raise WfsServerException(message)


def wfs_parse_csv(raw_text):
    """Parses a CSV/TSV WFS response. Sniffs the delimiter rather than
    assuming comma or tab, since a server's CSV output format can vary in
    what it actually sends. Returns (fieldnames, [dict, ...])."""
    sample = raw_text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel  # comma-delimited fallback
    reader = csv.DictReader(io.StringIO(raw_text), dialect=dialect)
    rows = list(reader)
    return (reader.fieldnames or []), rows


def wfs_parse_gml_fallback(raw_bytes, typename):
    """Best-effort, heuristic GML feature parser - not a full GML
    implementation. Extracts direct child element (localname -> text)
    pairs for each feature found (matched by the typename's local part).
    Geometry-valued children (elements containing further nested elements
    rather than plain text) are reported as the literal string
    "[geometry]" rather than extracted - parsing arbitrary GML geometry
    encodings is out of scope for this gateway. Adequate for simple
    flat-attribute layers; not guaranteed for complex real-world GML."""
    root = ET.fromstring(raw_bytes)
    local_typename = typename.split(":")[-1].lower()
    fieldnames = []
    rows = []
    for el in root.iter():
        if _local(el.tag).lower() != local_typename:
            continue
        row = {}
        for child in el:
            clocal = _local(child.tag)
            value = "[geometry]" if len(child) > 0 else (child.text or "").strip()
            row[clocal] = value
            if clocal not in fieldnames:
                fieldnames.append(clocal)
        rows.append(row)
    return fieldnames, rows


def wfs_get_hits(base_url, typename, bbox=None, fetch=None):
    """Returns the real total feature count for a query via WFS 2.0's
    RESULTTYPE=hits, which returns just a count (as a numberMatched
    attribute on an otherwise-empty response) rather than actual feature
    data - useful before committing to a full multi-page walk of a large
    layer. Returns an int, or None if the server doesn't return a
    parseable count (some servers or older WFS versions may not support
    this properly; see wfs_fetch_all_pages for the fallback behavior)."""
    fetch = fetch or _default_http_get
    params = {
        PARAM_SERVICE: SERVICE_WFS,
        PARAM_VERSION: WFS_VERSION_2_0_0,
        PARAM_REQUEST: REQUEST_GET_FEATURE,
        PARAM_TYPENAMES: typename,
        PARAM_RESULTTYPE: RESULT_TYPE_HITS,
    }
    if bbox:
        params[PARAM_BBOX] = bbox
    url = wfs_build_url(base_url, params)
    raw = fetch(url)
    root = ET.fromstring(raw)
    for attr in (ATTR_NUMBER_MATCHED, ATTR_NUMBER_OF_FEATURES):
        val = root.get(attr)
        if val is not None:
            try:
                return int(val)
            except ValueError:
                pass
    return None


def wfs_get_features(
    base_url,
    typename,
    count=200,
    start_index=0,
    bbox=None,
    try_csv=True,
    cql_filter=None,
    srsname=None,
    fetch=None,
):
    """Fetches one page of features. Tries CSV output first (simpler and
    more reliable to parse than GML) unless try_csv is False; falls back
    to default GML output if the CSV request errors out or the response
    doesn't look like CSV/plain text (some servers ignore the requested
    output format and return their default XML anyway). Either path
    raises WfsServerException if the response turns out to be an
    exception report rather than real data. Returns
    (raw_bytes, fieldnames, rows, format_used, full_url).

    srsname, when given, is passed straight through to
    wfs_build_getfeature_url on both attempts - see that function's own
    comment on why this matters, not just a cosmetic addition."""
    fetch = fetch or _default_http_get
    if try_csv:
        url = wfs_build_getfeature_url(
            base_url,
            typename,
            count,
            start_index,
            bbox,
            output_format=OUTPUT_FORMAT_CSV,
            cql_filter=cql_filter,
            srsname=srsname,
        )
        try:
            raw = fetch(url)
        except Exception:
            raw = None
        if raw is not None:
            text = raw.decode("utf-8", errors="replace")
            if not text.lstrip().startswith("<"):
                fieldnames, rows = wfs_parse_csv(text)
                return raw, fieldnames, rows, "csv", url
            _check_for_exception_report(
                raw
            )  # raises if this XML is an error, not just non-csv data

    url = wfs_build_getfeature_url(
        base_url,
        typename,
        count,
        start_index,
        bbox,
        output_format=None,
        cql_filter=cql_filter,
        srsname=srsname,
    )
    raw = fetch(url)
    _check_for_exception_report(raw)
    fieldnames, rows = wfs_parse_gml_fallback(raw, typename)
    return raw, fieldnames, rows, "gml (best-effort parse)", url


def wfs_fetch_all_pages(
    base_url,
    typename,
    bbox=None,
    page_size=200,
    expected_total=None,
    max_pages=None,
    progress=None,
    fetch=None,
):
    """Fetches every page of a WFS listing.

    If expected_total is known (e.g. from wfs_get_hits), pagination is
    driven by that number - it keeps requesting until the running count
    reaches it, rather than stopping the moment a page comes back shorter
    than requested. This matters because a server can silently cap its
    response below whatever page_size was asked for; treating any short
    page as automatically "the last page" would then truncate the result
    without any error, a real risk once a layer's total runs into the
    hundreds of thousands. STARTINDEX is advanced by however many rows
    actually came back, not by page_size, for the same reason - if a page
    returns fewer rows than requested, the next request must skip only
    that many, or features would be silently dropped.

    If expected_total is not known (e.g. the server didn't support hits),
    this falls back to the short-page heuristic, since nothing better is
    available - and in that case results could in principle still be
    truncated if a server ever caps a page below what was requested
    without it being the true last page.

    max_pages is a hard safety cap. If not given, it's derived from
    expected_total (with headroom) when known, or defaults to 500
    otherwise."""
    fetch = fetch or _default_http_get
    if max_pages is None:
        if expected_total is not None and page_size > 0:
            max_pages = (
                expected_total // page_size
            ) + 10  # headroom in case of surprises
        else:
            max_pages = 500

    all_rows = []
    fieldnames = []
    fmt_used = None
    start = 0
    for page_num in range(max_pages):
        if progress:
            progress(page_num + 1, len(all_rows), expected_total)
        _, page_fieldnames, rows, fmt_used, _ = wfs_get_features(
            base_url,
            typename,
            count=page_size,
            start_index=start,
            bbox=bbox,
            fetch=fetch,
        )
        if page_fieldnames and not fieldnames:
            fieldnames = page_fieldnames
        all_rows.extend(rows)
        if not rows:
            break
        start += len(rows)  # advance by what actually came back, not what was requested
        if expected_total is not None:
            if start >= expected_total:
                break
        elif len(rows) < page_size:
            break
    return all_rows, fieldnames, fmt_used
