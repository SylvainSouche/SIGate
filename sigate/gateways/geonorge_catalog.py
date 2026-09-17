"""
gateways.geonorge_catalog - a thin two-level directory view over Norway's
kartkatalog.geonorge.no/api/search catalog (flat search-and-facets, ~8,637
records total, confirmed live this session), built specifically so it can
plug into the Bulk Listing tab's tree-browsing model without needing a
new download/checksum pipeline of its own.

This module owns exactly two levels, both derived from a single fetch of
the *entire* catalog as CSV (`mediatype=csv&limit=10000`), rather than
one paginated search per theme:

    Level 1 (root):  every value of the CSV's "Tema" column (Geonorge's
                      own name for what this project has been calling
                      "Theme") that has at least one row carrying a
                      real Atom-feed URL - a "directory".
    Level 2 (theme):  the rows for that one theme which have a real
                      Atom-feed URL (rows without one are excluded
                      entirely, not shown as dead ends) - each also a
                      "directory" whose primary_href() is that row's
                      own real, already-existing INSPIRE Atom feed URL.

Real bug found and fixed after this module first shipped: the CSV's
column headers are Norwegian ("Tittel", "Tema", ...), not the English
names ("Title", "Topic") this module originally assumed - confirmed by
directly fetching a real CSV response from the same platform
(kartkatalog.test.geonorge.no, same header shape as production). Every
`row.get("Topic")` therefore silently matched nothing at all, so every
theme grouping came back empty - a real "the CSV loads fine but the tree
is empty" bug reported directly from actual use, not a fetch failure.
"Atom-feed" itself is not translated (identical in both the originally-
assumed English header and the confirmed real Norwegian one), so that
one column name needed no change.

Beyond that, this module does no work of its own at all: the moment a
URL isn't this module's own CATALOG_URL (or a `#theme=`-suffixed view of
it), every function here (fetch_page, fetch_all_pages,
resolve_downloadable_files) delegates straight to gateways.atom, since a
Geonorge dataset's Atom feed is a real, standard INSPIRE Atom feed -
structurally the same shape IGN's feeds already are, just simpler in
practice (Norwegian feeds tend to be one format's worth of files, not
IGN's own deep resource/product/edition tree). This is why this module
doesn't reimplement checksum handling or file resolution: gateways.atom
already does all of that correctly, and reusing it directly avoids a
second, parallel implementation of the exact same logic. LargeListing
below is a re-export of atom.LargeListing, not a new class, for the same
reason - a caller catching this module's LargeListing must still catch
it correctly even when the guard actually fired several levels down
inside a delegated atom.fetch_all_pages call.

The `#theme=X` URL shape is a fragment on this module's own CATALOG_URL,
not a second real HTTP resource - fetch_page always fetches the bare
CATALOG_URL underneath regardless of which view is requested (the whole
catalog is small enough, ~8,637 rows, that fetching it once per view is
simpler than caching), and filters client-side by the theme named in the
fragment.
"""

import csv
import io
from typing import Callable, List, Optional, Tuple
from urllib.parse import quote, unquote

from . import atom
from .base import GatewayCapabilities, GatewayError  # noqa: F401 (GatewayError kept for symmetry with sibling gateway modules)

CATALOG_URL = (
    "https://kartkatalog.geonorge.no/api/search?limit=10000&text=&mediatype=csv"
)

CAPABILITIES = GatewayCapabilities(
    supports_query=False,
    extent_flavor=None,
    supports_locale=False,
    is_file_index=True,
)

# Re-exported, not a new distinct exception class - see module docstring.
LargeListing = atom.LargeListing

_THEME_MARKER = "#theme="


class Entry:
    """A Theme or a dataset-with-an-Atom-feed - always a "directory" at
    this module's own two levels (file_link/checksum stay None; actual
    files only ever come from the delegated gateways.atom.Entry objects
    once a real feed URL has been reached)."""

    def __init__(self, title, is_dir, href, file_link=None, checksum=None):
        self.title = title
        self.is_dir = is_dir
        self._href = href
        self.file_link = file_link
        self.checksum = checksum

    def primary_href(self):
        return self._href


def _theme_url(theme: str) -> str:
    return f"{CATALOG_URL}{_THEME_MARKER}{quote(theme)}"


def _parse_theme_from_url(url: str) -> Optional[str]:
    idx = url.find(_THEME_MARKER)
    if idx == -1:
        return None
    return unquote(url[idx + len(_THEME_MARKER) :])


def _is_catalog_url(url: str) -> bool:
    return url == CATALOG_URL or url.startswith(CATALOG_URL + _THEME_MARKER)


