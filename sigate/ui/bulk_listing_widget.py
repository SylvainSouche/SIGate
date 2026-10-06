"""
ui.bulk_listing_widget - content of SIGate's Bulk Listing Data Source
Manager tab: browses an Atom feed, a STAC catalog collection, or a
two-level Theme/Dataset hierarchy built on top of Geonorge's otherwise-
flat search catalog, lets the user select one or more entries, and runs
the full download pipeline (target selection, size and free-space
checks, download, integrity checks, and a format-dependent archive-
extraction or simple-file-placement branch) on the result.

All three gateway types satisfy the same functional contract (see
gateways/stac.py's and gateways/geonorge_catalog.py's own module
docstrings for the detailed mapping onto this tab's atom-originated
directory-tree browsing model - geonorge_catalog.py's is what
particularly explains how a genuinely flat search-and-facets catalog
gets its own real two-level hierarchy, and why that hierarchy
deliberately delegates to gateways.atom the moment it reaches a real
per-dataset Atom feed rather than reimplementing anything) - this widget
dispatches between them via _GATEWAY_MODULES rather than hardcoding one,
and ConnectionManager's combo lists connections of any of these types
together. A source config whose gateway type isn't in _GATEWAY_MODULES
(e.g. a hypothetical future FTP gateway) simply never appears in this
tab's connection list.

Confirmation dialogs here use plain QMessageBox prompts rather than
bespoke custom dialogs, since the goal at this stage is a working,
complete pipeline wired end to end rather than a polished interactive
experience - the latter can be refined once the former is confirmed
correct.
"""

from pathlib import Path
from typing import List, Optional, Union

from qgis.gui import QgsAbstractDataSourceWidget
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from sigate.download import pipeline
from sigate.download.archive import find_7z_executable
from sigate.download.integrity import parse_md5_checksum_file
from sigate.gateways import atom, geonorge_catalog, stac
from sigate.translation import TranslationStore

from . import download_flow
from . import settings as sigate_settings
from .connection_manager import ConnectionManager
from .layer_groups import emit_in_scope
from .target_picker import TargetPicker

# One entry per gateway type this tab knows how to browse - both satisfy
# the exact same functional contract (fetch_page/fetch_all_pages/
# resolve_downloadable_files/LargeListing, Entry.is_dir/.title/
# .primary_href()), so every call site below dispatches through
# self._current_module() rather than hardcoding one gateway module the
# way this tab originally only supported atom. See gateways/stac.py's
# own module docstring for why STAC's Collection/Item/Asset maps onto
# this same tree shape and why a different-shaped source (e.g. a flat
# search-and-facets catalog) would not belong here.
_GATEWAY_MODULES = {"atom": atom, "stac": stac, "geonorge_catalog": geonorge_catalog}

# An entry from either gateway - structurally compatible (.title,
# .is_dir, .file_link, .primary_href()) but not related by inheritance,
# since atom.Entry and stac.Entry are each plain classes with no shared
# base beyond that duck-typed shape.
BulkEntry = Union[atom.Entry, stac.Entry, geonorge_catalog.Entry]


