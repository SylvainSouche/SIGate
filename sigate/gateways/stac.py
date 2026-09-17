"""
gateways.stac - STAC API (Collection/Item/Asset) browsing gateway.

Written to satisfy the exact same functional contract ui.bulk_listing_widget
already calls against gateways.atom - fetch_page/fetch_all_pages/
resolve_downloadable_files/LargeListing, plus an Entry type exposing
.is_dir/.title/.primary_href() - so the Bulk tab can dispatch to either
gateway uniformly by looking up GatewayConfig.gateway_type, with no
per-gateway-type branching inside the widget itself. This mirrors
gateways.atom's own tree shape deliberately: a STAC Collection's Items
page is this gateway's directory listing, and each Item (which itself
"contains" one or more Assets) is this gateway's sub-directory - the same
two-level indirection atom.py's own resource/product/edition/download
tree has, just with STAC's own vocabulary.

Unlike gateways.atom, a STAC connection is scoped to one specific
Collection at construction time (sources.seed's GatewayConfig.extra
carries the collection id) rather than one universal root a user would
browse entirely - data.geo.admin.ch alone has ~570 collections spanning
every federal office, not a curated single tree the way IGN's Atom
capabilities document is. See docs/candidate_sources.md and
sources/seed.py for how each Swiss dataset (swissALTI3D, swissTLM3D,
swissSURFACE3D, ...) becomes its own separate gateway instance under one
source, the same pattern already used for IGN's public/private WMTS.

Checksum handling deliberately differs in shape from gateways.atom's:
atom's checksum is an external ".md5" sidecar link that still needs
fetching and parsing by the caller (see atom.find_checksum_link's own
docstring on why gateways/ has no dependency on download/). STAC embeds
its checksum inline on the asset itself (the "file" extension's
"file:checksum" property, a multihash - confirmed real and live this
session, "1220" + 64 hex chars = a sha2-256 multihash per the multicodec
table; other digest algorithms are recognised as present but not
decoded, since guessing at an unconfirmed encoding would be worse than
surfacing nothing). Rather than force that inline value through the
same "Link the caller must fetch" shape atom.py uses, resolve_downloadable_
files here returns an already-resolved (algorithm, hex_digest) tuple in
the same tuple position atom.py puts its Link - ui.bulk_listing_widget's
checksum step dispatches on the type of that second tuple element
(Link-like -> fetch and parse; plain tuple -> already resolved) rather
than assuming one shape.

STAC Items-endpoint pagination is implemented via each page's own
"next"-relation link (the OGC API - Features / STAC API standard
mechanism), not a `page=` query parameter the way atom.py's INSPIRE Atom
feeds use - data.geo.admin.ch's own /collections endpoint (fetched and
confirmed live this session) already follows this "links: [...,
{rel: next, href: ...}]" shape for its own pagination, and the STAC API
spec requires /items to do the same. This specific detail (a live
/items response) was not independently fetched and confirmed this
session the way /collections was - flagged here rather than silently
assumed equivalent.
"""

import json
import ssl
from typing import Callable, List, Optional, Tuple
from urllib.error import URLError
from urllib.request import Request, urlopen

from .base import GatewayCapabilities, GatewayError

try:
    import certifi

    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CONTEXT = ssl.create_default_context()

USER_AGENT = "SIGate-QGIS-Plugin/stac"
ACCEPT_HEADER = "application/json, application/geo+json"

# Large-listing threshold before fetch_all_pages asks for confirmation -
# same rationale and same default as atom.BIG_LISTING_THRESHOLD, kept as
# an independent constant since the two gateways' realistic listing
# sizes (a product's editions vs. a collection's tiles) aren't
# necessarily comparable enough to assume the same number is right for
# both long-term.
BIG_LISTING_THRESHOLD = 2000
ITEMS_PAGE_LIMIT = 100

CAPABILITIES = GatewayCapabilities(
    supports_query=False,
    extent_flavor=None,
    supports_locale=False,
    is_file_index=True,
)


class LargeListing(GatewayError):
    def __init__(self, total):
        self.total = total
        super().__init__(f"listing has {total} entries")


class Link:
    """Deliberately field-compatible with gateways.atom.Link (.href,
    .length, .title) so ui.bulk_listing_widget's existing rendering code
    (entry.file_link.length, filename derived from link.href) works
    unchanged regardless of which gateway produced the entry."""

    __slots__ = ("href", "length", "title", "media_type")

    def __init__(self, href, length=None, title=None, media_type=""):
        self.href = href
        self.length = length
        self.title = title
        self.media_type = media_type


