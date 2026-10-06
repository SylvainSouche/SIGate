"""
ui.wfs_widget - content of SIGate's WFS Data Source Manager tab.

Covers both of WFS's two legitimate use patterns:

  - Vector-layer use (the common case: boundaries, hydrography, etc.) -
    "Add to map" hands off directly to QGIS's own native "WFS" data
    provider, with no feature fetching or rendering logic of SIGate's
    own.

  - File-index use (confirmed real for IGN's LiDAR HD ":dalle" layer,
    where each feature's geometry is just a tile footprint and the real
    payload is a "url" attribute pointing to an actual downloadable
    file) - "Query features" actually fetches feature data via
    gateways.wfs and shows it in a table with columns built dynamically
    from whatever the layer returns. A live filter narrows the current
    page instantly; "Search ALL pages" walks the whole layer first
    (hits-driven, via gateways.wfs.wfs_fetch_all_pages, avoiding the
    page-size-truncation risk that pagination naively assuming "short
    page = last page" would have) for finding one specific feature that
    might not be on whatever page is currently displayed. If any result
    row carries a url-like field, "Download selected"/"Download ALL
    pages" become available and run the exact same download pipeline the
    Bulk Listing tab uses (shared via ui.download_flow), rather than a
    separate implementation.

The "Add to map" connection string is built with QgsDataSourceUri rather
than by hand-formatting a string, since that class handles escaping
special characters (confirmed necessary: a filter value containing an
embedded quote must be escaped correctly, which QgsDataSourceUri does
and a naive string join would not). This is why that URI-building logic
lives here in the Qt-dependent ui layer rather than in the pure-Python
gateways.wfs module - unlike the WMS/WMTS case, this connection-string
format is not a plain URL query string that could be built without a Qt
dependency.

The filter expression used by both "Add to map" and "Query features" is
built with a shared ui.expression_builder.ExpressionBuilderWidget
(fields list, sample/all value browsing, a real SQL-syntax editor with
autocompletion), presented as its own separate panel via
ui.query_builder_dialog.QueryBuilderDialog - opened by the "Filter..."
button, rather than permanently embedded inline in this tab's main
layout. The intended flow: browse a feature type's features in the
results grid, hit Filter to open the query builder in its own window,
construct the filter there, and have the grid re-query to show only
matching features once accepted. The dialog is built fresh each time
it's opened rather than kept as one persistent, reparented-in-and-out
instance (see that module's own docstring for why); this tab keeps only
the raw expression text (self._active_filter_text) and the
currently-known field schema between openings, not a live widget
reference. The raw SQLite-syntax text is what's kept and reused across
"Add to map"/"Query features". For "Add to map", this SQLite-style
syntax is a deliberately good fit: QGIS's own native WFS "Build Query"
tool historically parsed exactly this kind of SQL-like dialect (via
QgsSQLComposerDialog, confirmed not exposed to Python, hence building
our own equivalent) before translating it internally to the real OGC
filter sent to the server. For "Query features"'s direct CQL_FILTER-based
HTTP requests, whether an arbitrary expression written in this
SQLite-oriented syntax is also valid CQL is not generally guaranteed -
simple field/value comparisons tend to work for both, but this has not
been verified to hold for every expression shape (a documented, not yet
resolved, open question).

The expression builder's field/value population needs a real
QgsVectorLayer (confirmed: QgsExpressionBuilderWidget's own
loadFieldsAndValues() has been a documented no-op since QGIS 3.14) -
since WFS query rows are plain dicts, an in-memory sample layer is built
from whatever page was last queried, freshly each time the Filter dialog
is opened (see gateways.wfs's own function for the underlying fetch).
"""

from pathlib import Path
from typing import Dict, List, Optional, Sequence
from urllib.parse import parse_qs, unquote, urlparse

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDataSourceUri,
    QgsGeometry,
    QgsProject,
    QgsRectangle,
)
from qgis.gui import QgsAbstractDataSourceWidget
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)
from qgis.utils import iface

from sigate.download import pipeline
from sigate.download.archive import find_7z_executable
from sigate.gateways.wfs import (
    wfs_describe_geometry_field,
    wfs_fetch_all_pages,
    wfs_get_capabilities,
    wfs_get_features,
    wfs_get_hits,
)

from . import download_flow
from . import settings as sigate_settings
from .connection_manager import ConnectionManager
from .expression_builder import (
    MAP_EXTENT_TOKEN,
    substitute_tokens,
    substitute_tokens_for_qgis,
)
from .layer_groups import emit_in_scope, group_scope
from .product_choice_dialog import ProductChoiceDialog
from .query_builder_dialog import QueryBuilderDialog
from .target_picker import TargetPicker

# QGIS's native provider key for WFS - note this is uppercase, unlike
# the WMS/WMTS provider's lowercase "wms".
WFS_PROVIDER_KEY = "WFS"

_DEFAULT_VERSION = "2.0.0"
_DEFAULT_COUNT = 200

# URI schemes the download pipeline can fetch (urllib over HTTP/S);
# a column is only treated as a download link if its values use one.
_DOWNLOAD_SCHEMES = ("http", "https")
_FILENAME_FIELD_NAMES = ("name_download", "filename", "name")
_MD5_FIELD_NAMES = ("md5", "checksum", "checksum_md5", "hash_md5")
_GEOMETRY_FIELD_NAMES = ("wkt_geom", "geometrie", "geom", "geometry", "the_geom")
# Checked (case-insensitively, in order) when a row-selection-based
# "Add to map" needs to restrict the added layer to exactly the
# selected rows.
#
# Real, confirmed bug this order fixes: "fid" was checked first on the
# assumption that GeoServer's CSV output consistently carrying a "FID"
# column made it a reliable identifier - it does carry one, but that
# column is a synthetic pseudo-property (the raw GML feature id,
# automatically prepended to CSV output regardless of the feature
# type's own real schema), not necessarily a real, CQL_FILTER-
# filterable attribute at all. A real reported failure confirmed this
# directly: "Illegal property name: BDTOPO_V3:FID for feature type
# BDTOPO_V3:itineraire_autre" - CQL_FILTER operates on real schema
# attributes only, and this feature type's own real schema (confirmed
# via a live DescribeFeatureType fetch) has no "FID" attribute at all.
# "cleabs" ("clé absolue") is checked first instead - IGN's own real,
# confirmed cross-product unique identifier convention, present as an
# actual schema attribute across the whole BD TOPO family (confirmed
# directly in that same real schema - the very first field listed).
_ID_FIELD_NAMES = ("cleabs", "fid", "gml_id", "id", "objectid")


