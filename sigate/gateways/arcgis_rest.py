"""
gateways.arcgis_rest - fetch/parse logic for Esri's ArcGIS REST API
(a MapServer or FeatureServer service), with no Qt dependency.

Not an OGC standard, but openly documented and so widely deployed it is a
de facto one, and many public sources publish vector data this way and
nothing else (e.g. Arpa Piemonte's SIVA avalanche service has a WMS but no
WFS). Three kinds of request matter here, all plain HTTP GETs returning
JSON:

  - the service root (`<service>?f=json`): which layers exist, nested in
    groups, plus service-wide limits;
  - one layer (`<service>/<id>?f=json`): its fields, geometry type,
    coordinate system and whether it can be queried;
  - a layer query (`<service>/<id>/query?...`): features, selected by a
    SQL `where` clause and/or a bounding box, paged with resultOffset /
    resultRecordCount (the server caps one page at maxRecordCount, usually
    1000-2000).

Two ArcGIS-specific behaviours worth knowing: an error comes back as
HTTP 200 with an {"error": {...}} body rather than an HTTP error status,
and a date field's value is epoch milliseconds, not a date string.

Network access goes through an injectable `fetch(url) -> bytes`, the same
convention as every other gateway module, so everything here is testable
with canned responses.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from .atom import http_get as _default_http_get
from .base import GatewayError

# Field types ArcGIS reports for a date column - values arrive as epoch ms.
_DATE_FIELD_TYPE = "esriFieldTypeDate"
# Field types a user can't meaningfully filter or display as text.
_NON_TEXT_FIELD_TYPES = {
    "esriFieldTypeGeometry",
    "esriFieldTypeBlob",
    "esriFieldTypeRaster",
}

DEFAULT_PAGE_SIZE = 200


class ArcGisRestError(GatewayError):
    """An ArcGIS REST request failed - either a transport error or the
    server's own {"error": {...}} answer."""


@dataclass(frozen=True)
class ArcGisLayerEntry:
    """One entry of a service's layer listing. A group layer (one with
    sub-layers) holds no features of its own and can't be queried."""

    id: int
    name: str
    parent_id: int  # -1 at the top level
    sub_layer_ids: Tuple[int, ...] = ()

    @property
    def is_group(self) -> bool:
        return bool(self.sub_layer_ids)


@dataclass(frozen=True)
class ArcGisService:
    layers: List[ArcGisLayerEntry]
    capabilities: str = ""
    max_record_count: int = 1000
    wkid: Optional[int] = None

    @property
    def can_query(self) -> bool:
        return "query" in self.capabilities.lower()

    def queryable_layers(self) -> List[ArcGisLayerEntry]:
        return [layer for layer in self.layers if not layer.is_group]

    def path_for(self, layer: ArcGisLayerEntry) -> str:
        """The layer's name preceded by its group ancestors: "VALANGHE >
        Valanghe documentate". A flat service just gives the bare name."""
        by_id = {entry.id: entry for entry in self.layers}
        names = [layer.name.strip()]
        parent = by_id.get(layer.parent_id)
        seen = {layer.id}
        while parent is not None and parent.id not in seen:
            names.append(parent.name.strip())
            seen.add(parent.id)
            parent = by_id.get(parent.parent_id)
        return " › ".join(reversed(names))


@dataclass(frozen=True)
class ArcGisField:
    name: str
    alias: str
    type: str

    @property
    def is_date(self) -> bool:
        return self.type == _DATE_FIELD_TYPE


@dataclass(frozen=True)
class ArcGisLayerDetail:
    id: int
    name: str
    geometry_type: str = ""
    fields: List[ArcGisField] = field(default_factory=list)
    object_id_field: Optional[str] = None
    max_record_count: int = 1000
    wkid: Optional[int] = None
    capabilities: str = ""
    supports_pagination: bool = True

    @property
    def can_query(self) -> bool:
        return "query" in self.capabilities.lower()

    @property
    def text_fieldnames(self) -> List[str]:
        return [f.name for f in self.fields if f.type not in _NON_TEXT_FIELD_TYPES]

    @property
    def epsg(self) -> Optional[str]:
        return f"EPSG:{self.wkid}" if self.wkid else None