class Entry:
    """Deliberately field-compatible with gateways.atom.Entry
    (.title, .is_dir, .file_link, .primary_href()) - see this module's
    own docstring for how a STAC Item plays the role of atom.py's
    "directory" and a STAC Asset plays the role of its "file"."""

    def __init__(self, title, is_dir, href, file_link=None, checksum=None):
        self.title = title
        self.is_dir = is_dir
        self._href = href
        self.file_link = file_link
        # (algorithm, hex_digest) or None - see module docstring on why
        # this is already-resolved rather than a Link needing a fetch.
        self.checksum = checksum

    def primary_href(self):
        return self._href


def http_get(url, timeout=30):
    headers = {"User-Agent": USER_AGENT, "Accept": ACCEPT_HEADER}
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout, context=_SSL_CONTEXT) as r:
            return r.read()
    except URLError as e:
        if isinstance(e.reason, ssl.SSLCertVerificationError):
            raise GatewayError(
                "SSL certificate verification failed. This is usually a local "
                "certificate-store issue rather than a problem with the "
                "server. Try upgrading the certifi package."
            ) from e
        raise


def parse_sha256_multihash(multihash_hex: str) -> Optional[str]:
    """Extracts a bare sha2-256 hex digest from a multihash-encoded
    string, if that's what it is. Multihash format: a 1-byte
    multicodec function code, a 1-byte digest-length byte, then the
    digest itself - all hex-encoded back to back. sha2-256 is code
    0x12 (18), and its digest is always 32 bytes (length byte 0x20,
    32) - so a real sha2-256 multihash is exactly "1220" followed by
    64 hex characters. Any other prefix is a different digest
    algorithm (or an already-decoded plain hex hash some other STAC
    catalog might expose some other way) - returns None rather than
    guessing, since decoding the wrong algorithm would silently produce
    a confidently-wrong verification rather than an honest "unknown"."""
    if not multihash_hex:
        return None
    candidate = multihash_hex.lower()
    if candidate.startswith("1220") and len(candidate) == 4 + 64:
        return candidate[4:]
    return None


def _asset_to_link(asset_key: str, asset: dict) -> Link:
    href = asset.get("href")
    length = asset.get("file:size")
    title = asset.get("title") or asset_key
    media_type = asset.get("type") or ""
    return Link(href=href, length=length, title=title, media_type=media_type)


def _asset_checksum(asset: dict) -> Optional[Tuple[str, str]]:
    multihash = asset.get("file:checksum") or asset.get("checksum:multihash")
    if not multihash:
        return None
    digest = parse_sha256_multihash(multihash)
    return ("sha256", digest) if digest else None


def _item_to_entries(item: dict) -> List[Entry]:
    """A single STAC Item's own Assets, each as a file Entry - what a
    "directory" produced by _collection_page_to_entries resolves to
    when descended into (mirrors atom.py: double-clicking a directory
    entry loads that entry's own primary_href(), which for an Item is
    this same Item's /items/{id} URL, self-describing)."""
    assets = item.get("assets") or {}
    entries = []
    for key, asset in assets.items():
        link = _asset_to_link(key, asset)
        if not link.href:
            continue
        entries.append(
            Entry(
                title=link.title,
                is_dir=False,
                href=link.href,
                file_link=link,
                checksum=_asset_checksum(asset),
            )
        )
    return entries


def _collection_page_to_entries(feature_collection: dict) -> List[Entry]:
    """A Collection's Items page - each Item becomes a directory-like
    Entry (is_dir=True), since it "contains" its own Assets rather than
    being downloadable itself."""
    features = feature_collection.get("features") or []
    entries = []
    for item in features:
        item_id = item.get("id") or "(untitled)"
        self_href = None
        for link in item.get("links") or []:
            if link.get("rel") == "self":
                self_href = link.get("href")
                break
        if self_href is None:
            # Not every implementation includes a self link on every
            # Item - fall back to constructing nothing rather than
            # guessing a URL shape; an Item with no discoverable self
            # link simply can't be descended into.
            continue
        entries.append(Entry(title=item_id, is_dir=True, href=self_href))
    return entries


def _next_link(payload: dict) -> Optional[str]:
    for link in payload.get("links") or []:
        if link.get("rel") == "next":
            return link.get("href")
    return None


def items_url_for_collection(base_url: str, collection_id: str) -> str:
    """Builds a Collection's Items-listing URL from a STAC API root and
    a collection id - the browse-root URL a Bulk Listing connection
    actually loads first (see ui.bulk_listing_widget's
    _current_entry_point_url). Kept here rather than string-built
    inline at the call site, matching this module's role as the place
    that owns STAC's own URL conventions."""
    return f"{base_url.rstrip('/')}/collections/{collection_id}/items"