def build_wfs_uri(
    base_url: str,
    typename: str,
    srsname: Optional[str] = None,
    filter_expression: Optional[str] = None,
    restrict_to_bbox: bool = True,
) -> str:
    """Builds a connection string for QGIS's native WFS data provider.

    Raises:
        ValueError: if base_url or typename is empty - either would
            silently produce a connection string QGIS's provider can't
            actually use, with no clear error at the point it's built.
    """
    if not base_url or not typename:
        raise ValueError("base_url and typename must both be non-empty")
    uri = QgsDataSourceUri()
    uri.setParam("url", base_url)
    uri.setParam("typename", typename)
    uri.setParam("version", _DEFAULT_VERSION)
    uri.setParam("pagingEnabled", "true")
    uri.setParam("restrictToRequestBBOX", "1" if restrict_to_bbox else "0")
    if srsname:
        uri.setParam("srsname", srsname)
    if filter_expression:
        uri.setParam("filter", filter_expression)
    return uri.uri()


def is_download_uri(value) -> bool:
    """True when `value` is, as a whole, one URI with a scheme the
    downloader supports (_DOWNLOAD_SCHEMES) - no surrounding text, no
    whitespace. A JSON metadata blob, a plain id, an email address or a
    relative path is not."""
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        return False
    parsed = urlparse(value)
    return parsed.scheme.lower() in _DOWNLOAD_SCHEMES and bool(parsed.netloc)


def url_fields_for(row: dict) -> List[str]:
    """Every download-link field in a queried feature row, in column
    order - empty for a plain vector-layer-use feature type. Decided by
    the *values*, not the column names: a field qualifies when its value
    is a URI with a supported scheme (is_download_uri). WFS schemas
    declare such columns as plain xsd:string, so neither the type nor
    the name (IGN's LiDAR HD metadata layer: url_mnt, url_mns, url_mnh,
    url_npl; the retired ":dalle" layers: url) is a dependable signal,
    while the content is. Caveat: a layer whose only link column points
    to documentation rather than data would also qualify - the user
    chose to download."""
    return [key for key, value in row.items() if is_download_uri(value)]


def url_field_for(row: dict) -> Optional[str]:
    """The first download-link field of a row, or None - see
    url_fields_for for every one."""
    fields = url_fields_for(row)
    return fields[0] if fields else None


def product_label_for(field: str) -> str:
    """A short display label for a download-link field: "url_mnt" ->
    "MNT"; a plain "url" stays "url"."""
    lowered = field.lower()
    if lowered.startswith("url_"):
        return field[4:].upper()
    if lowered.endswith("_url"):
        return field[:-4].upper()
    return field


def filename_from_url(href: str) -> str:
    """The file name a download link delivers. A WMS GetMap link (IGN's
    LiDAR HD raster links) names its file in a FILENAME query parameter,
    and its path's last segment is just the service name; otherwise the
    path's last segment, without any query string."""
    parsed = urlparse(href)
    for key, values in parse_qs(parsed.query).items():
        if key.upper() == "FILENAME" and values and values[0]:
            return values[0]
    return unquote(parsed.path.rsplit("/", 1)[-1]) or "download"


def filename_for(row: dict) -> Optional[str]:
    for key in _FILENAME_FIELD_NAMES:
        if row.get(key):
            return row[key]
    return None


def md5_field_for(row: dict) -> Optional[str]:
    """The name of an md5-checksum-like field in a queried feature row,
    or None if it doesn't have one - most WFS file-index feature types
    won't; if one exists, it's used to actually verify a download's
    integrity rather than skipping that check entirely."""
    for key in row:
        if key.lower() in _MD5_FIELD_NAMES:
            return key
    return None


def guess_geometry_field(fieldnames) -> str:
    """Guesses which field is the geometry column, checked against real
    column names confirmed across different IGN WFS products this
    project has actually used - GeoServer's own CSV output has
    consistently used "wkt_geom" (LiDAR HD and similar); IGN's
    BDTOPO_V3 family (region, departement, epci, commune,
    parc_reserve, itineraire_autre, and others - confirmed via a real
    working IGN query using it, and via a real reported 400 error
    ("Illegal property name: geom") from this project's own old
    fallback guessing wrong for exactly this family) uses "geometrie"
    instead. Falls back to a plain, unconfirmed default ("geom") if
    none of these match - a real, known-imperfect trade-off: this
    function has no visibility into the feature type's actual schema
    (no DescribeFeatureType parsing exists in this project yet), and
    when the guess is wrong, the failure only surfaces as a real HTTP
    400 from the server, worded as "Illegal property name: <guess>"
    (see gateways.atom's own http_get, which now at least surfaces
    that real message rather than a generic status line) - not
    something this function can detect or warn about itself."""
    lowered = {name.lower(): name for name in fieldnames}
    for candidate in _GEOMETRY_FIELD_NAMES:
        if candidate in lowered:
            return lowered[candidate]
    return "geom"


def guess_id_field(fieldnames) -> Optional[str]:
    """Guesses which field is a reliable per-feature identifier, checked
    against _ID_FIELD_NAMES - best-effort, same convention as
    guess_geometry_field. Returns None (rather than a wrong-but-plausible
    guess) if nothing matches, since restricting "Add to map" to the
    wrong field would silently add the wrong features rather than fail
    loudly - the caller is expected to fall back to the whole-filter add
    and say why, not guess further.

    Still a guess, not the authoritative fix _current_geometry_field now
    has via DescribeFeatureType - a feature type with neither "cleabs"
    nor any of the other real candidates as an actual schema attribute,
    but that still carries a synthetic CSV "fid"/"id" pseudo-column,
    could hit the same class of failure this reordering just fixed for
    BDTOPO_V3, just for a different type. Not yet worth the added
    complexity of validating this guess against the same
    DescribeFeatureType schema already being fetched for the geometry
    field - flagged here as a real, known, deliberately-deferred gap,
    not a silently-accepted one."""
    lowered = {name.lower(): name for name in fieldnames}
    for candidate in _ID_FIELD_NAMES:
        if candidate in lowered:
            return lowered[candidate]
    return None


