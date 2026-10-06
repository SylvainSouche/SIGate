"""
ui.wmts_wms_widget - content of SIGate's WM(T)S Data Source Manager tab: a
connection manager to pick a source, a real, filterable list of the
layers that source's server actually offers (fetched live from its own
GetCapabilities response, not a fixed example), and an action to add the
selected one to the map using QGIS's own native WMS/WMTS provider rather
than any fetching or rendering logic of SIGate's own.

Auth-gated gateway instances (an API-key header, confirmed to exist for
at least one configured source) are included: an authcfg is provisioned
or reused for the gateway via sigate.ui.authcfg, and its id is embedded
in the connection string exactly as QGIS's own authentication
infrastructure expects (see that module's docstring for the full
mechanism and its still-open verification caveats).

A source with more than one wmts_wms gateway instance (confirmed real:
IGN has a separate public and private/apikey-gated one) gets one
selectable connection-manager entry per instance - see
ui.connection_manager's docstring; this module's own part of it is
collect_layer_choices_for_gateway, handling exactly one gateway instance
per call rather than iterating all of a source's instances.

"Export clipped area as GeoTIFF" (gateways.wmts_export, ui.wmts_export_
task/ui.wmts_export_dialog) is a second, genuinely different capability
alongside "Add to map": rather than handing a live connection off to
QGIS's own provider, it materializes a real, standalone GeoTIFF file
clipped to the current map canvas extent. The canvas extent (whatever
CRS the current project happens to use) is reprojected into the
selected layer's own CRS first via QgsCoordinateTransform, since QGIS's
own real transform machinery is available to do this correctly. Runs on
a background QgsTask so the GDAL commands involved don't freeze the UI,
though true cooperative cancellation isn't possible the way it is for a
chunked download - see ui.wmts_export_task's own docstring.
"""

from pathlib import Path
from typing import Optional

from qgis.gui import QgsAbstractDataSourceWidget
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject
from qgis.utils import iface

from sigate.download import pipeline
from sigate.gateways.atom import fetch_with_header
from sigate.gateways.wmts_export import (
    build_gdal_http_headers_config,
    estimate_export_size,
    wmts_def_cache_filename,
)
from sigate.gateways.wmts_wms import (
    build_qgis_wms_uri,
    wms_choose_crs,
    wms_get_capabilities,
    wmts_get_capabilities,
)

from . import settings as sigate_settings
from .authcfg import ensure_authcfg_for_gateway
from .connection_manager import ConnectionManager
from .layer_groups import emit_in_scope, group_scope
from .wmts_export_dialog import run_modal
from .wmts_zoom_level_dialog import choose_zoom_level

# QGIS's native provider key for both WMS and WMTS connections.
WMS_PROVIDER_KEY = "wms"


def _log_exception(context: str, exception: Exception) -> None:
    """Logs full diagnostic detail (the real exception, not just its
    string form) to QGIS's Log Messages panel under the "SIGate" tag."""
    from qgis.core import Qgis, QgsMessageLog

    QgsMessageLog.logMessage(
        f"{context}: {exception!r}", "SIGate", Qgis.MessageLevel.Warning
    )