def _get_json(url: str, fetch: Optional[Callable]) -> dict:
    fetch = fetch or _default_http_get
    try:
        raw = fetch(url)
    except Exception as e:
        raise ArcGisRestError(f"request failed: {e}") from e
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError) as e:
        raise ArcGisRestError(f"not a JSON answer from {url!r}") from e
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        error = payload["error"]
        details = "; ".join(str(d) for d in (error.get("details") or []))
        message = str(error.get("message") or "ArcGIS REST error")
        code = error.get("code")
        raise ArcGisRestError(
            f"{message}"
            + (f" ({details})" if details else "")
            + (f" [code {code}]" if code else "")
        )
    return payload


def _wkid_of(spatial_reference: Optional[dict]) -> Optional[int]:
    if not isinstance(spatial_reference, dict):
        return None
    wkid = spatial_reference.get("latestWkid") or spatial_reference.get("wkid")
    return int(wkid) if wkid else None


def arcgis_service_url(base_url: str) -> str:
    return base_url.rstrip("/")


def arcgis_get_service(
    base_url: str, fetch: Optional[Callable] = None
) -> ArcGisService:
    """The service root: every layer (groups and leaves), the service's
    capabilities and its record limit."""
    payload = _get_json(f"{arcgis_service_url(base_url)}?f=json", fetch)
    layers = []
    for entry in payload.get("layers") or []:
        layers.append(
            ArcGisLayerEntry(
                id=int(entry["id"]),
                name=str(entry.get("name") or entry["id"]),
                parent_id=int(entry.get("parentLayerId", -1)),
                sub_layer_ids=tuple(int(i) for i in (entry.get("subLayerIds") or ())),
            )
        )
    return ArcGisService(
        layers=layers,
        capabilities=str(payload.get("capabilities") or ""),
        max_record_count=int(payload.get("maxRecordCount") or 1000),
        wkid=_wkid_of(payload.get("spatialReference")),
    )


def arcgis_get_layer(
    base_url: str, layer_id: int, fetch: Optional[Callable] = None
) -> ArcGisLayerDetail:
    """One layer's schema: fields, geometry type, coordinate system."""
    payload = _get_json(f"{arcgis_service_url(base_url)}/{int(layer_id)}?f=json", fetch)
    fields = [
        ArcGisField(
            name=str(f["name"]),
            alias=str(f.get("alias") or f["name"]),
            type=str(f.get("type") or ""),
        )
        for f in payload.get("fields") or []
        if f.get("name")
    ]
    object_id_field = payload.get("objectIdField")
    if not object_id_field:
        object_id_field = next(
            (f.name for f in fields if f.type == "esriFieldTypeOID"), None
        )
    advanced = payload.get("advancedQueryCapabilities") or {}
    extent = payload.get("extent") or {}
    return ArcGisLayerDetail(
        id=int(payload.get("id", layer_id)),
        name=str(payload.get("name") or layer_id),
        geometry_type=str(payload.get("geometryType") or ""),
        fields=fields,
        object_id_field=object_id_field,
        max_record_count=int(payload.get("maxRecordCount") or 1000),
        wkid=_wkid_of(extent.get("spatialReference")),
        capabilities=str(payload.get("capabilities") or ""),
        supports_pagination=bool(advanced.get("supportsPagination", True)),
    )