def build_id_list_filter(id_field: str, rows) -> str:
    """Builds a SQLite/CQL-compatible "field IN (...)" expression
    restricting to exactly the given rows' own values for id_field -
    quoted and escaped the same way manually-inserted values from the
    Values list already are (ui.expression_builder's own convention)."""
    quoted = [
        "'{}'".format(str(row.get(id_field, "")).replace("'", "''")) for row in rows
    ]
    return f"{id_field} IN ({', '.join(quoted)})"


def build_download_items_from_rows(
    rows: List[dict],
    source_display_name: str,
    layer_name: Optional[str] = None,
    fields: Optional[Sequence[str]] = None,
) -> List[pipeline.DownloadItem]:
    """Resolves query result rows carrying a url-like field into
    DownloadItems, skipping any row that doesn't have one. Each item's
    subdirectory is precomputed (source/category/layer) so a download
    batch doesn't land flatly in central_repo regardless of where it
    came from - see download.pipeline.build_download_subdirectory for
    what each segment actually means.

    fields, when given, restricts a row to those download-link fields
    (the products the user picked); otherwise a row's first link is used.
    A row with several links yields one item per chosen link, each named
    from its own URL (the row's "name_download"-style column describes
    only a single file, so it is used only for a one-link row)."""
    items = []
    for row in rows:
        row_fields = url_fields_for(row)
        chosen = (
            [f for f in row_fields if f in fields]
            if fields is not None
            else row_fields[:1]
        )
        for field in chosen:
            href = row.get(field)
            if not href:
                continue
            single_link = len(row_fields) == 1
            filename = (single_link and filename_for(row)) or filename_from_url(href)
            subdirectory = pipeline.build_download_subdirectory(
                source_display_name, filename, layer_name=layer_name
            )
            md5_field = md5_field_for(row)
            expected_md5 = row.get(md5_field) if md5_field and single_link else None
            items.append(
                pipeline.DownloadItem(
                    url=href,
                    filename=filename,
                    subdirectory=subdirectory,
                    expected_md5=expected_md5 or None,
                    product=None if single_link else product_label_for(field),
                )
            )
    return items


