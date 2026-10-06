"""
ui.arcgis_rest_widget - the SIGate ArcGIS REST tab in QGIS's Data Source
Manager: browse the layers of an ArcGIS MapServer/FeatureServer service,
filter them with a SQL where clause (and optionally the current map
extent), look at the matching features in a table, and add a layer - or
just the selected rows - to the map.

The WFS tab's counterpart for services that publish vector data through
Esri's REST API rather than OGC WFS (Arpa Piemonte's SIVA avalanche
service is the motivating case: a WMS exists, a WFS does not). The actual
adding is handed to QGIS's own "arcgisfeatureserver" provider, the same
way the WFS tab hands off to the native WFS provider.

A difference worth remembering: that provider takes its filter as an
`sql=` where clause in the connection string (confirmed live: it narrowed
a 4,091-feature layer to 292), not the `filter=` parameter the native WFS
provider uses - an unknown `filter=` is silently ignored.
"""

from typing import Callable, Dict, List, Optional

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDataSourceUri,
    QgsMessageLog,
    QgsProject,
)
from qgis.gui import QgsAbstractDataSourceWidget
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox,
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

from sigate.gateways.arcgis_rest import (
    DEFAULT_PAGE_SIZE,
    ArcGisLayerDetail,
    ArcGisService,
    arcgis_count,
    arcgis_get_layer,
    arcgis_get_service,
    arcgis_query_rows,
    arcgis_service_url,
    build_object_id_filter,
)

from . import settings as sigate_settings
from .connection_manager import ConnectionManager
from .layer_groups import emit_in_scope, group_scope
from .query_builder_dialog import QueryBuilderDialog

ARCGIS_FEATURE_PROVIDER_KEY = "arcgisfeatureserver"
GATEWAY_TYPE = "arcgis_rest"


def build_afs_uri(
    layer_url: str, epsg: Optional[str], where: Optional[str] = None
) -> str:
    """A connection string for QGIS's native ArcGIS provider. The filter
    goes in `sql=` (see the module docstring), and the layer's own CRS is
    stated explicitly rather than left to the provider to guess."""
    uri = QgsDataSourceUri()
    uri.setParam("url", layer_url)
    if epsg:
        uri.setParam("crs", epsg)
    if where:
        uri.setSql(where)
    return uri.uri(False)


