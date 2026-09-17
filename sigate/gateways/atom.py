"""
gateways.atom - INSPIRE-Atom download-service gateway.

The service exposes a tree of Atom feeds:
    capabilities  ->  resource/{PRODUCT}  ->  resource/{PRODUCT}/{EDITION}  ->  download/...

Each <entry> in a feed either points to another feed (a "directory": its
<link> has type="application/atom+xml") or to a real file (any other
mime type, e.g. application/x-7z-compressed).

Pagination: the API takes ?page= (starting at 1) and a page-size
parameter. The <feed> root element reports the actual state of a
paginated listing as XML attributes (page/pagesize/pagecount/totalentries,
namespace-agnostic); this module reads those back rather than assuming
its requested page size was honoured, since some servers do not honour
it.
"""

import os
import ssl
import time
import xml.etree.ElementTree as ET
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .atom_constants import (
    ACCEPT_HEADER,
    ATOM_NAMESPACE as ATOM,
    PAGE_INFO_ATTRS,
    PARAM_LIMIT,
    PARAM_PAGE,
    PARAM_PAGE_SIZE,
    USER_AGENT,
)
from .base import DownloadCancelled, GatewayCapabilities, GatewayError

try:
    import certifi

    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CONTEXT = ssl.create_default_context()

PAGE_LIMIT = 50  # documented maximum page size
BIG_LISTING_THRESHOLD = (
    2000  # ask before auto-fetching/recursing past this many entries
)
THROTTLE_SECONDS = 0.05  # be polite between successive page requests

CAPABILITIES = GatewayCapabilities(
    supports_query=False,
    extent_flavor="categorical",
    supports_locale=False,
    is_file_index=True,
)


# --------------------------------------------------------------------------- parsing


class Link:
    __slots__ = ("href", "rel", "type", "length", "title")

    def __init__(self, el):
        self.href = el.get("href")
        self.rel = el.get("rel") or "alternate"
        self.type = el.get("type") or ""
        self.length = el.get("length")
        self.title = el.get("title")


class Entry:
    def __init__(self, el):
        self.title = _text(el.find(ATOM + "title")) or "(untitled)"
        self.id = _text(el.find(ATOM + "id")) or ""
        self.updated = _text(el.find(ATOM + "updated")) or ""
        self.summary = _text(el.find(ATOM + "summary")) or ""
        self.links = [Link(child_link) for child_link in el.findall(ATOM + "link")]
        self.extra = {}
        for child in el:
            if child.tag.startswith(ATOM):
                continue
            local = child.tag.split("}")[-1]
            val = dict(child.attrib) if child.attrib else (child.text or "").strip()
            self.extra.setdefault(local, []).append(val)

    @property
    def feed_link(self):
        for link in self.links:
            if "atom+xml" in link.type or link.rel == "section":
                return link
        return None

    @property
    def file_link(self):
        candidates = [link for link in self.links if "atom+xml" not in link.type]
        for link in candidates:
            if link.rel == "alternate":
                return link
        return candidates[0] if candidates else None

    @property
    def is_dir(self):
        return self.feed_link is not None

    def primary_href(self):
        link = self.feed_link if self.is_dir else self.file_link
        return link.href if link else self.id


def _text(el):
    return el.text.strip() if el is not None and el.text else None


def _local(tag):
    return tag.split("}")[-1]


# --------------------------------------------------------------------------- http / pagination


class LargeListing(GatewayError):
    def __init__(self, total):
        self.total = total
        super().__init__(f"listing has {total} entries")