class WfsSourceSelectWidget(QgsAbstractDataSourceWidget):
    def __init__(
        self,
        parent=None,
        fl=Qt.WindowType(0),
        widget_mode=None,
        fetch=None,
        download_fn=None,
        progress_runner=None,
        product_chooser=None,
    ) -> None:
        # None means "use QGIS's own constructor default" - a bare int 0 is
        # QgsProviderRegistry.WidgetMode.None in QGIS 3 but that member
        # doesn't exist in QGIS 4 (0 is Standalone there), and Qt 6 rejects
        # a plain int for an enum argument outright.
        if widget_mode is None:
            super().__init__(parent, fl)
        else:
            super().__init__(parent, fl, widget_mode)
        self.fetch = fetch
        self.download_fn = download_fn
        self.progress_runner = progress_runner
        self.product_chooser = product_chooser
        self.feature_types = []  # [WfsFeatureTypeInfo, ...]
        # The real, authoritative geometry field name for each feature
        # type this session has actually looked up via DescribeFeatureType
        # (see _current_geometry_field) - keyed by typename, so (unlike
        # query_fieldnames) it's inherently safe against the cross-
        # feature-type staleness bug that guessing from query results
        # alone had: a cached answer for one typename can never be
        # mistaken for another's. None is a valid, cached value too -
        # "looked this type up and DescribeFeatureType had no usable
        # answer", not "haven't checked yet" (that's simply not being a
        # key in this dict at all).
        self._geometry_field_cache: Dict[str, Optional[str]] = {}
        self.query_fieldnames = []
        self.query_rows = []  # whatever is currently displayed (post-filter)
        self._all_query_rows = []  # the raw, unfiltered current page
        self.query_start_index = 0
        self.query_filtered_mode = False
        # Raw, as-typed SQLite-syntax filter text (not yet
        # token-substituted) - kept here rather than in a live widget
        # reference, since the query builder is now a dialog built fresh
        # each time it's opened (ui.query_builder_dialog). "" means no
        # filter is active.
        self._active_filter_text = ""
        self.sevenzip_exe = (
            sigate_settings.get_sevenzip_path_override() or find_7z_executable()
        )
        self._build_ui()
        self._load_initial_connection()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel(self.tr("Connection:")))
        self.connection_manager = ConnectionManager(
            "wfs", data_dir_provider=sigate_settings.get_sigate_data_dir, parent=self
        )
        self.connection_manager.connectionChanged.connect(self._on_connection_changed)
        source_row.addWidget(self.connection_manager)
        layout.addLayout(source_row)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel(self.tr("Filter feature types:")))
        self.layer_filter_edit = QLineEdit(self)
        self.layer_filter_edit.textChanged.connect(self._apply_layer_filter)
        filter_row.addWidget(self.layer_filter_edit)
        layout.addLayout(filter_row)

        self.list_widget = QListWidget(self)
        self.list_widget.currentItemChanged.connect(self._on_feature_type_changed)
        layout.addWidget(self.list_widget)

        query_row = QHBoxLayout()
        query_row.addWidget(QLabel(self.tr("Count:")))
        self.count_edit = QLineEdit(str(_DEFAULT_COUNT), self)
        self.count_edit.setFixedWidth(60)
        query_row.addWidget(self.count_edit)
        layout.addLayout(query_row)

        filter_action_row = QHBoxLayout()
        self.filter_button = QPushButton(self.tr("Filter..."), self)
        self.filter_button.clicked.connect(self._on_filter_button_clicked)
        filter_action_row.addWidget(self.filter_button)
        self.clear_filter_button = QPushButton(self.tr("Clear filter"), self)
        self.clear_filter_button.clicked.connect(self._on_clear_filter_clicked)
        filter_action_row.addWidget(self.clear_filter_button)
        self.filter_status_label = QLabel(self.tr("Filter: (none)"), self)
        filter_action_row.addWidget(self.filter_status_label)
        filter_action_row.addStretch()
        layout.addLayout(filter_action_row)

        query_action_row = QHBoxLayout()
        self.query_button = QPushButton(self.tr("Query features"), self)
        self.query_button.clicked.connect(self._on_query_clicked)
        query_action_row.addWidget(self.query_button)
        layout.addLayout(query_action_row)

        page_row = QHBoxLayout()
        self.prev_page_button = QPushButton(self.tr("< Prev page"), self)
        self.prev_page_button.setEnabled(False)
        self.prev_page_button.clicked.connect(self._prev_query_page)
        page_row.addWidget(self.prev_page_button)
        self.page_label = QLabel(self)
        page_row.addWidget(self.page_label)
        self.next_page_button = QPushButton(self.tr("Next page >"), self)
        self.next_page_button.setEnabled(False)
        self.next_page_button.clicked.connect(self._next_query_page)
        page_row.addWidget(self.next_page_button)
        layout.addLayout(page_row)

        result_filter_row = QHBoxLayout()
        result_filter_row.addWidget(QLabel(self.tr("Filter results (any field):")))
        self.result_filter_edit = QLineEdit(self)
        self.result_filter_edit.textChanged.connect(self._apply_result_filter)
        result_filter_row.addWidget(self.result_filter_edit)
        search_all_button = QPushButton(self.tr("Search ALL pages"), self)
        search_all_button.clicked.connect(self._search_all_pages)
        result_filter_row.addWidget(search_all_button)
        clear_result_filter_button = QPushButton(self.tr("Clear"), self)
        clear_result_filter_button.clicked.connect(self._clear_result_filter)
        result_filter_row.addWidget(clear_result_filter_button)
        layout.addLayout(result_filter_row)

        self.results_tree = QTreeWidget(self)
        self.results_tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self.results_tree)

        self.target_picker = TargetPicker(self)
        layout.addWidget(self.target_picker)

        download_row = QHBoxLayout()
        self.download_button = QPushButton(self.tr("Download selected"), self)
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self._on_download_selected_clicked)
        download_row.addWidget(self.download_button)
        self.download_all_button = QPushButton(self.tr("Download ALL pages"), self)
        self.download_all_button.clicked.connect(self._on_download_all_pages_clicked)
        download_row.addWidget(self.download_all_button)
        layout.addLayout(download_row)

        add_to_map_row = QHBoxLayout()
        self.add_button = QPushButton(self.tr("Add to map"), self)
        self.add_button.clicked.connect(self._on_add_clicked)
        add_to_map_row.addWidget(self.add_button)
        add_to_map_row.addWidget(
            QLabel(
                self.tr(
                    "(adds the selected row(s) if any are selected, "
                    "otherwise the full filtered result)"
                ),
                self,
            )
        )
        add_to_map_row.addStretch()
        layout.addLayout(add_to_map_row)

        self.status_label = QLabel(self)
        layout.addWidget(self.status_label)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _load_initial_connection(self):
        # ConnectionManager's own constructor already fired connectionChanged
        # once internally, before our slot was connected to it - picked up
        # explicitly here instead, now that the rest of the UI exists.
        self._on_connection_changed(self.connection_manager.current_connection())

    def _on_connection_changed(self, source) -> None:
        if source is None:
            return
        gateway = self.connection_manager.current_gateway()
        if gateway is None:
            return
        try:
            _, self.feature_types, _ = wfs_get_capabilities(
                gateway.base_url, fetch=self.fetch
            )
        except Exception as e:
            self._set_status(self.tr("Failed to load capabilities: {}").format(e))
            self.feature_types = []
            from qgis.core import Qgis, QgsMessageLog

            QgsMessageLog.logMessage(
                f"Failed to load WFS capabilities from {gateway.base_url!r}: {e!r}",
                "SIGate",
                Qgis.MessageLevel.Warning,
            )
        self._apply_layer_filter()
        self._set_status(
            self.tr("{} feature type(s) found").format(len(self.feature_types))
        )

    def _apply_layer_filter(self) -> None:
        needle = self.layer_filter_edit.text().strip().lower()
        self.list_widget.clear()
        for ft in self.feature_types:
            name, title = ft.name, ft.title
            if needle and needle not in name.lower() and needle not in title.lower():
                continue
            item = QListWidgetItem(f"{name} - {title}" if title else name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.list_widget.addItem(item)

    def _current_feature_type_crs(self) -> str:
        """The currently selected feature type's own declared CRS
        (WfsFeatureTypeInfo.default_crs) - used both to build a spatial
        filter's geometry literal in the CRS the server actually
        expects (_get_canvas_extent_wkt), and as the request's own
        SRSNAME parameter (_run_query) - the same value for both,
        deliberately: SRSNAME is what actually tells the server which
        CRS a request's filter geometry is expressed in (and which CRS
        to return features in), closing a real, previously-open
        ambiguity where a request's true CRS was left entirely to
        whatever a server defaults to when SRSNAME is omitted. Falls
        back to EPSG:4326 if the current selection can't be matched back
        to a known feature type (shouldn't normally happen, but a stale
        selection is safer to handle than to crash on)."""
        typename = self._current_typename()
        for ft in self.feature_types:
            if ft.name == typename:
                return ft.default_crs
        return "EPSG:4326"

    def _current_typename(self) -> Optional[str]:
        item = self.list_widget.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_feature_type_changed(self, _current, _previous) -> None:
        """A different feature type means a different (or as-yet-unknown)
        field schema - clears every piece of state tied to the previous
        type's query results, not just the active filter text.

        Regression fix for a real reported bug: query_fieldnames itself
        was left out of this reset, even though this method's own
        original reasoning ("a different field schema") already applied
        to it just as much as to the filter text - switching feature
        types without re-querying first left the geometry-field guess
        (_current_geometry_field, used to substitute the $geom token)
        silently working from a completely different, unrelated feature
        type's real column names. A field name that happened to be a
        real column on whatever was queried *before* switching (a
        plausible, common case - "geom" itself is a real column name on
        plenty of layers) would be guessed with full confidence and
        substituted into a filter that then failed against the actual,
        now-selected feature type - indistinguishable from a wrong
        guess with no signal anything was stale at all.
        """
        self._active_filter_text = ""
        self._all_query_rows = []
        self.query_start_index = 0
        self.query_filtered_mode = False
        self._render_results([], [])  # also resets query_fieldnames/query_rows
        self.page_label.setText(self.tr("Not queried yet"))
        self._update_filter_status_label()

    def _update_filter_status_label(self) -> None:
        if self._active_filter_text:
            self.filter_status_label.setText(
                self.tr("Filter: {}").format(self._active_filter_text)
            )
        else:
            self.filter_status_label.setText(self.tr("Filter: (none)"))

    def _current_geometry_field(self) -> str:
        """The geometry field name to substitute for the $geom token.

        Tries the real, authoritative source first: DescribeFeatureType
        (gateways.wfs.wfs_describe_geometry_field), the standard OGC WFS
        operation that answers this definitively from the feature type's
        own schema - not a guess over column names seen in query results.
        Cached per typename (self._geometry_field_cache) so this is a
        real network request only the first time a given feature type is
        actually needed, not on every call.

        Falls back to guess_geometry_field(self.query_fieldnames) only
        when DescribeFeatureType itself had no usable answer (a request
        failure, an unparseable response, or - a real, possible case - a
        feature type whose schema genuinely has no geometry element) -
        this fallback is exactly the guessing heuristic this whole
        DescribeFeatureType-based approach was built to stop needing to
        rely on, kept only as a last resort so a DescribeFeatureType
        outage doesn't block the $geom shortcut outright. Falls further
        back to the plain "geom" default if even the guess has nothing
        to work with."""
        typename = self._current_typename()
        if typename is None:
            return "geom"
        if typename not in self._geometry_field_cache:
            gateway = self.connection_manager.current_gateway()
            try:
                self._geometry_field_cache[typename] = wfs_describe_geometry_field(
                    gateway.base_url, typename, fetch=self.fetch
                )
            except Exception:
                self._geometry_field_cache[typename] = None
        authoritative = self._geometry_field_cache[typename]
        if authoritative:
            return authoritative
        return (
            guess_geometry_field(self.query_fieldnames)
            if self.query_fieldnames
            else "geom"
        )

    def _get_canvas_extent_wkt(self, qgis_axis_order: bool = False) -> str:
        """Builds a real WKT polygon from the current QGIS map canvas
        extent, reprojected into the currently selected feature type's
        own declared CRS (gateways.wfs's WfsFeatureTypeInfo.default_crs,
        parsed from GetCapabilities) - so a spatial filter's geometry
        literal is expressed in the CRS the server actually expects for
        that feature type, not assumed to already match the project's
        own display CRS.

        Regression fix for a real reported failure: a spatial filter
        against a feature type whose declared CRS is EPSG:4326
        (confirmed directly - geographic, not projected, unlike every
        other source this project had wired in until now) returned zero
        results with both WITHIN and INTERSECTS, ruling out a predicate-
        semantics mistake and leaving axis order as the remaining
        explanation. EPSG:4326 is authority-defined with lat,lon axis
        order (per the EPSG registry), but QGIS's own QgsGeometry.asWkt()
        always emits coordinates as x,y internally regardless of a
        CRS's authority-defined order - for a plain projected CRS (every
        source already confirmed working - IGN's Lambert-93, geodienste.ch's
        LV95) x,y is unambiguous and this was never an issue, but for a
        geographic CRS like this one, x,y (lon,lat) and the
        authority-defined lat,lon order are genuinely different, and
        which one a given WFS server's CQL parser actually expects for a
        filter geometry literal isn't universally standardized (unlike,
        say, a GML response's own coordinate order, which is more
        consistently spec-compliant across implementations) - so this
        was not caught nor fixable by guessing without the user's own
        empirical test (WITHIN and INTERSECTS both returning zero
        against a confirmed-4326 layer) actually ruling out the
        simpler, more common predicate-semantics explanation first.

        target_crs.hasAxisInverted() is QGIS's own authority-order flag
        (true for EPSG:4326 and similar lat,lon-authority CRSes, false
        for essentially every projected CRS) - when true, the extent's
        X/Y are swapped into a new QgsRectangle before serializing, so
        the resulting WKT's coordinate pairs come out as (lat, lon)
        rather than QGIS's own default (lon, lat) - not independently
        confirmed against a live server response this session (no real
        QGIS available in this sandbox to test against), but directly
        informed by the user's own two-step empirical elimination
        rather than guessed from first principles alone."""
        canvas = iface.mapCanvas()
        source_crs = canvas.mapSettings().destinationCrs()
        target_crs = QgsCoordinateReferenceSystem(self._current_feature_type_crs())
        if not target_crs.isValid():
            target_crs = source_crs
        transform = QgsCoordinateTransform(
            source_crs, target_crs, QgsProject.instance()
        )
        try:
            extent = transform.transformBoundingBox(canvas.extent())
        except Exception:
            extent = canvas.extent()
        if target_crs.hasAxisInverted() and not qgis_axis_order:
            extent = QgsRectangle(
                extent.yMinimum(),
                extent.xMinimum(),
                extent.yMaximum(),
                extent.xMaximum(),
            )
        return QgsGeometry.fromRect(extent).asWkt()

    def _on_filter_button_clicked(self) -> None:
        """Opens the query builder as its own panel (QueryBuilderDialog),
        seeded with whatever feature schema/sample rows are available for
        the currently selected feature type, and any filter already
        active. On accept, replaces the active filter and re-runs the
        current query immediately, so the results grid updates right
        away to only the matching features. On cancel, nothing changes.

        If the selected feature type hasn't been queried yet, a first
        page is fetched automatically here first - the panel is always
        initialised from the currently selected layer, not from whatever
        happened to be queried last."""
        typename = self._current_typename()
        if typename is None:
            QMessageBox.information(
                self,
                self.tr("No feature type selected"),
                self.tr("Select a feature type first."),
            )
            return
        if not self.query_fieldnames:
            self.query_start_index = 0
            self._run_query(typename, start_index=0)

        dialog = QueryBuilderDialog(
            self, get_canvas_extent_wkt=self._get_canvas_extent_wkt
        )
        dialog.set_fields_and_rows(
            self.query_fieldnames,
            self._all_query_rows,
            geometry_field_name=self._current_geometry_field(),
        )
        if self._active_filter_text:
            dialog.set_expression_text(self._active_filter_text)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._active_filter_text = dialog.expression_text()
        self._update_filter_status_label()
        self.query_start_index = 0
        self._run_query(typename, start_index=0)

    def _on_clear_filter_clicked(self):
        if not self._active_filter_text:
            return
        self._active_filter_text = ""
        self._update_filter_status_label()
        typename = self._current_typename()
        if typename is not None:
            self.query_start_index = 0
            self._run_query(typename, start_index=0)

    def _current_filter_expression(self, for_qgis: bool = False) -> Optional[str]:
        """The active filter with its tokens resolved. for_qgis=False
        (querying): CQL for the server, geometry literal in the feature
        type's CRS with the authority axis order the server expects.
        for_qgis=True ("Add to map"): a QGIS expression for the native
        provider, geometry literal in the same CRS but in QGIS's own x,y
        order - see expression_builder.substitute_tokens_for_qgis for why
        the two can't share one text."""
        if not self._active_filter_text:
            return None
        map_extent_wkt = (
            self._get_canvas_extent_wkt(qgis_axis_order=for_qgis)
            if MAP_EXTENT_TOKEN in self._active_filter_text
            else None
        )
        if for_qgis:
            resolved = substitute_tokens_for_qgis(
                self._active_filter_text, map_extent_wkt
            )
        else:
            resolved = substitute_tokens(
                self._active_filter_text,
                self._current_geometry_field(),
                map_extent_wkt,
            )
        return resolved or None

    def _current_count(self) -> int:
        try:
            return int(self.count_edit.text().strip())
        except ValueError:
            return _DEFAULT_COUNT

    def _on_add_clicked(self) -> None:
        """Adds a layer for exactly the currently selected result rows,
        if any are selected - not the broader active filter. With
        nothing selected, falls back to the whole filtered result (or
        the whole layer, if no filter is active either).

        Selection-based restriction needs a reliable per-feature
        identifier field among the currently displayed columns
        (guess_id_field) to build a "field IN (...)" filter from the
        selected rows' own values - if none can be found, falls back to
        the whole-filter behaviour too, but says so explicitly rather
        than silently adding more than what's selected."""
        typename = self._current_typename()
        if typename is None:
            QMessageBox.information(
                self,
                self.tr("No feature type selected"),
                self.tr("Select a feature type first."),
            )
            return

        selected_indices = [
            self.results_tree.indexOfTopLevelItem(item)
            for item in self.results_tree.selectedItems()
        ]
        filter_expression = self._current_filter_expression(for_qgis=True)
        layer_name = typename
        # Only the id-list-based selection path below overrides this to
        # False - restricting to the current viewport is reasonable for
        # "add whatever's visible", but wrong once a specific, fixed set
        # of features has already been chosen by id: that filter is
        # already a complete, canvas-independent selector, and combining
        # it with a BBOX adds nothing useful - only the real risk of
        # silently dropping a selected feature that's since scrolled out
        # of view (panning/zooming after selecting, before clicking Add,
        # is an entirely normal thing to do), and unnecessary request
        # length on top of an already-long id list (a real, confirmed
        # contributor to a real reported failure - see build_wfs_uri's
        # own restrict_to_bbox parameter).
        restrict_to_bbox = True

        if selected_indices:
            id_field = guess_id_field(self.query_fieldnames)
            if id_field is None:
                self._set_status(
                    self.tr(
                        "{} row(s) selected, but no identifying field was "
                        "found to restrict the layer to just those - added "
                        "the full filtered result instead."
                    ).format(len(selected_indices))
                )
            else:
                # A selection surviving a re-render (e.g. a filter
                # applied between selecting rows and clicking Add) could
                # reference a now out-of-range index - guarded against
                # rather than letting IndexError propagate, since
                # query_rows is reassigned wholesale on every render
                # (_render_results), not updated in place.
                selected_rows = [
                    self.query_rows[i]
                    for i in selected_indices
                    if 0 <= i < len(self.query_rows)
                ]
                filter_expression = build_id_list_filter(id_field, selected_rows)
                layer_name = self.tr("{} ({} selected)").format(
                    typename, len(selected_indices)
                )
                restrict_to_bbox = False

        gateway = self.connection_manager.current_gateway()
        uri = build_wfs_uri(
            gateway.base_url,
            typename,
            # Same CRS the filter's geometry literal was built in, so the
            # layer's CRS and the literal agree (the query path already
            # sends it as SRSNAME for the same reason).
            srsname=self._current_feature_type_crs(),
            filter_expression=filter_expression,
            restrict_to_bbox=restrict_to_bbox,
        )
        with group_scope(self.connection_manager.current_connection().display_name):
            emit_in_scope(
                lambda: self.addVectorLayer.emit(uri, layer_name, WFS_PROVIDER_KEY)
            )

    # --------------------------------------------------------------- querying

    def _render_results(self, fieldnames: List[str], rows: List[dict]) -> None:
        """Renders `rows` into the results tree using `fieldnames` as
        columns. Used for a fresh query, a live result filter, and an
        all-pages search alike - query_rows always reflects whatever is
        actually displayed, since selection indexes into it."""
        self.query_fieldnames = fieldnames
        self.query_rows = rows
        self.results_tree.clear()
        self.results_tree.setColumnCount(len(fieldnames))
        self.results_tree.setHeaderLabels(fieldnames)
        for row in rows:
            values = []
            for field in fieldnames:
                value = row.get(field, "")
                if value and len(value) > 60:
                    value = value[:57] + "..."
                values.append(value)
            self.results_tree.addTopLevelItem(QTreeWidgetItem(values))
        self.download_button.setEnabled(any(url_fields_for(row) for row in rows))

    def _on_query_clicked(self) -> None:
        typename = self._current_typename()
        if typename is None:
            QMessageBox.information(
                self,
                self.tr("No feature type selected"),
                self.tr("Select a feature type first."),
            )
            return
        self.query_start_index = 0
        self._run_query(typename, start_index=0)

    def _run_query(self, typename: str, start_index: int) -> None:
        gateway = self.connection_manager.current_gateway()
        count = self._current_count()
        filter_expression = self._current_filter_expression()

        # Empty the grid immediately, before the fetch even starts - a
        # query in progress should never leave the previous query's rows
        # sitting there looking like they might still be current,
        # whether this attempt succeeds or fails.
        self._render_results([], [])
        self.page_label.setText(self.tr("Querying..."))
        self._set_status(self.tr("Querying..."))

        try:
            _, fieldnames, rows, fmt, _ = wfs_get_features(
                gateway.base_url,
                typename,
                count=count,
                start_index=start_index,
                cql_filter=filter_expression,
                srsname=self._current_feature_type_crs(),
                fetch=self.fetch,
            )
        except Exception as e:
            # A message box makes a query failure impossible to miss
            # (as opposed to only the small status label at the bottom),
            # and names whether a filter was even involved - a filter's
            # server-side rejection is a real, plausible way a failed
            # query could otherwise look like "the filter did nothing".
            # The grid is already empty (cleared above), not showing
            # stale data.
            message = self.tr("Query failed: {}").format(e)
            self._set_status(message)
            self.page_label.setText(self.tr("Query failed"))
            from qgis.core import Qgis, QgsMessageLog

            QgsMessageLog.logMessage(
                f"WFS query failed for {typename!r} "
                f"(filter={filter_expression!r}): {e!r}",
                "SIGate",
                Qgis.MessageLevel.Warning,
            )
            QMessageBox.warning(
                self,
                self.tr("Query failed"),
                message
                + (
                    self.tr("\n\nA filter was active for this query:\n{}").format(
                        filter_expression
                    )
                    if filter_expression
                    else ""
                ),
            )
            return

        self.query_filtered_mode = False
        self.result_filter_edit.setText("")
        self.query_start_index = start_index
        self._all_query_rows = rows
        self._render_results(fieldnames, rows)

        # WFS's CSV output carries no reliable declared total the way
        # Atom's feed attributes do - "a full page came back" is treated
        # as "there might be more," which is a real, accepted limitation
        # for this manual, human-observed pagination (as opposed to an
        # unattended bulk operation, where the same heuristic would be a
        # correctness risk rather than just a minor inconvenience).
        self.prev_page_button.setEnabled(start_index > 0)
        self.next_page_button.setEnabled(len(rows) == count)
        self.page_label.setText(
            self.tr("startIndex {}, {} feature(s) returned (format: {})").format(
                start_index, len(rows), fmt
            )
        )
        has_download_links = any(url_fields_for(row) for row in rows)
        # Names whether a filter was actually part of this query, not
        # just whether one is currently displayed in the status label -
        # confirms the filter genuinely reached the server for this
        # specific request, since the grid staying the same either way
        # would otherwise be indistinguishable from "the filter was
        # silently ignored" versus "applied but matched everything."
        filter_note = (
            self.tr(" (filter applied: {})").format(filter_expression)
            if filter_expression
            else self.tr(" (no filter)")
        )
        self._set_status(
            self.tr("{} feature(s) returned{}{}").format(
                len(rows),
                self.tr(" - has downloadable file(s)") if has_download_links else "",
                filter_note,
            )
        )

    def _prev_query_page(self) -> None:
        typename = self._current_typename()
        if typename is None or self.query_start_index <= 0:
            return
        count = self._current_count()
        self._run_query(typename, start_index=max(0, self.query_start_index - count))

    def _next_query_page(self) -> None:
        typename = self._current_typename()
        if typename is None:
            return
        self._run_query(
            typename, start_index=self.query_start_index + self._current_count()
        )

    # --------------------------------------------------------------- result filtering

    def _apply_result_filter(self) -> None:
        """Live, instant filter over whatever page of results is
        currently loaded - no extra fetch. Does nothing while an
        all-pages search is active, since that already represents a
        filtered view spanning more than just the current page."""
        if self.query_filtered_mode:
            return
        needle = self.result_filter_edit.text().strip().lower()
        if not needle:
            self._render_results(self.query_fieldnames, self._all_query_rows)
            return
        matched = [
            row
            for row in self._all_query_rows
            if any(needle in str(v).lower() for v in row.values())
        ]
        self._render_results(self.query_fieldnames, matched)

    def _clear_result_filter(self) -> None:
        self.result_filter_edit.setText("")
        if self.query_filtered_mode:
            self.query_filtered_mode = False
            typename = self._current_typename()
            if typename is not None:
                self._run_query(typename, start_index=0)
        else:
            self._render_results(self.query_fieldnames, self._all_query_rows)

    def _search_all_pages(self) -> None:
        """Fetches every page of the current query first (hits-driven,
        so a server capping a page below what was requested can't
        silently truncate the result - the exact bug this pagination
        scheme was designed to avoid), then filters the full result -
        for finding one specific feature that might be sitting on a page
        far beyond whatever is currently displayed."""
        typename = self._current_typename()
        if typename is None:
            QMessageBox.information(
                self,
                self.tr("No feature type selected"),
                self.tr("Select a feature type first."),
            )
            return
        needle = self.result_filter_edit.text().strip()
        if not needle:
            QMessageBox.information(
                self,
                self.tr("No filter text"),
                self.tr("Type something in the filter field first."),
            )
            return

        gateway = self.connection_manager.current_gateway()
        count = self._current_count()
        try:
            total = wfs_get_hits(gateway.base_url, typename, fetch=self.fetch)
        except Exception as e:
            # Falls back to an unknown total (no pre-fetch size warning)
            # rather than blocking the search - a server not supporting
            # the hits request is a real, accepted possibility, not
            # treated as a fatal error for this feature.
            total = None
            from qgis.core import Qgis, QgsMessageLog

            QgsMessageLog.logMessage(
                f"Could not get feature count (hits) for {typename!r}: {e!r}",
                "SIGate",
                Qgis.MessageLevel.Info,
            )

        if total is not None and total > 2000:
            proceed = QMessageBox.question(
                self,
                self.tr("Large layer"),
                self.tr(
                    '"{}" has {} feature(s) total. Searching all pages means fetching all of them first. Continue?'
                ).format(typename, total),
            )
            if proceed != QMessageBox.StandardButton.Yes:
                return

        self._set_status(self.tr("Fetching all pages..."))
        try:
            rows, fieldnames, fmt = wfs_fetch_all_pages(
                gateway.base_url,
                typename,
                page_size=count,
                expected_total=total,
                fetch=self.fetch,
            )
        except Exception as e:
            self._set_status(self.tr("Failed to fetch all pages: {}").format(e))
            from qgis.core import Qgis, QgsMessageLog

            QgsMessageLog.logMessage(
                f"Failed to fetch all pages for {typename!r}: {e!r}",
                "SIGate",
                Qgis.MessageLevel.Warning,
            )
            return

        lowered = needle.lower()
        matched = [
            row for row in rows if any(lowered in str(v).lower() for v in row.values())
        ]
        self.query_filtered_mode = True
        self._render_results(fieldnames, matched)
        self.prev_page_button.setEnabled(False)
        self.next_page_button.setEnabled(False)
        self.page_label.setText(
            self.tr('filtered: {} / {} match "{}" (all pages)').format(
                len(matched), len(rows), needle
            )
        )
        self._set_status(
            self.tr("Searched all pages: {} of {} feature(s) match.").format(
                len(matched), len(rows)
            )
        )

    # --------------------------------------------------------------- download

    def _choose_download_fields(self, rows: List[dict]) -> Optional[List[str]]:
        """Which download-link fields to fetch for `rows`. With one link
        column there is nothing to ask; with several (e.g. LiDAR HD's
        url_mnt/mns/mnh/npl) asks via ProductChoiceDialog, pre-ticking
        everything except point clouds (far larger). Returns None when
        the user cancels or ticks nothing. `product_chooser` is
        injectable (a callable taking the products dict, the
        preselected set and the row count, returning a field list or
        None) so tests need no real dialog."""
        products: Dict[str, str] = {}
        for row in rows:
            for field in url_fields_for(row):
                products.setdefault(field, product_label_for(field))
        if len(products) <= 1:
            return list(products)
        sample = next(
            (r for r in rows if all(f in url_fields_for(r) for f in products)), rows[0]
        )
        preselected = {
            f
            for f in products
            if not str(filename_from_url(str(sample.get(f, ""))))
            .lower()
            .endswith((".laz", ".las"))
        }
        chooser = self.product_chooser or self._show_product_dialog
        chosen = chooser(products, preselected, len(rows))
        return chosen or None

    def _show_product_dialog(
        self, products: Dict[str, str], preselected, feature_count: int
    ) -> Optional[List[str]]:
        dialog = ProductChoiceDialog(self, products, set(preselected), feature_count)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.chosen_fields()

    def _on_download_selected_clicked(self) -> None:
        selected_indices = [
            self.results_tree.indexOfTopLevelItem(item)
            for item in self.results_tree.selectedItems()
        ]
        if not selected_indices:
            QMessageBox.information(
                self,
                self.tr("Nothing selected"),
                self.tr("Select one or more result rows first."),
            )
            return

        selected_rows = [
            self.query_rows[i]
            for i in selected_indices
            if 0 <= i < len(self.query_rows)
        ]
        fields = self._choose_download_fields(selected_rows)
        if fields is None:
            return
        items = build_download_items_from_rows(
            selected_rows,
            self.connection_manager.current_connection().display_name,
            layer_name=self._current_typename(),
            fields=fields,
        )
        if not items:
            QMessageBox.information(
                self,
                self.tr("Nothing to download"),
                self.tr(
                    "None of the selected rows have a downloadable file - only some WFS layers carry one."
                ),
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
            group_base=[self.connection_manager.current_connection().display_name],
        )

    def _on_download_all_pages_clicked(self) -> None:
        """Fetches every page of the current query first (ignoring
        whatever filter is currently displayed - this is deliberately
        "every downloadable file this query would ever return," not just
        what's on screen; use Download selected on a filtered view
        instead for a targeted subset), then downloads every row with a
        url-like field."""
        typename = self._current_typename()
        if typename is None:
            QMessageBox.information(
                self,
                self.tr("No feature type selected"),
                self.tr("Select a feature type first."),
            )
            return

        gateway = self.connection_manager.current_gateway()
        count = self._current_count()
        try:
            total = wfs_get_hits(gateway.base_url, typename, fetch=self.fetch)
        except Exception as e:
            total = None
            from qgis.core import Qgis, QgsMessageLog

            QgsMessageLog.logMessage(
                f"Could not get feature count (hits) for {typename!r}: {e!r}",
                "SIGate",
                Qgis.MessageLevel.Info,
            )

        message = self.tr('Fetch and download everything "{}" has to offer?').format(
            typename
        )
        if total is not None:
            message = self.tr('"{}" has {} feature(s) total. {}').format(
                typename, total, message
            )
        if (
            not QMessageBox.question(self, self.tr("Download all pages"), message)
            == QMessageBox.StandardButton.Yes
        ):
            return

        self._set_status(self.tr("Fetching all pages..."))
        try:
            rows, _, _ = wfs_fetch_all_pages(
                gateway.base_url,
                typename,
                page_size=count,
                expected_total=total,
                fetch=self.fetch,
            )
        except Exception as e:
            self._set_status(self.tr("Failed to fetch all pages: {}").format(e))
            from qgis.core import Qgis, QgsMessageLog

            QgsMessageLog.logMessage(
                f"Failed to fetch all pages for {typename!r}: {e!r}",
                "SIGate",
                Qgis.MessageLevel.Warning,
            )
            return

        fields = self._choose_download_fields(rows)
        if fields is None:
            return
        items = build_download_items_from_rows(
            rows,
            self.connection_manager.current_connection().display_name,
            layer_name=typename,
            fields=fields,
        )
        if not items:
            QMessageBox.information(
                self,
                self.tr("Nothing to download"),
                self.tr(
                    "Fetched {} feature(s) across all pages, but none had a downloadable file."
                ).format(len(rows)),
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
            group_base=[self.connection_manager.current_connection().display_name],
        )

    def _add_layer(self, path: Path) -> None:
        kind = download_flow.guess_layer_kind(path)
        if kind == "raster":
            emit_in_scope(
                lambda: self.addRasterLayer.emit(str(path), path.stem, "gdal")
            )
        elif kind == "vector":
            emit_in_scope(lambda: self.addVectorLayer.emit(str(path), path.stem, "ogr"))
        elif kind == "point_cloud":
            emit_in_scope(
                lambda: self.addPointCloudLayer.emit(
                    str(path), path.name[: -len(".copc.laz")], "copc"
                )
            )
