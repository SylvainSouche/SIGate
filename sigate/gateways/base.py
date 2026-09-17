"""
gateways.base - shared base for all SIGate gateway modules (Atom, WFS, FTP,
STAC, WMTS/WMS).

Each gateway module is pure Python with no Qt/QGIS dependency. Any
function that needs to fetch something takes an optional `fetch`
parameter: a plain callable, URL in, raw response bytes out. This keeps
gateway modules independently testable and lets the calling application
supply its own transport (e.g. one that respects a proxy configuration)
without any gateway module needing to import an HTTP client itself. If
`fetch` isn't given, a module falls back to its own small default
implementation.
"""

from dataclasses import dataclass
from typing import Callable, Optional

# A fetch function: URL in, raw response bytes out. Implementations that
# need extra context (auth headers, a particular transport backend) should
# carry that via functools.partial or a small wrapper object at the call
# site; this module only depends on the URL-in-bytes-out shape.
FetchFunction = Callable[[str], bytes]


@dataclass(frozen=True)
class GatewayCapabilities:
    """Declares what a gateway type can do. Not every gateway supports
    every capability - flags default to the most conservative
    (unsupported) value, so a gateway module only needs to override what
    it actually has."""

    # Server-side attribute/spatial query support (e.g. WFS BBOX +
    # CQL_FILTER, STAC bbox + datetime) - False for gateways that only
    # support browsing, with no query mechanism.
    supports_query: bool = False

    # None | "coordinate" | "categorical". A "coordinate" gateway accepts
    # an arbitrary bounding box; a "categorical" one is organised by named
    # divisions (e.g. administrative regions) baked into its resource
    # hierarchy, navigated by name rather than by coordinates. A gateway
    # with no spatial extent concept at all leaves this None.
    extent_flavor: Optional[str] = None

    # True if a source behind this gateway can be asked for a preferred
    # content language. This is a hint at the gateway-type level, not a
    # guarantee for every source using it - checked per source in
    # practice.
    supports_locale: bool = False

    # True if listing entries represent files/folders meant to be
    # downloaded, as opposed to features to be added directly as a native
    # vector layer.
    is_file_index: bool = False


class GatewayError(Exception):
    """Base class for gateway-specific errors, letting callers catch
    broadly (GatewayError) or narrowly (a specific subclass) as needed."""


class DownloadCancelled(GatewayError):
    """Raised by a download transport when a should_continue callback
    starts returning False mid-transfer - not a transport failure, but
    needs to propagate distinctly from one so callers (download.pipeline)
    can report "cancelled" rather than "failed" for the interrupted
    file, and stop attempting any remaining ones."""


# Real, confirmed non-standard CRS declarations seen in the wild,
# mapped to a complete "AUTHORITY:CODE" identifier - not a general
# alias table (there is no way to anticipate every name a server might
# use), just the specific cases a real reported bug has confirmed
# occur, as a last-resort fallback for a genuinely bare name with no
# colon-delimited structure to parse authority out of at all. IGN's
# private WMTS (data.geopf.fr/private/wmts) declares at least one
# TileMatrixSet's CRS using IGN's own "IGNF" authority, not EPSG at
# all - confirmed by matching QGIS's own native "Add Layer from
# WMS/WMTS" dialog, connecting to this exact same layer, which
# resolves it to "IGNF:LAMB93". (An earlier version of this fix mapped
# "LAMB93" to "EPSG:2154" - a reasonable guess at the time, given only
# the single garbage value "EPSG:LAMB93" to go on, but wrong: nothing
# in a genuinely bare "LAMB93" string could have told QGIS's own
# parser to produce "IGNF" specifically, which is why the real fix
# below is parsing the URN's actual declared authority properly,
# rather than continuing to guess at aliases.)
_KNOWN_NON_STANDARD_CRS_ALIASES = {"LAMB93": "IGNF:LAMB93"}