def fetch_page(url, page=1, fetch=None):
    """Fetches one "page" of a STAC listing, matching atom.fetch_page's
    call shape (page=N) even though STAC's own pagination is link-based
    (a "next" relation), not page-number-based - the widget's Prev/Next
    buttons call fetch_page(url, page=N) directly, so this has to
    accept the same shape rather than force a widget-side special case
    for one gateway type.

    page=1 fetches `url` directly. page>1 walks forward from page 1
    through successive "next" links, page-1 times - not just following
    a directly-addressable "page N" URL, since STAC doesn't have one.
    This is O(page) requests rather than O(1) (real cost for a deep
    listing), but ITEMS_PAGE_LIMIT=100 combined with
    BIG_LISTING_THRESHOLD's LargeListing guard keeps realistic listings
    to a small number of pages in practice. Returns (entries, title,
    page_info); page_info has no reliable "pagecount" (STAC doesn't
    promise a total-item count up front) - degrades honestly (Prev/Next
    still work, the page-count display just doesn't appear) rather than
    guessing one.

    Only meaningful for a Collection's Items URL - fetching a single
    Item's own URL (page must be 1 there; STAC gives no meaning to
    "page 2 of one Item's assets") returns its Assets regardless of the
    requested page.
    """
    fetch = fetch or http_get
    target = url
    for _ in range(page - 1):
        raw = fetch(target)
        payload = json.loads(raw)
        next_url = _next_link(payload)
        if not next_url:
            break
        target = next_url
    raw = fetch(target)
    payload = json.loads(raw)
    doc_type = payload.get("type")
    if doc_type == "Feature":
        entries = _item_to_entries(payload)
        title = payload.get("id") or ""
        return entries, title, {"next_url": None}
    entries = _collection_page_to_entries(payload)
    return entries, "", {"next_url": _next_link(payload)}


def fetch_all_pages(url, force=False, progress=None, fetch=None):
    """Fetches every page of a Collection's Items listing, following
    "next" links - mirrors atom.fetch_all_pages's shape and
    LargeListing-guard behaviour. Not meaningful (and not called) for a
    single-Item URL, which fetch_page already resolves in one call.

    Prefers an upfront LargeListing warning via the first page's own
    "numberMatched" field (OGC API - Features' optional reported total)
    when the server provides it - matching atom.py's own
    fetch-page-1-then-check-total-before-continuing behaviour. Not
    every STAC API implementation reports numberMatched, though; when
    it's absent this falls back to guarding after the fact (raising
    once BIG_LISTING_THRESHOLD entries have already been fetched)
    rather than promising an upfront warning it can't actually give."""
    fetch = fetch or http_get
    raw = fetch(url)
    payload = json.loads(raw)
    entries = _collection_page_to_entries(payload)
    number_matched = payload.get("numberMatched")
    if (
        isinstance(number_matched, int)
        and number_matched > BIG_LISTING_THRESHOLD
        and not force
    ):
        raise LargeListing(number_matched)
    all_entries = list(entries)
    seen_urls = {url}
    next_url = _next_link(payload)
    page_num = 1
    while next_url and next_url not in seen_urls:
        if len(all_entries) > BIG_LISTING_THRESHOLD and not force:
            raise LargeListing(len(all_entries))
        if progress:
            page_num += 1
            progress(page_num, None)
        seen_urls.add(next_url)
        more, _, info = fetch_page(next_url, fetch=fetch)
        if not more:
            break
        all_entries.extend(more)
        next_url = info.get("next_url")
    return all_entries, None


def resolve_downloadable_files(
    entry: Entry, fetch: Optional[Callable] = None
) -> List[Tuple[Link, Optional[Tuple[str, str]]]]:
    """Resolves a selected entry (an Item, i.e. entry.is_dir) into its
    own Assets as (file_link, checksum) pairs - mirrors
    atom.resolve_downloadable_files's shape and purpose, but checksum
    here is either None or an already-resolved (algorithm, hex_digest)
    tuple (see module docstring), never a Link needing a further
    fetch."""
    if not entry.is_dir:
        if not entry.file_link:
            return []
        return [(entry.file_link, entry.checksum)]
    fetch = fetch or http_get
    item_entries, _, _ = fetch_page(entry.primary_href(), fetch=fetch)
    return [
        (e.file_link, e.checksum) for e in item_entries if not e.is_dir and e.file_link
    ]