class LayerChoice:
    """One selectable entry: a real layer discovered from a source's
    GetCapabilities response, paired with enough information to build a
    working connection string for it."""

    def __init__(
        self,
        source_display_name: str,
        base_url: str,
        layer_info,
        tilematrixsets,
        authcfg_id: Optional[str] = None,
    ) -> None:
        self.source_display_name = source_display_name
        self.base_url = base_url
        self.layer_info = layer_info
        self.tilematrixsets = tilematrixsets
        self.authcfg_id = authcfg_id

    @property
    def label(self) -> str:
        return (
            f"{self.layer_info.identifier} - {self.layer_info.title}"
            if self.layer_info.title
            else self.layer_info.identifier
        )

    def effective_crs_and_tilematrixset(self):
        """The tilematrixset id and CRS this layer will actually be used
        with - shared by build_uri (the live "Add to map" handoff) and
        the "Export clipped area" path, so both agree on which CRS a
        clip bbox needs to be expressed in.

        Falls back to EPSG:3857 not only when no tilematrixset is
        found at all, but also when one is found and its declared CRS
        turns out to be unresolvable (QgsCoordinateReferenceSystem(...)
        .isValid() is False) - the real reported bug this guards
        against: gateways.base.urn_to_epsg can decode a URN or a known
        non-standard alias, but can't anticipate every possible bad
        server declaration, and a server-declared CRS string was
        previously trusted all the way into the resulting QGIS
        connection string with no validation at all."""
        tilematrixset_id = self.layer_info.default_tilematrixset
        crs = "EPSG:3857"
        if tilematrixset_id is None and self.layer_info.crs:
            # A plain WMS layer: its own declared coordinate systems.
            return None, wms_choose_crs(
                self.layer_info.crs,
                lambda c: QgsCoordinateReferenceSystem(c).isValid(),
            )
        if tilematrixset_id and tilematrixset_id in self.tilematrixsets:
            declared_crs = self.tilematrixsets[tilematrixset_id].crs
            if QgsCoordinateReferenceSystem(declared_crs).isValid():
                crs = declared_crs
        return tilematrixset_id, crs

    def build_uri(self) -> str:
        tilematrixset_id, crs = self.effective_crs_and_tilematrixset()
        return build_qgis_wms_uri(
            self.base_url,
            layer=self.layer_info.identifier,
            tile_matrix_set=tilematrixset_id,
            style=self.layer_info.default_style,
            crs=crs,
            image_format=self.layer_info.default_format,
            authcfg=self.authcfg_id,
            wms=tilematrixset_id is None and bool(self.layer_info.crs),
        )


def collect_layer_choices_for_gateway(
    source, gateway_config, fetch=None, authcfg_provisioner=None
):
    """Fetches real GetCapabilities for exactly one wmts_wms gateway
    instance and returns every layer actually found on it.

    Auth-gated gateways are provisioned an authcfg (via
    authcfg_provisioner, defaulting to sigate.ui.authcfg's real
    ensure_authcfg_for_gateway) rather than being skipped - if
    provisioning fails, an empty list is returned rather than layers
    offered with no working authentication.
    """
    if source is None or gateway_config is None:
        return []
    if authcfg_provisioner is None:
        authcfg_provisioner = ensure_authcfg_for_gateway

    gateway_role = gateway_config.extra.get("role", "default")
    try:
        authcfg_id = authcfg_provisioner(source.key, gateway_role, gateway_config.auth)
    except Exception as e:
        _log_exception(
            f"Could not provision authcfg for {source.key!r}/{gateway_role!r}", e
        )
        return []

    # The authcfg above only covers the final tile-serving connection
    # QGIS's own provider will make once a layer is added to the map.
    # Listing this gateway's own layers is a separate, earlier request
    # SIGate makes itself (to parse GetCapabilities) - if the gateway
    # requires an apikey header, that header has to be attached here too,
    # or the listing request itself gets rejected and this gateway's
    # layers silently disappear, indistinguishable from an unreachable
    # server. Only applied when the caller hasn't already injected their
    # own fetch (e.g. tests, which don't care about headers at all).
    capabilities_fetch = fetch
    if (
        capabilities_fetch is None
        and gateway_config.auth.kind == "apikey_header"
        and gateway_config.auth.header_name
    ):
        capabilities_fetch = fetch_with_header(
            gateway_config.auth.header_name, gateway_config.auth.value or ""
        )

    try:
        layers, tilematrixsets = [], {}
        # A gateway declared as a plain WMS (extra service=wms) skips the
        # WMTS attempt; an undeclared one tries WMTS first and falls back
        # to WMS when that finds nothing - a user-added connection
        # shouldn't need to say which it is.
        wmts_error = None
        if gateway_config.extra.get("service", "").lower() != "wms":
            try:
                layers, tilematrixsets = wmts_get_capabilities(
                    gateway_config.base_url,
                    fetch=capabilities_fetch,
                    lang=sigate_settings.get_locale(),
                )
            except Exception as e:
                wmts_error = e
        if not layers:
            try:
                layers = wms_get_capabilities(
                    gateway_config.base_url, fetch=capabilities_fetch
                )
            except Exception as wms_error:
                raise wmts_error or wms_error
    except Exception as e:
        _log_exception(
            f"Could not fetch capabilities from {gateway_config.base_url!r}", e
        )
        return []
    return [
        LayerChoice(
            source.display_name,
            gateway_config.base_url,
            layer_info,
            tilematrixsets,
            authcfg_id=authcfg_id,
        )
        for layer_info in layers
    ]