class ArcGisRestSourceSelectWidget(QgsAbstractDataSourceWidget):
    def __init__(
        self,
        parent=None,
        fl=Qt.WindowType(0),
        widget_mode=None,
        fetch: Optional[Callable] = None,
    ) -> None:
        # None -> QGIS's own default; see WfsSourceSelectWidget.__init__.
        if widget_mode is None:
            super().__init__(parent, fl)
        else:
            super().__init__(parent, fl, widget_mode)
        self.fetch = fetch
        self.service: Optional[ArcGisService] = None
        self.layer_detail: Optional[ArcGisLayerDetail] = None
        self.query_fieldnames: List[str] = []
        self.query_rows: List[Dict[str, str]] = []  # what is displayed
        self._all_query_rows: List[Dict[str, str]] = []  # the raw current page
        self.query_offset = 0
        self.total_count: Optional[int] = None
        self._more_available = False
        # Raw where-clause text; "" means no filter.
        self._active_filter_text = ""
        self._build_ui()
        self._load_initial_connection()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel(self.tr("Connection:")))
        self.connection_manager = ConnectionManager(
            GATEWAY_TYPE,
            data_dir_provider=sigate_settings.get_sigate_data_dir,
            parent=self,
        )
        self.connection_manager.connectionChanged.connect(self._on_connection_changed)
        source_row.addWidget(self.connection_manager)
        layout.addLayout(source_row)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel(self.tr("Filter layers:")))
        self.layer_filter_edit = QLineEdit(self)
        self.layer_filter_edit.textChanged.connect(self._apply_layer_filter)
        filter_row.addWidget(self.layer_filter_edit)
        layout.addLayout(filter_row)

        self.list_widget = QListWidget(self)
        self.list_widget.currentItemChanged.connect(self._on_layer_changed)
        layout.addWidget(self.list_widget)

        self.layer_info_label = QLabel(self)
        layout.addWidget(self.layer_info_label)

        query_row = QHBoxLayout()
        query_row.addWidget(QLabel(self.tr("Count:")))
        self.count_edit = QLineEdit(str(DEFAULT_PAGE_SIZE), self)
        self.count_edit.setFixedWidth(60)
        query_row.addWidget(self.count_edit)
        self.extent_checkbox = QCheckBox(self.tr("Limit to current map extent"), self)
        query_row.addWidget(self.extent_checkbox)
        query_row.addStretch()
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

        self.query_button = QPushButton(self.tr("Query features"), self)
        self.query_button.clicked.connect(self._on_query_clicked)
        layout.addWidget(self.query_button)

        page_row = QHBoxLayout()
        self.prev_page_button = QPushButton(self.tr("< Prev page"), self)
        self.prev_page_button.setEnabled(False)
        self.prev_page_button.clicked.connect(self._prev_page)
        page_row.addWidget(self.prev_page_button)
        self.page_label = QLabel(self.tr("Not queried yet"), self)
        page_row.addWidget(self.page_label)
        self.next_page_button = QPushButton(self.tr("Next page >"), self)
        self.next_page_button.setEnabled(False)
        self.next_page_button.clicked.connect(self._next_page)
        page_row.addWidget(self.next_page_button)
        layout.addLayout(page_row)

        result_filter_row = QHBoxLayout()
        result_filter_row.addWidget(QLabel(self.tr("Filter results (any field):")))
        self.result_filter_edit = QLineEdit(self)
        self.result_filter_edit.textChanged.connect(self._apply_result_filter)
        result_filter_row.addWidget(self.result_filter_edit)
        layout.addLayout(result_filter_row)

        self.results_tree = QTreeWidget(self)
        self.results_tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self.results_tree)

        add_row = QHBoxLayout()
        self.add_button = QPushButton(self.tr("Add to map"), self)
        self.add_button.clicked.connect(self._on_add_clicked)
        add_row.addWidget(self.add_button)
        add_row.addWidget(
            QLabel(
                self.tr(
                    "(adds the selected row(s) if any are selected, "
                    "otherwise every feature matching the filter)"
                ),
                self,
            )
        )
        add_row.addStretch()
        layout.addLayout(add_row)

        self.status_label = QLabel(self)
        layout.addWidget(self.status_label)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    # ------------------------------------------------------------ connection

    def _load_initial_connection(self) -> None:
        self._on_connection_changed(self.connection_manager.current_connection())

    def _current_gateway(self):
        # The selected combo entry's own gateway - source.gateway(type)
        # would return only the first-declared instance of a source that
        # offers several services of this type.
        return self.connection_manager.current_gateway()

    def _on_connection_changed(self, source) -> None:
        if source is None:
            return
        gateway = self._current_gateway()
        if gateway is None:
            return
        try:
            self.service = arcgis_get_service(gateway.base_url, fetch=self.fetch)
        except Exception as e:
            self.service = None
            self._set_status(self.tr("Failed to load the service: {}").format(e))
            QgsMessageLog.logMessage(
                f"Failed to load ArcGIS REST service {gateway.base_url!r}: {e!r}",
                "SIGate",
                Qgis.MessageLevel.Warning,
            )
        self._apply_layer_filter()
        if self.service is not None:
            self._set_status(
                self.tr("{} layer(s) found").format(
                    len(self.service.queryable_layers())
                )
            )

    def _apply_layer_filter(self) -> None:
        needle = self.layer_filter_edit.text().strip().lower()
        self.list_widget.clear()
        if self.service is None:
            return
        for layer in self.service.queryable_layers():
            label = self.service.path_for(layer)
            if needle and needle not in label.lower():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, layer.id)
            self.list_widget.addItem(item)

    # ---------------------------------------------------------------- layer

    def _current_layer_id(self) -> Optional[int]:
        item = self.list_widget.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_layer_changed(self, _current, _previous) -> None:
        """A different layer means a different schema: clears every piece
        of state tied to the previous layer's results, then reads the new
        layer's own description (fields, geometry type, CRS)."""
        self._active_filter_text = ""
        self._all_query_rows = []
        self.query_offset = 0
        self.total_count = None
        self.layer_detail = None
        self._render_results([], [])
        self.page_label.setText(self.tr("Not queried yet"))
        self._update_filter_status_label()
        self.layer_info_label.setText("")
        layer_id = self._current_layer_id()
        gateway = self._current_gateway()
        if layer_id is None or gateway is None:
            return
        try:
            self.layer_detail = arcgis_get_layer(
                gateway.base_url, layer_id, fetch=self.fetch
            )
        except Exception as e:
            self._set_status(self.tr("Failed to read the layer: {}").format(e))
            return
        detail = self.layer_detail
        if not detail.can_query:
            self._set_status(self.tr("This layer cannot be queried."))
            return
        self.layer_info_label.setText(
            self.tr("{} — {} — {} — {} field(s)").format(
                detail.name.strip(),
                detail.geometry_type.replace("esriGeometry", "") or "?",
                detail.epsg or "?",
                len(detail.fields),
            )
        )

    def _update_filter_status_label(self) -> None:
        if self._active_filter_text:
            self.filter_status_label.setText(
                self.tr("Filter: {}").format(self._active_filter_text)
            )
        else:
            self.filter_status_label.setText(self.tr("Filter: (none)"))

    # --------------------------------------------------------------- filter

    def _on_filter_button_clicked(self) -> None:
        layer_id = self._current_layer_id()
        if layer_id is None or self.layer_detail is None:
            QMessageBox.information(
                self,
                self.tr("No layer selected"),
                self.tr("Select a layer first."),
            )
            return
        if not self.query_fieldnames:
            self.query_offset = 0
            self._run_query(0)
        dialog = QueryBuilderDialog(self)
        dialog.set_fields_and_rows(
            self.layer_detail.text_fieldnames or self.query_fieldnames,
            self._all_query_rows,
        )
        if self._active_filter_text:
            dialog.set_expression_text(self._active_filter_text)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._active_filter_text = dialog.expression_text().strip()
        self._update_filter_status_label()
        self.query_offset = 0
        self._run_query(0)

    def _on_clear_filter_clicked(self) -> None:
        if not self._active_filter_text:
            return
        self._active_filter_text = ""
        self._update_filter_status_label()
        if self._current_layer_id() is not None and self.layer_detail is not None:
            self.query_offset = 0
            self._run_query(0)

    # ---------------------------------------------------------------- query

    def _page_size(self) -> int:
        try:
            requested = int(self.count_edit.text().strip())
        except ValueError:
            requested = DEFAULT_PAGE_SIZE
        limit = self.layer_detail.max_record_count if self.layer_detail else requested
        return max(1, min(requested, limit))

    def _layer_wkid(self) -> Optional[int]:
        if self.layer_detail and self.layer_detail.wkid:
            return self.layer_detail.wkid
        return self.service.wkid if self.service else None

    def _canvas_bbox(self):
        """The map canvas extent as (xmin, ymin, xmax, ymax) in the layer's
        own coordinate system, or None when the box is unticked or the
        layer's CRS is unknown. ArcGIS envelopes are plain x,y - no axis
        flip for geographic CRSes, unlike an OGC WFS filter."""
        wkid = self._layer_wkid()
        if not self.extent_checkbox.isChecked() or not wkid:
            return None
        canvas = iface.mapCanvas()
        source_crs = canvas.mapSettings().destinationCrs()
        target_crs = QgsCoordinateReferenceSystem(f"EPSG:{wkid}")
        extent = canvas.extent()
        if target_crs.isValid() and target_crs != source_crs:
            transform = QgsCoordinateTransform(
                source_crs, target_crs, QgsProject.instance()
            )
            extent = transform.transformBoundingBox(extent)
        return (
            extent.xMinimum(),
            extent.yMinimum(),
            extent.xMaximum(),
            extent.yMaximum(),
        )

    def _on_query_clicked(self) -> None:
        if self._current_layer_id() is None or self.layer_detail is None:
            QMessageBox.information(
                self,
                self.tr("No layer selected"),
                self.tr("Select a layer first."),
            )
            return
        self.query_offset = 0
        self._run_query(0)

    def _run_query(self, offset: int) -> None:
        gateway = self._current_gateway()
        layer_id = self._current_layer_id()
        detail = self.layer_detail
        if gateway is None or layer_id is None or detail is None:
            return
        where = self._active_filter_text or "1=1"
        bbox = self._canvas_bbox()
        wkid = self._layer_wkid()
        size = self._page_size()

        # Empty the grid first so a query in progress (or a failed one)
        # never leaves the previous rows looking current.
        self._render_results([], [])
        self.page_label.setText(self.tr("Querying..."))
        self._set_status(self.tr("Querying..."))
        try:
            fieldnames, rows, more = arcgis_query_rows(
                gateway.base_url,
                layer_id,
                detail.fields,
                where=where,
                bbox=bbox,
                bbox_wkid=wkid,
                offset=offset,
                count=size,
                fetch=self.fetch,
            )
            if offset == 0:
                try:
                    self.total_count = arcgis_count(
                        gateway.base_url,
                        layer_id,
                        where=where,
                        bbox=bbox,
                        bbox_wkid=wkid,
                        fetch=self.fetch,
                    )
                except Exception:
                    self.total_count = None  # the count is a nicety
        except Exception as e:
            self.page_label.setText(self.tr("Query failed"))
            self._set_status(self.tr("Query failed: {}").format(e))
            QgsMessageLog.logMessage(
                f"ArcGIS REST query failed for layer {layer_id} "
                f"(where={where!r}): {e!r}",
                "SIGate",
                Qgis.MessageLevel.Warning,
            )
            note = (
                self.tr("A filter was active: {}").format(self._active_filter_text)
                if self._active_filter_text
                else self.tr("No filter was active.")
            )
            QMessageBox.warning(
                self,
                self.tr("Query failed"),
                self.tr("{}\n\n{}").format(e, note),
            )
            return
        self.query_offset = offset
        self._more_available = more
        self._render_results(fieldnames, rows)
        self.prev_page_button.setEnabled(offset > 0)
        self.next_page_button.setEnabled(more or len(rows) == size)
        total = (
            self.tr(" of {}").format(self.total_count)
            if self.total_count is not None
            else ""
        )
        self.page_label.setText(
            self.tr("{}-{}{}").format(
                offset + 1 if rows else offset, offset + len(rows), total
            )
        )
        self._set_status(self.tr("{} feature(s) on this page").format(len(rows)))

    def _next_page(self) -> None:
        self._run_query(self.query_offset + self._page_size())

    def _prev_page(self) -> None:
        self._run_query(max(0, self.query_offset - self._page_size()))

    # -------------------------------------------------------------- results

    def _render_results(self, fieldnames: List[str], rows: List[dict]) -> None:
        self.query_fieldnames = list(fieldnames)
        self._all_query_rows = list(rows)
        self.result_filter_edit.blockSignals(True)
        self.result_filter_edit.clear()
        self.result_filter_edit.blockSignals(False)
        self._show_rows(rows)

    def _show_rows(self, rows: List[dict]) -> None:
        self.query_rows = list(rows)
        self.results_tree.clear()
        self.results_tree.setColumnCount(len(self.query_fieldnames))
        self.results_tree.setHeaderLabels(self.query_fieldnames)
        for row in rows:
            values = []
            for name in self.query_fieldnames:
                value = row.get(name, "")
                if value and len(value) > 60:
                    value = value[:57] + "..."
                values.append(value)
            self.results_tree.addTopLevelItem(QTreeWidgetItem(values))

    def _apply_result_filter(self) -> None:
        needle = self.result_filter_edit.text().strip().lower()
        if not needle:
            self._show_rows(self._all_query_rows)
            return
        self._show_rows(
            [
                row
                for row in self._all_query_rows
                if any(needle in str(v).lower() for v in row.values())
            ]
        )

    # ------------------------------------------------------------ add to map

    def _on_add_clicked(self) -> None:
        layer_id = self._current_layer_id()
        gateway = self._current_gateway()
        detail = self.layer_detail
        if layer_id is None or gateway is None or detail is None:
            QMessageBox.information(
                self,
                self.tr("No layer selected"),
                self.tr("Select a layer first."),
            )
            return

        where = self._active_filter_text or None
        layer_name = detail.name.strip()
        selected = [
            self.query_rows[self.results_tree.indexOfTopLevelItem(item)]
            for item in self.results_tree.selectedItems()
            if 0 <= self.results_tree.indexOfTopLevelItem(item) < len(self.query_rows)
        ]
        if selected:
            if detail.object_id_field and all(
                detail.object_id_field in row for row in selected
            ):
                # A selection is already a complete, exact selector.
                where = build_object_id_filter(
                    detail.object_id_field,
                    [row[detail.object_id_field] for row in selected],
                )
                layer_name = self.tr("{} ({} selected)").format(
                    layer_name, len(selected)
                )
            else:
                self._set_status(
                    self.tr(
                        "{} row(s) selected, but the layer's object id column "
                        "is not in the results - added the full filtered "
                        "result instead."
                    ).format(len(selected))
                )

        layer_url = f"{arcgis_service_url(gateway.base_url)}/{layer_id}"
        uri = build_afs_uri(layer_url, detail.epsg, where)
        source = self.connection_manager.current_connection()
        with group_scope(source.display_name):
            emit_in_scope(
                lambda: self.addVectorLayer.emit(
                    uri, layer_name, ARCGIS_FEATURE_PROVIDER_KEY
                )
            )