class BulkListingSourceSelectWidget(QgsAbstractDataSourceWidget):
    def __init__(
        self,
        parent=None,
        fl=Qt.WindowType(0),
        widget_mode=None,
        fetch=None,
        download_fn=None,
        progress_runner=None,
    ) -> None:
        # None -> QGIS's own default; see WfsSourceSelectWidget.__init__.
        if widget_mode is None:
            super().__init__(parent, fl)
        else:
            super().__init__(parent, fl, widget_mode)
        self.fetch = fetch
        self.download_fn = download_fn
        self.progress_runner = progress_runner
        self.history: List[str] = []
        self.current_url: Optional[str] = None
        self.current_page: int = 1
        self.current_pagecount: Optional[int] = None
        self.current_entries: List[BulkEntry] = []
        self._all_page_entries: List[BulkEntry] = []
        self.filtered_mode: bool = False
        self.sevenzip_exe = (
            sigate_settings.get_sevenzip_path_override() or find_7z_executable()
        )
        # Loaded once and kept for this widget's lifetime rather than
        # re-read on every render pass - see TranslationStore's own
        # docstring for why. Uses the same locale setting as
        # gateways.wmts_wms's ?lang= request - one preferred-language
        # choice serves both "ask the server directly" (when it
        # supports that) and "substitute from the translation memory"
        # (when it doesn't), rather than two separate settings for what
        # is, from the user's point of view, one preference.
        self._translation_store = TranslationStore(
            sigate_settings.get_sigate_data_dir(), sigate_settings.get_locale()
        )
        self._build_ui()
        self._load_initial_connection()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel(self.tr("Connection:")))
        self.connection_manager = ConnectionManager(
            ["atom", "stac", "geonorge_catalog"],
            data_dir_provider=sigate_settings.get_sigate_data_dir,
            parent=self,
        )
        self.connection_manager.connectionChanged.connect(self._on_connection_changed)
        source_row.addWidget(self.connection_manager)
        layout.addLayout(source_row)

        nav_row = QHBoxLayout()
        self.back_button = QPushButton(self.tr("< Back"), self)
        self.back_button.clicked.connect(self._go_back)
        nav_row.addWidget(self.back_button)
        self.url_edit = QLineEdit(self)
        self.url_edit.returnPressed.connect(
            lambda: self._load(self.url_edit.text().strip())
        )
        nav_row.addWidget(self.url_edit)
        go_button = QPushButton(self.tr("Go"), self)
        go_button.clicked.connect(lambda: self._load(self.url_edit.text().strip()))
        nav_row.addWidget(go_button)
        layout.addLayout(nav_row)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel(self.tr("Filter:")))
        self.filter_edit = QLineEdit(self)
        self.filter_edit.textChanged.connect(self._apply_current_page_filter)
        filter_row.addWidget(self.filter_edit)
        filter_all_pages_button = QPushButton(self.tr("Filter (all pages)"), self)
        filter_all_pages_button.clicked.connect(self._filter_all_pages)
        filter_row.addWidget(filter_all_pages_button)
        clear_filter_button = QPushButton(self.tr("Clear"), self)
        clear_filter_button.clicked.connect(self._clear_filter)
        filter_row.addWidget(clear_filter_button)
        layout.addLayout(filter_row)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels([self.tr("Type"), self.tr("Title"), self.tr("Size")])
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.tree)

        page_row = QHBoxLayout()
        self.prev_page_button = QPushButton(self.tr("< Prev page"), self)
        self.prev_page_button.clicked.connect(self._prev_page)
        page_row.addWidget(self.prev_page_button)
        self.page_label = QLabel(self)
        page_row.addWidget(self.page_label)
        self.next_page_button = QPushButton(self.tr("Next page >"), self)
        self.next_page_button.clicked.connect(self._next_page)
        page_row.addWidget(self.next_page_button)
        layout.addLayout(page_row)

        self.target_picker = TargetPicker(self)
        layout.addWidget(self.target_picker)

        self.download_button = QPushButton(self.tr("Download selected"), self)
        self.download_button.clicked.connect(self._on_download_clicked)
        layout.addWidget(self.download_button)

        self.status_label = QLabel(self)
        layout.addWidget(self.status_label)

    def _load_initial_connection(self) -> None:
        # ConnectionManager's own constructor already fired connectionChanged
        # once internally (from its reload_connections() call), before our
        # slot was connected to it - that first emission was missed, so the
        # already-selected connection is picked up explicitly here instead,
        # now that the rest of the UI (self.tree, etc.) actually exists.
        self._on_connection_changed(self.connection_manager.current_connection())

    def _current_module(self):
        """The gateway module (atom or stac) backing whichever
        connection is currently selected - looked up by the selected
        GatewayConfig's own declared type, not assumed. Defaults to
        atom only when nothing is selected yet (matches this tab's
        original sole gateway type, so an empty-selection state behaves
        the same as before this generalization)."""
        gateway = self.connection_manager.current_gateway()
        gateway_type = gateway.gateway_type if gateway else "atom"
        return _GATEWAY_MODULES.get(gateway_type, atom)

    def _current_entry_point_url(self, gateway) -> str:
        """The actual browse-root URL for a gateway instance - plain
        base_url for atom (its capabilities document already is the
        browse root), but for stac, base_url is the STAC API root and
        the specific collection to browse is named separately in
        extra["collection_id"] (see sources/seed.py) - a STAC catalog
        the size of data.geo.admin.ch's (~570 collections spanning
        every federal office) has no single curated root a user would
        want to browse in full the way IGN's Atom capabilities
        document is, so each Swiss dataset is its own gateway
        instance."""
        if gateway.gateway_type == "stac":
            return stac.items_url_for_collection(
                gateway.base_url, gateway.extra.get("collection_id", "")
            )
        return gateway.base_url

    def _on_connection_changed(self, source) -> None:
        if source is None:
            return
        # The actually-selected gateway instance, not
        # source.gateway("atom") - a source can now contribute both an
        # atom and a stac instance (or, for Switzerland, several stac
        # instances - one per collection), and picking the wrong one
        # would silently browse the wrong dataset.
        gateway = self.connection_manager.current_gateway()
        if gateway is None:
            return
        self._load(self._current_entry_point_url(gateway), push_history=False)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _log_exception(self, context: str, exception: Exception) -> None:
        """Logs full diagnostic detail (the real exception, not just its
        string form) to QGIS's Log Messages panel under the "SIGate" tag
        - the user-facing status label alone only ever shows str(e),
        which can be too terse to actually diagnose a real failure."""
        from qgis.core import Qgis, QgsMessageLog

        QgsMessageLog.logMessage(
            f"{context}: {exception!r}", "SIGate", Qgis.MessageLevel.Warning
        )

    # --------------------------------------------------------------- navigation

    def _render_entries(self, entries: List[BulkEntry]) -> None:
        """Renders `entries` into the tree. Used for a fresh page load, a
        live current-page filter, and an all-pages filter result alike -
        current_entries always reflects whatever is actually displayed,
        since selection and double-click both index into it."""
        self.current_entries = entries
        self.tree.clear()
        for entry in entries:
            kind = self.tr("DIR") if entry.is_dir else self.tr("FILE")
            size = ""
            if not entry.is_dir and entry.file_link and entry.file_link.length:
                size = str(entry.file_link.length)
            self.tree.addTopLevelItem(QTreeWidgetItem([kind, entry.title, size]))

    def _load(self, url: str, push_history: bool = True, page: int = 1) -> None:
        if not url:
            return
        try:
            entries, feed_title, page_info = self._current_module().fetch_page(
                url, page=page, fetch=self.fetch
            )
        except Exception as e:
            self._set_status(self.tr("Failed to load {}: {}").format(url, e))
            self._log_exception(f"Failed to load listing page {url!r}", e)
            return

        if push_history and self.current_url and self.current_url != url:
            self.history.append(self.current_url)
        self.current_url = url
        self.current_page = page
        self.current_pagecount = page_info.get("pagecount")
        self._all_page_entries = entries
        self.filtered_mode = False
        self.filter_edit.setText("")
        self.url_edit.setText(url)
        self._render_entries(entries)

        total = page_info.get("totalentries")
        page_label_text = self.tr("page {}").format(page)
        if self.current_pagecount:
            page_label_text += self.tr(" / {}").format(self.current_pagecount)
        if total is not None:
            page_label_text += self.tr("  ({} entries total)").format(total)
        self.page_label.setText(page_label_text)
        self.prev_page_button.setEnabled(page > 1)
        self.next_page_button.setEnabled(
            self.current_pagecount is None or page < self.current_pagecount
        )

        self._set_status(
            self.tr("{} - {} entrie(s) on this page").format(
                feed_title or url, len(entries)
            )
        )

    def _prev_page(self) -> None:
        if self.current_page > 1:
            self._load(self.current_url, push_history=False, page=self.current_page - 1)

    def _next_page(self) -> None:
        self._load(self.current_url, push_history=False, page=self.current_page + 1)

    def _go_back(self) -> None:
        if self.history:
            self._load(self.history.pop(), push_history=False)

    def _apply_current_page_filter(self, _text=None) -> None:
        """Live, instant filter over whatever page is currently loaded -
        no extra fetch. Does nothing while an all-pages filter is active,
        since that already represents a filtered view spanning more than
        just the current page."""
        if self.filtered_mode:
            return
        needle = self.filter_edit.text().strip().lower()
        if not needle:
            self._render_entries(self._all_page_entries)
            return
        matched = [e for e in self._all_page_entries if needle in e.title.lower()]
        self._render_entries(matched)

    def _filter_all_pages(self) -> None:
        """Fetches every page of the current listing first, then filters
        the full result - for finding one specific entry that might be
        sitting on a page far beyond whatever is currently displayed,
        rather than only ever searching the current page's 50 entries."""
        needle = self.filter_edit.text().strip()
        if not needle:
            QMessageBox.information(
                self,
                self.tr("No filter text"),
                self.tr("Type something in the filter field first."),
            )
            return
        if not self.current_url:
            return

        module = self._current_module()
        try:
            entries, _ = module.fetch_all_pages(self.current_url, fetch=self.fetch)
        except module.LargeListing as e:
            proceed = QMessageBox.question(
                self,
                self.tr("Large listing"),
                self.tr(
                    "This listing reports {} entries total across many pages. "
                    "Filtering means fetching every page first. Continue?"
                ).format(e.total),
            )
            if proceed != QMessageBox.StandardButton.Yes:
                return
            try:
                entries, _ = module.fetch_all_pages(
                    self.current_url, fetch=self.fetch, force=True
                )
            except Exception as inner_e:
                self._set_status(
                    self.tr("Failed to fetch all pages: {}").format(inner_e)
                )
                self._log_exception(
                    f"Failed to fetch all pages (forced) for {self.current_url!r}",
                    inner_e,
                )
                return
        except Exception as e:
            self._set_status(self.tr("Failed to fetch all pages: {}").format(e))
            self._log_exception(
                f"Failed to fetch all pages for {self.current_url!r}", e
            )
            return

        lowered = needle.lower()
        matched = [e for e in entries if lowered in e.title.lower()]
        self.filtered_mode = True
        self._render_entries(matched)
        self.prev_page_button.setEnabled(False)
        self.next_page_button.setEnabled(False)
        self.page_label.setText(
            self.tr('filtered: {} / {} match "{}" (all pages)').format(
                len(matched), len(entries), needle
            )
        )
        self._set_status(self.tr("Filter applied across all pages."))

    def _clear_filter(self) -> None:
        self.filter_edit.setText("")
        if self.filtered_mode:
            self.filtered_mode = False
            self._load(self.current_url, push_history=False, page=1)
        else:
            self._render_entries(self._all_page_entries)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        """Descends into item if it's a directory-like entry. Guarded
        against an out-of-range index the same way _selected_entries
        is (see its own docstring for the general reasoning: a stale
        index from a re-render, or item being None entirely - e.g. a
        double-click firing against an unexpectedly empty tree - must
        not crash with a raw IndexError; there's simply nothing to
        descend into)."""
        index = self.tree.indexOfTopLevelItem(item)
        if not (0 <= index < len(self.current_entries)):
            return
        entry = self.current_entries[index]
        if entry.is_dir:
            self._load(entry.primary_href())

    # --------------------------------------------------------------- download

    def _selected_entries(self) -> List[BulkEntry]:
        """Entries currently selected in the tree, mapped back to
        self.current_entries by index. A selection surviving a
        re-render (e.g. a filter applied between selecting rows and
        calling this) could reference a now out-of-range index -
        guarded against here rather than letting IndexError propagate,
        since self.current_entries is reassigned wholesale on every
        render (_render_entries), not updated in place."""
        indices = [
            self.tree.indexOfTopLevelItem(item) for item in self.tree.selectedItems()
        ]
        return [
            self.current_entries[i]
            for i in indices
            if 0 <= i < len(self.current_entries)
        ]

    def _resolve_expected_hash(self, checksum_ref):
        """Resolves whatever resolve_downloadable_files handed back as
        the checksum half of its (file_link, checksum_ref) pair into a
        uniform (algorithm, hex_digest) tuple, or None. Two real shapes
        exist here, one per gateway type - dispatched on the Python
        type of checksum_ref itself rather than needing the caller to
        know which gateway produced it:

        - atom: a Link-like object (an external ".md5" sidecar link
          still needing a fetch + parse - see atom.find_checksum_link's
          own docstring for why gateways/ doesn't do that parsing
          itself).
        - stac: already a plain (algorithm, hex_digest) tuple - the
          checksum was already inline on the asset, no separate fetch
          needed (see gateways/stac.py's own module docstring).

        Returns None for any failure (network error, unexpected
        content) rather than blocking the download over a failed
        checksum lookup - a download proceeding without integrity
        verification is far preferable to a download refused entirely
        because its optional checksum happened to be unreachable or
        unparseable."""
        if checksum_ref is None:
            return None
        if isinstance(checksum_ref, tuple):
            return checksum_ref
        try:
            content = self.fetch(checksum_ref.href)
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")
            digest = parse_md5_checksum_file(content)
            return ("md5", digest) if digest else None
        except Exception as e:
            self._log_exception(
                f"Could not fetch/parse checksum file {checksum_ref.href!r}", e
            )
            return None

    def _on_download_clicked(self) -> None:
        selected = self._selected_entries()
        if not selected:
            QMessageBox.information(
                self,
                self.tr("Nothing selected"),
                self.tr("Select one or more entries first."),
            )
            return

        items = []
        source_display_name = self.connection_manager.current_connection().display_name
        module = self._current_module()
        for entry in selected:
            try:
                resolved = module.resolve_downloadable_files(entry, fetch=self.fetch)
            except Exception as e:
                self._set_status(
                    self.tr("Failed to resolve {}: {}").format(entry.title, e)
                )
                self._log_exception(
                    f"Failed to resolve downloadable files for entry {entry.title!r}",
                    e,
                )
                return
            for link, checksum_ref in resolved:
                filename = link.href.rsplit("/", 1)[-1]
                declared_size = (
                    int(link.length)
                    if link.length and str(link.length).isdigit()
                    else None
                )
                expected_hash = self._resolve_expected_hash(checksum_ref)
                # entry.title is this gateway's closest analog to a WFS
                # typename/WMTS layer identifier - there's no separate
                # "layer" concept for an Atom feed entry, but the entry
                # itself is the specific resource this download came
                # from, which is exactly what the layer segment of the
                # directory structure is meant to capture.
                subdirectory = pipeline.build_download_subdirectory(
                    source_display_name, filename, layer_name=entry.title
                )
                items.append(
                    pipeline.DownloadItem(
                        url=link.href,
                        filename=filename,
                        declared_size=declared_size,
                        subdirectory=subdirectory,
                        expected_hash=expected_hash,
                    )
                )

        if not items:
            QMessageBox.information(
                self,
                self.tr("Nothing to download"),
                self.tr("No downloadable files were found for the selected entries."),
            )
            return

        download_flow.run_download_and_add_layers(
            self,
            items,
            self.target_picker,
            self.sevenzip_exe,
            set_status=self._set_status,
            add_layer=self._add_layer,
            download_fn=self.download_fn,
            progress_runner=self.progress_runner,
            group_base=[source_display_name],
        )

    def _add_layer(self, path: Path) -> None:
        kind = download_flow.guess_layer_kind(path)
        if kind == "raster":
            emit_in_scope(
                lambda: self.addRasterLayer.emit(str(path), path.stem, "gdal")
            )
        elif kind == "vector":
            emit_in_scope(lambda: self.addVectorLayer.emit(str(path), path.stem, "ogr"))