class WmtsWmsSourceSelectWidget(QgsAbstractDataSourceWidget):
    def __init__(
        self,
        parent=None,
        fl=Qt.WindowType(0),
        widget_mode=None,
        fetch=None,
        authcfg_provisioner=None,
    ) -> None:
        # None -> QGIS's own default; see WfsSourceSelectWidget.__init__.
        if widget_mode is None:
            super().__init__(parent, fl)
        else:
            super().__init__(parent, fl, widget_mode)
        self.fetch = fetch
        # Injectable for the same reason `fetch` is: production leaves this
        # None so collect_layer_choices_for_gateway falls back to the real
        # ui.authcfg.ensure_authcfg_for_gateway (a real QGIS auth-database
        # write). Tests should always override this - going through the
        # real QgsAuthManager in a headless/offscreen session with no
        # master password set blocks waiting for interactive input that
        # can never arrive.
        self.authcfg_provisioner = authcfg_provisioner
        self.choices = []
        self._build_ui()
        self._load_initial_connection()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel(self.tr("Connection:")))
        self.connection_manager = ConnectionManager(
            "wmts_wms",
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
        layout.addWidget(self.list_widget)

        self.add_button = QPushButton(self.tr("Add to map"), self)
        self.add_button.clicked.connect(self._on_add_clicked)
        layout.addWidget(self.add_button)

        self.export_button = QPushButton(
            self.tr("Export clipped area as GeoTIFF..."), self
        )
        self.export_button.clicked.connect(self._on_export_clicked)
        layout.addWidget(self.export_button)

        self.status_label = QLabel(self)
        layout.addWidget(self.status_label)

    def _load_initial_connection(self) -> None:
        # ConnectionManager's own constructor already fired connectionChanged
        # once internally, before our slot was connected to it - picked up
        # explicitly here instead, now that the rest of the UI exists.
        self._on_connection_changed(self.connection_manager.current_connection())

    def _on_connection_changed(self, source) -> None:
        self.layer_filter_edit.setText("")
        self.status_label.setText(self.tr("Loading layers..."))
        gateway = self.connection_manager.current_gateway()
        self.choices = collect_layer_choices_for_gateway(
            source,
            gateway,
            fetch=self.fetch,
            authcfg_provisioner=self.authcfg_provisioner,
        )
        self._apply_layer_filter()
        self.status_label.setText(
            self.tr("{} layer(s) found").format(len(self.choices))
        )

    def _apply_layer_filter(self) -> None:
        needle = self.layer_filter_edit.text().strip().lower()
        self.list_widget.clear()
        for choice in self.choices:
            if needle and needle not in choice.label.lower():
                continue
            item = QListWidgetItem(choice.label)
            item.setData(Qt.ItemDataRole.UserRole, choice)
            self.list_widget.addItem(item)

    def _on_add_clicked(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        choice = item.data(Qt.ItemDataRole.UserRole)
        uri = choice.build_uri()
        # The layer's own name/title, not choice.source_display_name
        # (the connection's own display name, e.g. "IGN (France)") -
        # a real reported bug: every layer added from the same
        # connection was landing in the QGIS layers panel under that
        # one shared, generic name, rather than which specific layer
        # it actually was.
        layer_name = choice.layer_info.title or choice.layer_info.identifier
        with group_scope(choice.source_display_name):
            emit_in_scope(
                lambda: self.addRasterLayer.emit(uri, layer_name, WMS_PROVIDER_KEY)
            )

    def _on_export_clicked(self) -> None:
        """Materializes a real, standalone GeoTIFF clipped to the
        current map canvas extent, reprojected into the selected
        layer's own CRS first - a genuinely different capability from
        Add to map's live connection handoff, ported from a standalone
        script (gateways.wmts_export) confirmed working against real
        IGN WMTS endpoints. Runs in the background (ui.wmts_export_task/
        ui.wmts_export_dialog) so the GDAL commands involved don't
        freeze QGIS's UI."""
        item = self.list_widget.currentItem()
        if item is None:
            QMessageBox.information(
                self,
                self.tr("No layer selected"),
                self.tr("Select a layer first."),
            )
            return
        choice = item.data(Qt.ItemDataRole.UserRole)
        tilematrixset_id, crs = choice.effective_crs_and_tilematrixset()
        if tilematrixset_id is None and choice.layer_info.crs:
            # A plain WMS layer: the export works through GDAL's WMTS
            # driver and needs a tile matrix set.
            QMessageBox.information(
                self,
                self.tr("Not available for WMS layers"),
                self.tr(
                    "Export as GeoTIFF needs a tiled (WMTS) layer. "
                    "Use Add to map for this one."
                ),
            )
            return

        target_crs = QgsCoordinateReferenceSystem(crs)
        if not target_crs.isValid():
            QMessageBox.warning(
                self,
                self.tr("Invalid CRS"),
                self.tr("Could not resolve the layer's CRS ({}).").format(crs),
            )
            return

        canvas = iface.mapCanvas()
        source_crs = canvas.mapSettings().destinationCrs()
        transform = QgsCoordinateTransform(
            source_crs, target_crs, QgsProject.instance()
        )
        try:
            bbox_rect = transform.transformBoundingBox(canvas.extent())
        except Exception as e:
            QMessageBox.warning(
                self,
                self.tr("Could not reproject the current view"),
                self.tr(
                    "The current map view could not be reprojected into "
                    "this layer's CRS ({}): {}"
                ).format(crs, e),
            )
            return
        bbox = (
            bbox_rect.xMinimum(),
            bbox_rect.yMinimum(),
            bbox_rect.xMaximum(),
            bbox_rect.yMaximum(),
        )
        # bbox is now in `crs` - passed to run_modal below as both the
        # bbox itself and, explicitly, as bbox_crs (gateways.wmts_export
        # forwards it to GDAL's own --bbox-crs flag) rather than relying
        # on an implicit assumption that this already matches whatever
        # CRS GDAL itself derives for the WMTS dataset - see that
        # module's own docstring for exactly why that distinction
        # matters here.

        # When a layer's tilematrixset declares more than one real zoom
        # level, let the user pick one directly - each shown with its
        # own estimated size for the current view - rather than only
        # ever warning about the single finest level. Picking a coarser
        # level genuinely constrains what GDAL fetches (passed through
        # as its own tilematrix= parameter), not just a smaller estimate
        # followed by downsampling the same full-resolution fetch. A
        # tilematrixset with only one level (or none parseable) falls
        # back to the simpler threshold-only warning, reusing the same
        # size-warning setting already configured for downloads.
        tilematrixset_info = (
            choice.tilematrixsets.get(tilematrixset_id) if tilematrixset_id else None
        )
        chosen_tilematrix: Optional[str] = None
        if tilematrixset_info and len(tilematrixset_info.levels) > 1:
            chosen_tilematrix = choose_zoom_level(self, tilematrixset_info.levels, bbox)
            if chosen_tilematrix is None:
                return
        elif tilematrixset_info and tilematrixset_info.finest_resolution:
            estimate = estimate_export_size(bbox, tilematrixset_info.finest_resolution)
            if pipeline.exceeds_size_warning_threshold(
                estimate.raw_bytes, sigate_settings.get_size_warning_threshold_bytes()
            ):
                size_gb = estimate.raw_bytes / 1024**3
                proceed = QMessageBox.question(
                    self,
                    self.tr("Large export"),
                    self.tr(
                        "The current map view, at this layer's finest "
                        "available resolution, is approximately {} x {} "
                        "pixels (~{:.1f} GB uncompressed). The actual file "
                        "will likely be smaller once compressed, but this "
                        "may take a long time and use significant disk "
                        "space during the clip. Continue?"
                    ).format(estimate.width_px, estimate.height_px, size_gb),
                )
                if proceed != QMessageBox.StandardButton.Yes:
                    return

        out_path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Export clipped area as GeoTIFF"),
            "",
            self.tr("GeoTIFF (*.tif)"),
        )
        if not out_path:
            return
        if not out_path.lower().endswith(".tif"):
            out_path += ".tif"

        gateway = self.connection_manager.current_gateway()
        gdal_config = []
        if gateway.auth.kind == "apikey_header" and gateway.auth.header_name:
            # GDAL's own HTTP-header mechanism, not QGIS's authcfg - this
            # module shells out to GDAL's CLI tools directly rather than
            # going through QGIS's own provider, the same reasoning
            # already applied to this tab's capabilities-listing fetch.
            gdal_config = build_gdal_http_headers_config(
                gateway.auth.header_name, gateway.auth.value or ""
            )

        # Colocated with the output file itself, not the QGIS profile's
        # settings directory - a hidden, out-of-the-way location the
        # user doesn't see or control, unlike tmp_dir's own handling just
        # below, which already puts the scratch clip right next to the
        # output for exactly this reason (guaranteed writable, since the
        # user just chose it as a save location, and visible rather than
        # hidden). This does give up reusing one cached definition across
        # exports saved to different output directories - accepted,
        # since predictability and not writing into a location the user
        # didn't choose matters more for a single interactive export
        # action.
        def_filename = wmts_def_cache_filename(
            choice.layer_info.identifier,
            tilematrixset_id or "",
            choice.layer_info.default_style,
            tilematrix=chosen_tilematrix,
        )
        def_path = str(Path(out_path).parent / def_filename)

        succeeded, exception, was_cancelled = run_modal(
            self,
            def_path=def_path,
            caps_url=gateway.base_url,
            gdal_config=gdal_config,
            max_connections=2,
            layer=choice.layer_info.identifier,
            tilematrixset=tilematrixset_id or "",
            style=choice.layer_info.default_style,
            bbox=bbox,
            bbox_crs=crs,
            tilematrix=chosen_tilematrix,
            out_path=out_path,
            # The scratch clip (JPEG pipeline only) needs somewhere
            # writable - the output file's own directory is guaranteed
            # to be, since the user just chose it as a save location,
            # unlike relying on the process's current working directory
            # or assuming a system temp dir is writable in this specific
            # environment.
            tmp_dir=str(Path(out_path).parent),
        )

        if not succeeded:
            if was_cancelled:
                self.status_label.setText(self.tr("Export cancelled."))
            else:
                QMessageBox.warning(
                    self,
                    self.tr("Export failed"),
                    self.tr("Could not export the clipped area: {}").format(exception),
                )
            return

        self.status_label.setText(self.tr("Exported: {}").format(out_path))
        with group_scope(choice.source_display_name):
            emit_in_scope(
                lambda: self.addRasterLayer.emit(out_path, Path(out_path).stem, "gdal")
            )