def _fetch_catalog_rows(fetch: Callable) -> List[dict]:
    raw = fetch(CATALOG_URL)
    if isinstance(raw, bytes):
        # utf-8-sig rather than plain utf-8: a leading BOM is common in
        # CSV exports meant for Excel compatibility (this one has
        # non-ASCII column names - Åpne data, Dekningsområde - a real
        # signal a BOM is likely present), and an unstripped BOM would
        # silently prefix the first column's key ("Tittel" becoming
        # "\ufeffTittel"), leaving every entry's title stuck on the
        # "(untitled)" fallback without ever raising an error.
        raw = raw.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(raw), delimiter=";"))


def fetch_page(url, page=1, fetch=None):
    """Matches atom.fetch_page's call shape (page=N) and return shape
    (entries, title, page_info) at this module's own two levels, so the
    widget's existing page-label logic works unmodified - though in
    practice a real page 2 never exists here (see module docstring on
    why the whole catalog is fetched in one call). Delegates wholesale
    to atom.fetch_page the moment the URL isn't this module's own."""
    fetch = fetch or atom.http_get
    if not _is_catalog_url(url):
        return atom.fetch_page(url, page=page, fetch=fetch)

    rows = _fetch_catalog_rows(fetch)
    theme = _parse_theme_from_url(url)

    if theme is None:
        themes_with_feed = sorted(
            {
                row.get("Tema")
                for row in rows
                if (row.get("Atom-feed") or "").strip() and row.get("Tema")
            }
        )
        entries = [
            Entry(title=t, is_dir=True, href=_theme_url(t)) for t in themes_with_feed
        ]
        return (
            entries,
            "Geonorge - themes",
            {"pagecount": 1, "totalentries": len(entries)},
        )

    entries = [
        Entry(
            title=row.get("Tittel") or "(untitled)", is_dir=True, href=row["Atom-feed"]
        )
        for row in rows
        if row.get("Tema") == theme and (row.get("Atom-feed") or "").strip()
    ]
    return entries, theme, {"pagecount": 1, "totalentries": len(entries)}


def fetch_all_pages(url, force=False, progress=None, fetch=None):
    """At this module's own two levels, fetch_page already returns
    everything in one call (see module docstring) - no real pagination
    to walk, so this is just fetch_page(page=1). Delegates wholesale to
    atom.fetch_all_pages otherwise."""
    fetch = fetch or atom.http_get
    if not _is_catalog_url(url):
        return atom.fetch_all_pages(url, force=force, progress=progress, fetch=fetch)
    entries, _, _ = fetch_page(url, page=1, fetch=fetch)
    return entries, None


def resolve_downloadable_files(
    entry, fetch: Optional[Callable] = None
) -> List[Tuple["atom.Link", object]]:
    """Resolves a selected entry into its real downloadable files.

    Two genuinely different kinds of entry can reach this function, and
    conflating them was a real reported bug (AttributeError: 'Entry'
    object has no attribute 'checksum'): once browsing has already
    descended past a dataset's own Atom feed (fetch_page delegates to
    atom.fetch_page the moment a URL isn't a kartkatalog one - see that
    function and the module docstring), every entry the tree shows from
    that point on is a real gateways.atom.Entry, not this module's own
    Entry class - atom.Entry has no .checksum attribute at all (atom's
    checksum model is a separate sidecar Link, paired up by
    atom.resolve_downloadable_files itself, not a field on Entry). The
    connection's gateway type stays "geonorge_catalog" for the whole
    session regardless of how deep browsing has gone, so this function
    - not the widget - is what has to tell the two kinds apart.

    A selected Theme or dataset entry (this module's own Entry class,
    always is_dir=True) resolves by handing off entirely to
    gateways.atom once its Atom feed URL is known - this module has no
    file-resolution or checksum logic of its own at all otherwise (see
    module docstring). A Theme entry specifically has nothing to
    resolve to - themes aren't downloadable, only descendable - and
    returns an empty list rather than trying to XML-parse a kartkatalog
    URL as if it were an Atom feed."""
    fetch = fetch or atom.http_get
    if not isinstance(entry, Entry):
        # A real atom.Entry reached via delegated browsing - resolve it
        # exactly the way the WM(T)S/Bulk Atom connection itself would,
        # checksum included. None of this module's own Entry-specific
        # fields apply here.
        return atom.resolve_downloadable_files(entry, fetch=fetch)
    if not entry.is_dir:
        if not entry.file_link:
            return []
        return [(entry.file_link, entry.checksum)]
    href = entry.primary_href()
    if _is_catalog_url(href):
        return []
    atom_entries, _ = atom.fetch_all_pages(href, force=True, fetch=fetch)
    results = []
    for e in atom_entries:
        results.extend(atom.resolve_downloadable_files(e, fetch=fetch))
    return results
