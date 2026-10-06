"""
Tests for sigate.gateways.arcgis_rest - pure Python, canned responses
shaped like the real Arpa Piemonte SIVA MapServer's.
"""

import json
from urllib.parse import parse_qs, urlparse

import pytest

from sigate.gateways import arcgis_rest as ar

BASE = "https://example.test/arcgis/rest/services/x/SIVA/MapServer"

SERVICE = {
    "capabilities": "Map,Query,Data",
    "maxRecordCount": 2000,
    "spatialReference": {"wkid": 32632, "latestWkid": 32632},
    "layers": [
        {"id": 4, "name": "CARTE VALANGHE ", "parentLayerId": -1, "subLayerIds": [5]},
        {"id": 5, "name": "VALANGHE", "parentLayerId": 4, "subLayerIds": [6, 7]},
        {
            "id": 6,
            "name": "Valanghe documentate ",
            "parentLayerId": 5,
            "subLayerIds": None,
        },
        {
            "id": 7,
            "name": "Valanghe non documentate",
            "parentLayerId": 5,
            "subLayerIds": None,
        },
        {"id": 3, "name": "OPERE di DIFESA", "parentLayerId": -1, "subLayerIds": None},
    ],
}

LAYER = {
    "id": 6,
    "name": "Valanghe documentate ",
    "geometryType": "esriGeometryPolygon",
    "capabilities": "Map,Query,Data",
    "maxRecordCount": 2000,
    "objectIdField": "objectid",
    "extent": {"spatialReference": {"wkid": 32632}},
    "advancedQueryCapabilities": {"supportsPagination": True},
    "fields": [
        {"name": "objectid", "type": "esriFieldTypeOID", "alias": "OBJECTID"},
        {"name": "comune", "type": "esriFieldTypeString", "alias": "Comune"},
        {"name": "rilievo", "type": "esriFieldTypeDate", "alias": "Rilievo"},
        {"name": "Shape", "type": "esriFieldTypeGeometry", "alias": "Shape"},
    ],
}


def _fetch_returning(payload):
    seen = []

    def fetch(url):
        seen.append(url)
        return json.dumps(payload).encode("utf-8")

    fetch.seen = seen
    return fetch


def test_get_service_lists_layers_and_marks_groups():
    service = ar.arcgis_get_service(BASE, fetch=_fetch_returning(SERVICE))

    assert service.can_query and service.max_record_count == 2000
    assert service.wkid == 32632
    assert [layer.id for layer in service.queryable_layers()] == [6, 7, 3]
    assert service.layers[0].is_group and not service.layers[2].is_group


def test_path_for_names_the_group_ancestors():
    service = ar.arcgis_get_service(BASE, fetch=_fetch_returning(SERVICE))
    by_id = {layer.id: layer for layer in service.layers}

    assert (
        service.path_for(by_id[6]) == "CARTE VALANGHE › VALANGHE › Valanghe documentate"
    )
    assert service.path_for(by_id[3]) == "OPERE di DIFESA"


def test_get_layer_reads_fields_geometry_and_crs():
    fetch = _fetch_returning(LAYER)
    detail = ar.arcgis_get_layer(BASE, 6, fetch=fetch)

    assert fetch.seen == [f"{BASE}/6?f=json"]
    assert detail.can_query and detail.geometry_type == "esriGeometryPolygon"
    assert detail.object_id_field == "objectid"
    assert detail.epsg == "EPSG:32632"
    assert detail.text_fieldnames == [
        "objectid",
        "comune",
        "rilievo",
    ]  # no geometry column
    assert [f.is_date for f in detail.fields] == [False, False, True, False]


def test_get_layer_falls_back_to_the_oid_typed_field():
    payload = dict(LAYER)
    payload.pop("objectIdField")
    assert (
        ar.arcgis_get_layer(BASE, 6, fetch=_fetch_returning(payload)).object_id_field
        == "objectid"
    )


