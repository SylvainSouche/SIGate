"""
Shared constant names for OGC KVP (key-value-pair) web service requests -
the SERVICE=.../VERSION=.../REQUEST=... pattern common to WFS, WMS, WMTS,
and WCS. Centralized here so each parameter/value name is spelled once,
rather than repeated as a string literal in every gateway module that
uses it.
"""

# --- KVP parameter names ---
PARAM_SERVICE = "SERVICE"
PARAM_VERSION = "VERSION"
PARAM_REQUEST = "REQUEST"
PARAM_TYPENAMES = "TYPENAMES"
PARAM_COUNT = "COUNT"
PARAM_STARTINDEX = "STARTINDEX"
PARAM_BBOX = "BBOX"
PARAM_OUTPUTFORMAT = "OUTPUTFORMAT"
PARAM_RESULTTYPE = "RESULTTYPE"
PARAM_CQL_FILTER = "CQL_FILTER"
PARAM_SRSNAME = "SRSNAME"
# Confirmed real on geo.admin.ch's own WMS and WMTS (GetCapabilities,
# GetMap, GetFeatureInfo, GetLegendGraphic all support it) - not every
# WMS/WMTS server honours this parameter, but it's harmless to send
# regardless (an unrecognized parameter is normally just ignored).
PARAM_LANG = "lang"

# --- REQUEST values ---
REQUEST_GET_CAPABILITIES = "GetCapabilities"
REQUEST_GET_FEATURE = "GetFeature"
REQUEST_DESCRIBE_FEATURE_TYPE = "DescribeFeatureType"

# --- SERVICE values ---
SERVICE_WFS = "WFS"

# --- Protocol versions ---
WFS_VERSION_2_0_0 = "2.0.0"

# --- OUTPUTFORMAT / RESULTTYPE values ---
OUTPUT_FORMAT_CSV = "csv"
RESULT_TYPE_HITS = "hits"

# --- Capabilities-document element local names (matched namespace-agnostically) ---
ELEM_FEATURE_TYPE = "FeatureType"
ELEM_NAME = "Name"
ELEM_TITLE = "Title"
# WFS 2.0 uses DefaultCRS; WFS 1.1/1.0 uses DefaultSRS - both matched,
# namespace-agnostically, as the real declared CRS for a feature type's
# own geometry.
ELEM_DEFAULT_CRS = "DefaultCRS"
ELEM_DEFAULT_SRS = "DefaultSRS"

# --- Exception-report element local names ---
ELEM_EXCEPTION_REPORT = "ExceptionReport"
ELEM_SERVICE_EXCEPTION_REPORT = "ServiceExceptionReport"
ELEM_EXCEPTION_TEXT = "ExceptionText"
ELEM_SERVICE_EXCEPTION = "ServiceException"

# --- GetFeature response count attributes ---
# WFS 2.0 uses numberMatched; WFS 1.1 servers used numberOfFeatures instead.
ATTR_NUMBER_MATCHED = "numberMatched"
ATTR_NUMBER_OF_FEATURES = "numberOfFeatures"