def arcgis_build_query_url(
    base_url: str,
    layer_id: int,
    where: str = "1=1",
    bbox: Optional[Tuple[float, float, float, float]] = None,
    bbox_wkid: Optional[int] = None,
    out_wkid: Optional[int] = None,
    out_fields: str = "*",
    offset: int = 0,
    count: Optional[int] = None,
    count_only: bool = False,
    return_geometry: bool = False,
    order_by: Optional[str] = None,
) -> str:
    """A layer query URL. `bbox` is (xmin, ymin, xmax, ymax) in `bbox_wkid`
    (always x,y: ArcGIS has no axis-order ambiguity, unlike OGC CRS
    authority order). Attributes come back as Esri JSON (f=json)."""
    params: Dict[str, str] = {
        "where": where.strip() or "1=1",
        "outFields": out_fields,
        "returnGeometry": "true" if return_geometry else "false",
        "f": "json",
    }
    if count_only:
        params["returnCountOnly"] = "true"
    else:
        params["resultOffset"] = str(int(offset))
        if count is not None:
            params["resultRecordCount"] = str(int(count))
        if order_by:
            params["orderByFields"] = order_by
    if bbox is not None:
        xmin, ymin, xmax, ymax = bbox
        params["geometry"] = f"{xmin},{ymin},{xmax},{ymax}"
        params["geometryType"] = "esriGeometryEnvelope"
        params["spatialRel"] = "esriSpatialRelIntersects"
        if bbox_wkid:
            params["inSR"] = str(int(bbox_wkid))
    if out_wkid and return_geometry:
        params["outSR"] = str(int(out_wkid))
    return f"{arcgis_service_url(base_url)}/{int(layer_id)}/query?{urlencode(params)}"


def arcgis_count(
    base_url: str,
    layer_id: int,
    where: str = "1=1",
    bbox: Optional[Tuple[float, float, float, float]] = None,
    bbox_wkid: Optional[int] = None,
    fetch: Optional[Callable] = None,
) -> int:
    url = arcgis_build_query_url(
        base_url, layer_id, where=where, bbox=bbox, bbox_wkid=bbox_wkid, count_only=True
    )
    payload = _get_json(url, fetch)
    if "count" not in payload:
        raise ArcGisRestError("the server's answer carried no count")
    return int(payload["count"])


def _display_value(value, field_type: str) -> str:
    if value is None:
        return ""
    if field_type == _DATE_FIELD_TYPE and isinstance(value, (int, float)):
        try:
            return (
                datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
            )
        except (OverflowError, OSError, ValueError):
            return str(value)
    return str(value)


def arcgis_query_rows(
    base_url: str,
    layer_id: int,
    fields: List[ArcGisField],
    where: str = "1=1",
    bbox: Optional[Tuple[float, float, float, float]] = None,
    bbox_wkid: Optional[int] = None,
    offset: int = 0,
    count: int = DEFAULT_PAGE_SIZE,
    fetch: Optional[Callable] = None,
) -> Tuple[List[str], List[Dict[str, str]], bool]:
    """One page of attribute rows (no geometry): (fieldnames, rows,
    more_available). `more_available` is the server's own
    exceededTransferLimit flag - true when a further page exists beyond
    this one. Values are strings (dates as ISO dates, null as "")."""
    url = arcgis_build_query_url(
        base_url,
        layer_id,
        where=where,
        bbox=bbox,
        bbox_wkid=bbox_wkid,
        offset=offset,
        count=count,
    )
    payload = _get_json(url, fetch)
    type_by_name = {f.name: f.type for f in fields}
    rows: List[Dict[str, str]] = []
    for feature in payload.get("features") or []:
        attributes = feature.get("attributes") or {}
        rows.append(
            {
                name: _display_value(value, type_by_name.get(name, ""))
                for name, value in attributes.items()
            }
        )
    fieldnames = (
        [
            f.name
            for f in fields
            if f.type not in _NON_TEXT_FIELD_TYPES and (not rows or f.name in rows[0])
        ]
        if fields
        else (list(rows[0]) if rows else [])
    )
    if rows and not fieldnames:
        fieldnames = list(rows[0])
    return fieldnames, rows, bool(payload.get("exceededTransferLimit"))


def build_object_id_filter(object_id_field: str, ids: List[str]) -> str:
    """A where clause selecting exactly the given object ids (numeric, so
    unquoted - and anything that isn't an integer is dropped rather than
    interpolated into the clause)."""
    clean = []
    for value in ids:
        try:
            clean.append(str(int(str(value).strip())))
        except ValueError:
            continue
    if not clean:
        return "1=0"
    return f"{object_id_field} IN ({', '.join(clean)})"