def test_query_url_carries_where_paging_and_an_envelope_in_its_own_crs():
    url = ar.arcgis_build_query_url(
        BASE,
        6,
        where="comune = 'Vinadio'",
        bbox=(1.0, 2.0, 3.0, 4.0),
        bbox_wkid=32632,
        offset=400,
        count=200,
    )
    query = parse_qs(urlparse(url).query)

    assert urlparse(url).path.endswith("/MapServer/6/query")
    assert query["where"] == ["comune = 'Vinadio'"]
    assert query["resultOffset"] == ["400"] and query["resultRecordCount"] == ["200"]
    assert query["geometry"] == ["1.0,2.0,3.0,4.0"]  # x,y order, always
    assert query["geometryType"] == ["esriGeometryEnvelope"]
    assert query["inSR"] == ["32632"]
    assert query["returnGeometry"] == ["false"] and query["f"] == ["json"]


def test_count_only_query_has_no_paging_parameters():
    query = parse_qs(
        urlparse(ar.arcgis_build_query_url(BASE, 6, count_only=True)).query
    )

    assert query["returnCountOnly"] == ["true"]
    assert "resultOffset" not in query and "resultRecordCount" not in query


def test_count_reads_the_servers_answer():
    assert ar.arcgis_count(BASE, 6, fetch=_fetch_returning({"count": 4091})) == 4091
    with pytest.raises(ar.ArcGisRestError):
        ar.arcgis_count(BASE, 6, fetch=_fetch_returning({}))


def test_query_rows_stringifies_values_and_converts_dates():
    fields = ar.arcgis_get_layer(BASE, 6, fetch=_fetch_returning(LAYER)).fields
    payload = {
        "exceededTransferLimit": True,
        "features": [
            {
                "attributes": {
                    "objectid": 1,
                    "comune": "Vinadio",
                    "rilievo": 1356998400000,
                }
            },
            {"attributes": {"objectid": 2, "comune": None, "rilievo": None}},
        ],
    }

    fieldnames, rows, more = ar.arcgis_query_rows(
        BASE, 6, fields, fetch=_fetch_returning(payload)
    )

    assert fieldnames == ["objectid", "comune", "rilievo"]
    assert rows[0] == {"objectid": "1", "comune": "Vinadio", "rilievo": "2013-01-01"}
    assert rows[1] == {"objectid": "2", "comune": "", "rilievo": ""}
    assert more is True


def test_query_rows_without_more_pages():
    _, rows, more = ar.arcgis_query_rows(
        BASE, 6, [], fetch=_fetch_returning({"features": []})
    )
    assert rows == [] and more is False


def test_an_arcgis_error_answered_with_http_200_is_raised():
    error = {
        "error": {
            "code": 400,
            "message": "Unable to complete operation.",
            "details": ["Invalid query parameters"],
        }
    }
    with pytest.raises(ar.ArcGisRestError) as excinfo:
        ar.arcgis_get_service(BASE, fetch=_fetch_returning(error))
    assert "Unable to complete operation" in str(excinfo.value)
    assert "Invalid query parameters" in str(excinfo.value)


def test_transport_failure_and_non_json_are_wrapped():
    def broken(url):
        raise OSError("connection reset")

    with pytest.raises(ar.ArcGisRestError, match="request failed"):
        ar.arcgis_get_service(BASE, fetch=broken)
    with pytest.raises(ar.ArcGisRestError, match="not a JSON answer"):
        ar.arcgis_get_service(BASE, fetch=lambda url: b"<html>maintenance</html>")


def test_object_id_filter_only_interpolates_integers():
    assert (
        ar.build_object_id_filter("objectid", ["3", " 7 ", "12"])
        == "objectid IN (3, 7, 12)"
    )
    assert ar.build_object_id_filter("objectid", ["3'; DROP", "x"]) == "1=0"
    assert ar.build_object_id_filter("objectid", ["3", "1 OR 1=1"]) == "objectid IN (3)"