def urn_to_epsg(urn_or_code: str) -> str:
    """Converts a CRS URN, as WMTS/WFS capabilities documents declare
    it, to a plain "AUTHORITY:CODE" form - most commonly
    "EPSG:<code>" (e.g. "urn:ogc:def:crs:EPSG::2154" -> "EPSG:2154"),
    but NOT always: a URN's authority segment is not always EPSG, and
    this function preserves whatever authority a URN actually
    declares rather than assuming EPSG unconditionally (a real,
    confirmed bug this function itself had until now - see
    _KNOWN_NON_STANDARD_CRS_ALIASES's own comment for the full story:
    IGN's private WMTS declares "urn:ogc:def:crs:IGNF::LAMB93" for at
    least one TileMatrixSet, using its own "IGNF" authority, not
    EPSG). Kept the historical name (used throughout this project
    since before this was understood) rather than renaming it project-
    wide for what turned out to be a documentation-accuracy concern,
    not a functional one - every real call site just needs a resolved
    "AUTHORITY:CODE" identifier, regardless of which authority it
    turns out to be. Leaves an already-plain "AUTHORITY:CODE" pair
    unchanged, for any authority. Shared between gateways.wmts_wms
    (TileMatrixSet SupportedCRS) and gateways.wfs (FeatureType
    DefaultCRS/DefaultSRS) - both declare CRS the same URN-shaped way.

    Args:
        urn_or_code: a non-empty CRS identifier - a full OGC CRS URN
            (urn:ogc:def:crs:<AUTHORITY>:<VERSION>:<CODE>), an
            already-plain "AUTHORITY:CODE" pair, or (a real, confirmed
            case, not just a theoretical one) a bare non-standard CRS
            name with no colon-delimited structure at all.

    Returns:
        A plain "AUTHORITY:CODE" form: the URN's own declared
        authority and code when given a real 7-segment OGC CRS URN;
        the input unchanged when it's already a 2-segment
        "AUTHORITY:CODE" pair; a resolved alias from
        _KNOWN_NON_STANDARD_CRS_ALIASES for a genuinely bare,
        unstructured name matching one of the specific real cases
        confirmed there; otherwise the original input completely
        unchanged. An unresolved value passed through as-is will fail
        a downstream QgsCoordinateReferenceSystem(...).isValid()
        check, which is the correct place for this to surface - not
        silently accepted here as if it were valid, and not fabricated
        into a plausible-looking but wrong value the way this
        function's own real reported bug once did.

    Raises:
        ValueError: if urn_or_code is empty or not a string - an empty
            input would otherwise silently produce the bare, invalid
            string "EPSG:", which QGIS's own provider accepts without
            complaint and then fails to actually use, a far less
            diagnosable failure than raising here.
    """
    if not isinstance(urn_or_code, str) or not urn_or_code.strip():
        raise ValueError("urn_or_code must be a non-empty string")
    parts = urn_or_code.split(":")
    if len(parts) == 2:
        # Already a plain "AUTHORITY:CODE" pair - EPSG:2154,
        # IGNF:LAMB93, or any other authority QGIS/PROJ might
        # recognize. Not specifically an EPSG check - any two-segment
        # authority:code shape is left exactly as declared.
        return urn_or_code
    if (
        len(parts) == 7
        and parts[0].lower() == "urn"
        and parts[1].lower() == "ogc"
        and parts[4]
        and parts[6]
    ):
        # A real OGC CRS URN: urn:ogc:def:crs:<AUTHORITY>:<VERSION>:<CODE>
        # (VERSION, parts[5], is commonly empty - "urn:ogc:def:crs:EPSG::2154"
        # - and not needed here). The authority is parts[4], not
        # unconditionally "EPSG" - this is the actual fix (see this
        # function's own docstring and _KNOWN_NON_STANDARD_CRS_ALIASES'
        # comment for why assuming EPSG here was a real, confirmed bug).
        return f"{parts[4]}:{parts[6]}"
    candidate = parts[-1] if parts else urn_or_code
    if candidate.isdigit():
        return f"EPSG:{candidate}"
    alias = _KNOWN_NON_STANDARD_CRS_ALIASES.get(candidate.upper())
    if alias:
        return alias
    return urn_or_code