def _extract_ows_exception_text(body: str) -> str:
    """Best-effort extraction of the human-readable message from a real
    OGC OWS ExceptionReport body (the standard shape a WFS/WMS/WMTS
    server's own error response takes) - namespace-agnostic, matching
    this project's own established convention for parsing real server
    XML, since different servers bind the ows: prefix differently or
    omit a namespace declaration entirely. Falls back to the raw body
    text (truncated, since a full HTML error page can be very long and
    mostly irrelevant) if it isn't parseable as XML or doesn't have
    this shape at all - some servers return a plain-text or HTML error
    instead of a real OWS ExceptionReport."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return body.strip()[:500]
    texts = [
        (el.text or "").strip()
        for el in root.iter()
        if el.tag.rsplit("}", 1)[-1] == "ExceptionText" and el.text
    ]
    if texts:
        return " / ".join(t for t in texts if t)
    return body.strip()[:500]


def http_get(url, timeout=30, extra_headers=None):
    """Default fetch implementation, used when no `fetch` is injected.
    extra_headers, when given, is merged into the request's own headers -
    used for gateway instances gated behind a header-based API key, where
    SIGate itself needs to make the request directly (rather than handing
    off to QGIS's own provider, which has its own separate authentication
    mechanism - see ui/authcfg.py).

    A 4xx/5xx response raises HTTPError - genuinely diagnosed here now,
    not just passed through as the generic "HTTP Error 400: Bad
    Request" status line: HTTPError's own response body is still
    readable at this point (it hasn't been closed), and for a real
    WFS/WMS/WMTS server, that body is typically a specific OWS
    ExceptionReport explaining exactly what was wrong with the request
    (an unrecognized field name, a rejected filter expression, a
    missing parameter, ...) - a real, confirmed gap this closes: a WFS
    spatial filter reported failing with only the generic message gave
    no way to actually diagnose why, forcing guesswork rather than a
    real answer, for something the server had already explained in its
    own response the whole time."""
    headers = {"User-Agent": USER_AGENT, "Accept": ACCEPT_HEADER}
    if extra_headers:
        headers.update(extra_headers)
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout, context=_SSL_CONTEXT) as r:
            return r.read()
    except HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        detail = _extract_ows_exception_text(body) if body else "(no response body)"
        raise GatewayError(f"HTTP {e.code} {e.reason}: {detail}") from e
    except URLError as e:
        if isinstance(e.reason, ssl.SSLCertVerificationError):
            raise GatewayError(
                "SSL certificate verification failed. This is usually a local "
                "certificate-store issue rather than a problem with the "
                "server. Try upgrading the certifi package."
            ) from e
        raise


def fetch_with_header(header_name, header_value, timeout=30):
    """Builds a fetch function (url -> bytes) using this module's own
    default transport, with one extra HTTP header attached to every
    request. For a gateway instance gated behind a header-based API key
    where SIGate itself makes the discovery/listing request directly
    (e.g. fetching a private WMTS endpoint's own GetCapabilities to list
    its layers) - a separate concern from the final tile-serving
    connection QGIS's own provider makes, which goes through QGIS's own
    authcfg mechanism instead (ui/authcfg.py) since SIGate has no control
    over that request."""

    def fetch(url):
        return http_get(url, timeout=timeout, extra_headers={header_name: header_value})

    return fetch


def _get_page_info(root):
    """Reads the page/pagesize/pagecount/totalentries attributes off the
    <feed> root element, matched namespace-agnostically."""
    info = {}
    for k, v in root.attrib.items():
        local = _local(k)
        if local in PAGE_INFO_ATTRS:
            try:
                info[local] = int(v)
            except ValueError:
                pass
    return info


def build_url(base_url, page=1, limit=PAGE_LIMIT, extra_params=None):
    parts = urlsplit(base_url)
    qs = parse_qs(parts.query)
    if extra_params:
        for k, v in extra_params.items():
            qs[k] = [str(v)]
    qs[PARAM_PAGE] = [str(page)]
    qs[PARAM_LIMIT] = [str(limit)]
    qs[PARAM_PAGE_SIZE] = [str(limit)]
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(qs, doseq=True), "")
    )


def fetch_page(base_url, page=1, limit=PAGE_LIMIT, extra_params=None, fetch=None):
    """Fetches a single page. Returns (entries, feed_title, page_info dict)."""
    fetch = fetch or http_get
    data = fetch(build_url(base_url, page, limit, extra_params))
    root = ET.fromstring(data)
    entries = [Entry(e) for e in root.findall(ATOM + "entry")]
    feed_title = _text(root.find(ATOM + "title")) or ""
    info = _get_page_info(root)
    return entries, feed_title, info


def fetch_all_pages(
    base_url, extra_params=None, force=False, progress=None, fetch=None
):
    """Fetches every page of a listing. Raises LargeListing if the total
    is large and force is False, so callers can warn before committing to
    a full walk."""
    fetch = fetch or http_get
    entries, _, info = fetch_page(base_url, 1, PAGE_LIMIT, extra_params, fetch=fetch)
    total = info.get("totalentries")
    pagecount = info.get("pagecount")
    if total is not None and total > BIG_LISTING_THRESHOLD and not force:
        raise LargeListing(total)
    all_entries = list(entries)
    page = 2
    if pagecount:
        while page <= pagecount:
            if progress:
                progress(page, pagecount)
            time.sleep(THROTTLE_SECONDS)
            more, _, _ = fetch_page(
                base_url, page, PAGE_LIMIT, extra_params, fetch=fetch
            )
            if not more:
                break
            all_entries.extend(more)
            page += 1
    else:
        # No page-info attributes present: keep going until a page comes
        # back empty, as a fallback.
        while True:
            if progress:
                progress(page, None)
            time.sleep(THROTTLE_SECONDS)
            more, _, _ = fetch_page(
                base_url, page, PAGE_LIMIT, extra_params, fetch=fetch
            )
            if not more:
                break
            all_entries.extend(more)
            page += 1
            if page > 200000:  # sanity cap
                break
    return all_entries, total


def human_size(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "-"
    for unit in ["B", "K", "M", "G", "T"]:
        if n < 1024:
            return f"{int(n)}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}P"


def find_checksum_link(entry, file_link):
    """Looks for a sibling link within the same entry that looks like an
    MD5 checksum companion for file_link - a link whose href ends in
    .md5 and whose own filename corresponds to file_link's (either
    "<file_link's full name>.md5", or "<file_link's stem>.md5"). Returns
    None if none is found.

    This is a heuristic based on a common real-world convention (a
    companion .md5 sidecar link alongside the real download link within
    the same Atom entry) - not a confirmed universal Atom/INSPIRE
    convention, and not every source that provides a checksum
    necessarily represents it this way. Only identifies a *candidate*
    link; actually fetching and parsing its content (to get the real
    expected hash) is the caller's responsibility -
    download.integrity.parse_md5_checksum_file, kept out of this module
    since gateways/ has no dependency on download/."""
    if not file_link or not file_link.href:
        return None
    target_name = file_link.href.rsplit("/", 1)[-1]
    target_stem = target_name.rsplit(".", 1)[0] if "." in target_name else target_name
    for link in entry.links:
        if not link.href or not link.href.lower().endswith(".md5"):
            continue
        link_name = link.href.rsplit("/", 1)[-1]
        link_stem = link_name[: -len(".md5")]
        if link_stem == target_name or link_stem == target_stem:
            return link
    return None


def resolve_downloadable_files(entry, fetch=None):
    """Resolves a selected entry into the actual file(s) to download,
    each paired with a candidate checksum link if one was found
    alongside it (find_checksum_link) - a (file_link, checksum_link)
    tuple per file, checksum_link being None when no candidate was
    found. If the entry is itself a file, returns a single-item list.
    If it is a directory, fetches its own listing and returns every file
    found there - a product can resolve to more than one physical file
    (a directory containing multiple parts of a split archive), and this
    must return all of them, not just the first. A checksum sibling can
    only be looked up correctly per the entry it actually belongs to,
    which is why this is resolved here rather than left to the caller,
    who would otherwise lose track of which entry a directory-resolved
    file link came from."""
    if not entry.is_dir:
        if not entry.file_link:
            return []
        return [(entry.file_link, find_checksum_link(entry, entry.file_link))]
    fetch = fetch or http_get
    sub_entries, _ = fetch_all_pages(entry.primary_href(), force=True, fetch=fetch)
    return [
        (e.file_link, find_checksum_link(e, e.file_link))
        for e in sub_entries
        if not e.is_dir and e.file_link
    ]


def download(
    url,
    dest_dir=".",
    filename=None,
    quiet=True,
    progress_callback=None,
    should_continue=None,
):
    """Streams url to dest_dir/filename. Uses urllib directly rather than
    an injected `fetch`, since a single url-in/bytes-out callable does not
    fit a streamed, progress-reporting download; a higher-level download
    pipeline is expected to supply its own transport for this rather than
    call this function directly in the final application.

    progress_callback(read, total), when given, is called after every
    chunk - total is None if the server didn't send a Content-Length
    header, so a caller driving a progress bar has to handle an unknown
    total explicitly (e.g. an indeterminate/marquee bar) rather than
    assume one is always available.

    should_continue(), when given, is checked before every chunk read -
    returning False stops the transfer, deletes the partial file (a
    half-downloaded file left on disk would otherwise look like a
    genuine, complete one to anything checking for its existence later,
    e.g. download.pipeline's already-downloaded check), and raises
    DownloadCancelled rather than returning a partial path silently,
    so a caller can distinguish "cancelled" from "actually succeeded."
    """
    if not filename:
        filename = os.path.basename(urlsplit(url).path) or "download.bin"
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, filename)
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, context=_SSL_CONTEXT) as r, open(dest, "wb") as f:
        total = r.headers.get("Content-Length")
        total = int(total) if total else None
        read = 0
        while True:
            if should_continue is not None and not should_continue():
                f.close()
                try:
                    os.remove(dest)
                except OSError:
                    pass
                raise DownloadCancelled(f"Download of {filename!r} was cancelled")
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            read += len(chunk)
            if progress_callback is not None:
                progress_callback(read, total)
            if not quiet:
                if total:
                    print(
                        f"\r  {filename}: {read / 1e6:8.2f}/{total / 1e6:.2f} MB ({read * 100 // total}%)",
                        end="",
                        flush=True,
                    )
                else:
                    print(f"\r  {filename}: {read / 1e6:8.2f} MB", end="", flush=True)
    if not quiet:
        print()
    return dest
