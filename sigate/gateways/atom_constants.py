"""
Constant names for the INSPIRE-Atom download-service protocol.
"""

ATOM_NAMESPACE = "{http://www.w3.org/2005/Atom}"
USER_AGENT = "sigate-atom-gateway/1.0"
ACCEPT_HEADER = "application/atom+xml,application/xml,*/*"

# Query parameter names. Both LIMIT and PAGE_SIZE are sent on every request:
# documented behavior uses "limit", but some real-world Atom feed servers
# expect "pageSize" instead. Sending both is harmless, and the actual
# effective page size is always read back from the response's own
# attributes (see PAGE_INFO_ATTRS) rather than assumed from either.
PARAM_PAGE = "page"
PARAM_LIMIT = "limit"
PARAM_PAGE_SIZE = "pageSize"

# <feed> root element attributes (in the gpf_dl: namespace on IGN's
# service; matched namespace-agnostically here since other Atom-feed
# implementations may bind a different prefix to the same attribute names)
# reporting the actual state of a paginated listing.
PAGE_INFO_ATTRS = ("page", "pagesize", "pagecount", "totalentries")
