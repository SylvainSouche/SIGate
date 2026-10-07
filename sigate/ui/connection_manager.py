"""
ui.connection_manager - shared "connection manager" widget matching
QGIS's own established convention for connection-based (non-file-based)
providers: a combo box to switch between saved connections, plus
New/Edit/Delete/Load/Save buttons. Used identically across all three
SIGate tabs (WM(T)S, Bulk Listing, WFS), parameterized by which gateway
type each tab cares about.

The combo lists one entry per gateway *instance* of the tab's gateway
type, not one entry per source - a source declaring more than one
instance of the same gateway type (confirmed real: IGN's WM(T)S gateway
has a separate public and private/apikey-gated instance) gets one combo
entry per instance, distinguished by that instance's own `extra["role"]`
when set. A source with exactly one gateway of the tab's type (true for
every seeded WFS and Bulk Listing source today) gets exactly one entry,
plain display name.

Backed directly by sources.store's SourceConfig persistence: "New"/"Edit"
write to the user's own JSON override file via add_or_replace_source;
"Delete" removes an entry from that same file. Only user-added
connections can be deleted this way - bundled seed connections (e.g. the
built-in IGN entry) aren't stored in the user's override file at all, so
there's nothing there to remove; the Delete button is disabled for those.
"Load"/"Save" import/export a single connection as a standalone JSON
file, reusing the same serialization sources.store already has. These
five operations all act at the SourceConfig level (each still creates,
edits, or removes exactly one gateway instance via
ConnectionEditDialog.get_source_config(), same as before) - only the
combo's *listing* is gateway-instance-granular, not connection
management itself.

Three combos narrow the list: country, then organisation, then the
connection itself. The first two offer "All ..." or one value, drawn only
from what has at least one connection for this tab's gateway type(s)
(an organisation is listed under the country it is in). The choices are
remembered across tabs and sessions (settings.get_last_country_filter /
get_last_organisation_filter) and fall back to "all" when a tab has
nothing for them. With an organisation picked, entry names drop its
prefix ("Regione Piemonte - base cartography" shows as "base
cartography"); with "All organisations" they keep it.

The New/Edit dialog exposes generic fields (key, display name, country,
base URL, a basic authentication type) plus a free-form key=value text
area for anything gateway-specific. A fully gateway-type-aware form
(e.g. asking specifically for an example layer/style/tile matrix set
when editing a WMTS connection, rather than a generic text box) would be
a larger undertaking than this pass covers.
"""

import json
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple, Union

from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from sigate.sources.countries import canonical_country
from sigate.sources.organisations import (
    organisation_key,
    organisation_of,
    short_label,
)
from sigate.sources.seed import all_seed_sources
from sigate.ui import settings as sigate_settings
from sigate.sources.store import (
    AuthConfig,
    GatewayConfig,
    SourceConfig,
    add_or_replace_source,
    load_user_overrides,
    merged_sources,
    save_user_overrides,
)


def _parse_extra_text(text: str) -> dict:
    extra = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        extra[key.strip()] = value.strip()
    return extra


def _format_extra_text(extra: dict) -> str:
    return "\n".join(f"{key}={value}" for key, value in extra.items())


class ConnectionEditDialog(QDialog):
    """New/Edit dialog for a single-gateway-type SourceConfig."""

    def __init__(
        self, gateway_type: str, existing: Optional[SourceConfig] = None, parent=None
    ) -> None:
        super().__init__(parent)
        self.gateway_type = gateway_type
        self.setWindowTitle(
            self.tr("Edit connection") if existing else self.tr("New connection")
        )
        self._build_ui()
        if existing:
            self._load_existing(existing)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.key_edit = QLineEdit(self)
        form.addRow(self.tr("Key (unique identifier):"), self.key_edit)
        self.name_edit = QLineEdit(self)
        form.addRow(self.tr("Display name:"), self.name_edit)
        self.country_edit = QLineEdit(self)
        form.addRow(self.tr("Country:"), self.country_edit)
        self.organisation_edit = QLineEdit(self)
        self.organisation_edit.setPlaceholderText(
            self.tr("optional - taken from the display name when empty")
        )
        form.addRow(self.tr("Organisation:"), self.organisation_edit)
        self.url_edit = QLineEdit(self)
        form.addRow(self.tr("Base URL:"), self.url_edit)

        self.auth_combo = QComboBox(self)
        self.auth_combo.addItems(["none", "apikey_header", "token"])
        form.addRow(self.tr("Authentication:"), self.auth_combo)
        self.auth_header_edit = QLineEdit(self)
        form.addRow(self.tr("Header name (if applicable):"), self.auth_header_edit)
        self.auth_value_edit = QLineEdit(self)
        self.auth_value_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(self.tr("Header/token value:"), self.auth_value_edit)

        layout.addLayout(form)

        layout.addWidget(
            QLabel(self.tr("Additional settings (one key=value per line):"))
        )
        self.extra_edit = QPlainTextEdit(self)
        layout.addWidget(self.extra_edit)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _load_existing(self, source: SourceConfig) -> None:
        self.key_edit.setText(source.key)
        self.key_edit.setEnabled(
            False
        )  # identity field - not editable once a connection exists
        self.name_edit.setText(source.display_name)
        self.country_edit.setText(source.country)
        self.organisation_edit.setText(source.organisation)
        gateway = source.gateway(self.gateway_type)
        if gateway:
            self.url_edit.setText(gateway.base_url)
            self.auth_combo.setCurrentText(gateway.auth.kind)
            self.auth_header_edit.setText(gateway.auth.header_name or "")
            self.auth_value_edit.setText(gateway.auth.value or "")
            self.extra_edit.setPlainText(_format_extra_text(gateway.extra))

    def validation_error(self) -> Optional[str]:
        if not self.key_edit.text().strip():
            return self.tr("A key is required.")
        if not self.name_edit.text().strip():
            return self.tr("A display name is required.")
        if not self.url_edit.text().strip():
            return self.tr("A base URL is required.")
        return None

    def _on_accept(self) -> None:
        error = self.validation_error()
        if error:
            QMessageBox.warning(self, self.tr("Invalid connection"), error)
            return
        self.accept()

    def get_source_config(self) -> SourceConfig:
        auth = AuthConfig(
            kind=self.auth_combo.currentText(),
            header_name=self.auth_header_edit.text().strip() or None,
            value=self.auth_value_edit.text().strip() or None,
        )
        gateway = GatewayConfig(
            gateway_type=self.gateway_type,
            base_url=self.url_edit.text().strip(),
            auth=auth,
            extra=_parse_extra_text(self.extra_edit.toPlainText()),
        )
        return SourceConfig(
            key=self.key_edit.text().strip(),
            display_name=self.name_edit.text().strip(),
            country=self.country_edit.text().strip(),
            gateways=[gateway],
            organisation=self.organisation_edit.text().strip(),
        )


class ConnectionManager(QWidget):
    """Combo box + New/Edit/Delete/Load/Save, for a single gateway type.

    `data_dir_provider` is a zero-argument callable returning the Path to
    use for user-added connection overrides - injected rather than
    imported directly from ui.settings, so this widget (and anything
    testing it) doesn't need a real QGIS profile path; a test can supply
    a plain tmp_path instead.

    Emits `connectionChanged` (with the selected SourceConfig, or None if
    nothing is selected) whenever the active connection changes - on
    initial load, after New/Edit/Delete, or when the user picks a
    different entry from the combo - so an owning tab can react (e.g.
    reload its listing against the newly-selected connection).
    """

    connectionChanged = pyqtSignal(object)

    def __init__(
        self,
        gateway_type: Union[str, Sequence[str]],
        data_dir_provider: Callable[[], Path],
        parent=None,
    ) -> None:
        super().__init__(parent)
        # Accepts either one gateway type (every existing WFS/WMTS-tab
        # call site) or a sequence of types (needed for Bulk Listing,
        # which now spans both atom and stac gateways in the same
        # combo) - normalized to a list either way so
        # _all_connection_choices only has one code path to maintain.
        self.gateway_types: List[str] = (
            [gateway_type] if isinstance(gateway_type, str) else list(gateway_type)
        )
        self.data_dir_provider = data_dir_provider
        self._build_ui()
        self.reload_connections()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        layout = QHBoxLayout()
        outer.addLayout(layout)

        self.country_combo = QComboBox(self)
        self.country_combo.setToolTip(
            self.tr("Show only the connections of one country")
        )
        self.country_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.country_combo.currentIndexChanged.connect(self._on_country_changed)
        layout.addWidget(self.country_combo)

        self.organisation_combo = QComboBox(self)
        self.organisation_combo.setToolTip(
            self.tr("Show only the connections of one organisation")
        )
        self.organisation_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.organisation_combo.currentIndexChanged.connect(
            self._on_organisation_changed
        )
        layout.addWidget(self.organisation_combo)

        self.combo = QComboBox(self)
        self.combo.currentIndexChanged.connect(self._on_combo_changed)
        layout.addWidget(self.combo, 1)

        layout = QHBoxLayout()
        outer.addLayout(layout)
        layout.addStretch()

        self.new_button = QPushButton(self.tr("New..."), self)
        self.new_button.clicked.connect(self._on_new)
        layout.addWidget(self.new_button)

        self.edit_button = QPushButton(self.tr("Edit..."), self)
        self.edit_button.clicked.connect(self._on_edit)
        layout.addWidget(self.edit_button)

        self.delete_button = QPushButton(self.tr("Delete"), self)
        self.delete_button.clicked.connect(self._on_delete)
        layout.addWidget(self.delete_button)

        self.load_button = QPushButton(self.tr("Load..."), self)
        self.load_button.clicked.connect(self._on_load)
        layout.addWidget(self.load_button)

        self.save_button = QPushButton(self.tr("Save..."), self)
        self.save_button.clicked.connect(self._on_save)
        layout.addWidget(self.save_button)

    def _all_connection_choices(self) -> List[Tuple[SourceConfig, GatewayConfig]]:
        """One (source, gateway) pair per gateway instance of any of
        this tab's allowed type(s), across every seeded and user-added
        source - not one per source. A source with N instances of an
        allowed gateway type contributes N pairs, all sharing the same
        source. Order is: for each source, its gateways in declaration
        order, restricted to allowed types - not grouped by type
        first - so a source's own gateway declaration order (e.g. IGN's
        atom-then-wfs-then-wmts ordering in sources/seed.py) is what
        determines combo order, not which gateway_types entry happened
        to be listed first."""
        sources = merged_sources(all_seed_sources(), self.data_dir_provider())
        choices = []
        for source in sources:
            for gateway in source.gateways:
                if gateway.gateway_type in self.gateway_types:
                    choices.append((source, gateway))
        return choices

    def _all_connections(self) -> List[SourceConfig]:
        """Every distinct source contributing at least one gateway of
        this tab's type - used by operations that act at the source
        level (key-uniqueness checks for New; nothing here needs
        gateway-instance granularity)."""
        seen = {}
        for source, _gateway in self._all_connection_choices():
            seen.setdefault(source.key, source)
        return list(seen.values())

    def _is_user_added(self, key: str) -> bool:
        overrides = load_user_overrides(self.data_dir_provider())
        return any(o.key == key for o in overrides)

    def _label_for(
        self, source: SourceConfig, gateway: GatewayConfig, choices, short: bool = False
    ) -> str:
        """Plain display name when this source contributes only one
        gateway of this type to the combo (the common case, and the
        only case for every seeded WFS/Bulk Listing source today) -
        otherwise disambiguated with the gateway's own role, so a source
        with multiple instances (confirmed real: IGN's public vs
        apikey-gated WM(T)S) never collapses into one indistinguishable
        entry the way it did before this was fixed. `short` is used once
        an organisation is picked: the organisation prefix and the
        trailing country are dropped (sources.organisations.short_label)."""
        shared = sum(1 for s, _g in choices if s.key == source.key) > 1
        role = gateway.extra.get("role")
        if short:
            return short_label(
                source, role or (gateway.base_url if shared else None), shared
            )
        if not shared:
            return source.display_name
        label = role if role else gateway.base_url
        return f"{source.display_name} \u2014 {label}"

    def _country_of(self, source: SourceConfig) -> str:
        """Grouping key for the country combo: one English name whether
        the connection says "NO", "no", "Norge" or "Norway"."""
        return canonical_country(source.country)

    def _in_country(self, choices, country: Optional[str]):
        return [
            (s, g)
            for s, g in choices
            if country is None or self._country_of(s) == country
        ]

    def _in_organisation(self, choices, organisation: Optional[str]):
        return [
            (s, g)
            for s, g in choices
            if organisation is None
            or organisation_key(organisation_of(s)) == organisation
        ]

    def _populate_countries(self, choices, selected: Optional[str]) -> Optional[str]:
        """Fills the country combo from `choices` (all countries first)
        and returns the country actually selected - `selected` when this
        tab has connections for it, otherwise None (all countries)."""
        countries = sorted({self._country_of(s) for s, _g in choices}, key=str.casefold)
        if selected not in countries:
            selected = None
        self.country_combo.blockSignals(True)
        self.country_combo.clear()
        self.country_combo.addItem(self.tr("All countries"), None)
        for country in countries:
            self.country_combo.addItem(country or self.tr("(no country)"), country)
        index = 0 if selected is None else countries.index(selected) + 1
        self.country_combo.setCurrentIndex(index)
        self.country_combo.blockSignals(False)
        return selected

    def _populate_organisations(
        self, choices, selected: Optional[str]
    ) -> Optional[str]:
        """Fills the organisation combo from `choices` (already narrowed
        to the picked country). Items carry a case-insensitive key, text
        is the first spelling seen. Returns the key actually selected,
        None meaning all organisations."""
        names = {}
        for source, _g in choices:
            name = organisation_of(source)
            names.setdefault(organisation_key(name), name)
        ordered = sorted(names.items(), key=lambda kv: kv[1].casefold())
        if selected not in names:
            selected = None
        self.organisation_combo.blockSignals(True)
        self.organisation_combo.clear()
        self.organisation_combo.addItem(self.tr("All organisations"), None)
        for key, name in ordered:
            self.organisation_combo.addItem(name, key)
        keys = [key for key, _n in ordered]
        index = 0 if selected is None else keys.index(selected) + 1
        self.organisation_combo.setCurrentIndex(index)
        self.organisation_combo.blockSignals(False)
        return selected

    def _fill_connections(
        self,
        choices,
        country: Optional[str],
        organisation: Optional[str] = None,
        select_key: Optional[str] = None,
        select_role: Optional[str] = None,
        select_base_url: Optional[str] = None,
    ) -> None:
        """Fills the connection combo with `choices` restricted to
        `country` and `organisation` (None = all), selecting the
        requested entry when it is among them, else the first. Emits
        nothing."""
        shown = self._in_organisation(self._in_country(choices, country), organisation)
        self.combo.blockSignals(True)
        self.combo.clear()
        select_index = 0
        for i, (source, gateway) in enumerate(shown):
            self.combo.addItem(
                self._label_for(source, gateway, shown, short=organisation is not None),
                (source, gateway),
            )
            if select_base_url is not None:
                if source.key == select_key and gateway.base_url == select_base_url:
                    select_index = i
            elif select_key and source.key == select_key:
                if select_role is None or gateway.extra.get("role") == select_role:
                    select_index = i
        if shown:
            self.combo.setCurrentIndex(select_index)
        self.combo.blockSignals(False)

    def reload_connections(
        self, select_key: Optional[str] = None, select_role: Optional[str] = None
    ) -> None:
        choices = self._all_connection_choices()
        # Keep the country and organisation the user is looking at,
        # unless the connection being selected (just added, edited or
        # loaded) lives elsewhere.
        if self.country_combo.count():
            country = self.country_combo.currentData()
            organisation = self.organisation_combo.currentData()
        else:
            country = sigate_settings.get_last_country_filter()
            organisation = sigate_settings.get_last_organisation_filter()
        if select_key:
            for source, _gateway in choices:
                if source.key != select_key:
                    continue
                if country not in (None, self._country_of(source)):
                    country = None
                if organisation not in (
                    None,
                    organisation_key(organisation_of(source)),
                ):
                    organisation = None
                break
        country = self._populate_countries(choices, country)
        in_country = self._in_country(choices, country)
        organisation = self._populate_organisations(in_country, organisation)
        self._fill_connections(choices, country, organisation, select_key, select_role)
        self._update_button_state()
        self._emit_current()

    def _identity(self):
        current = self.combo.currentData()
        return (current[0].key, current[1].base_url) if current else None

    def _refill_keeping_selection(self, country, organisation) -> None:
        before = self._identity()
        self._fill_connections(
            self._all_connection_choices(),
            country,
            organisation,
            select_key=before[0] if before else None,
            select_base_url=before[1] if before else None,
        )
        self._update_button_state()
        if self._identity() != before:
            self._emit_current()

    def _on_country_changed(self, _index: int) -> None:
        country = self.country_combo.currentData()
        sigate_settings.set_last_country_filter(country)
        # An organisation belongs to the countries it is in: keep it if
        # it is still offered, else back to all.
        organisation = self._populate_organisations(
            self._in_country(self._all_connection_choices(), country),
            self.organisation_combo.currentData(),
        )
        sigate_settings.set_last_organisation_filter(organisation)
        self._refill_keeping_selection(country, organisation)

    def _on_organisation_changed(self, _index: int) -> None:
        organisation = self.organisation_combo.currentData()
        sigate_settings.set_last_organisation_filter(organisation)
        self._refill_keeping_selection(self.country_combo.currentData(), organisation)

    def _update_button_state(self) -> None:
        current = self.current_connection()
        has_selection = current is not None
        self.edit_button.setEnabled(has_selection)
        self.save_button.setEnabled(has_selection)
        self.delete_button.setEnabled(
            has_selection and self._is_user_added(current.key) if current else False
        )

    def current_connection(self) -> Optional[SourceConfig]:
        current = self.combo.currentData()
        return current[0] if current else None

    def current_gateway(self) -> Optional[GatewayConfig]:
        """The specific gateway instance backing the selected combo
        entry - not source.gateway(self.gateway_type), which would
        silently return only the first-declared instance regardless of
        which entry is actually selected."""
        current = self.combo.currentData()
        return current[1] if current else None

    def _emit_current(self) -> None:
        self.connectionChanged.emit(self.current_connection())

    def _on_combo_changed(self, _index: int) -> None:
        self._update_button_state()
        self._emit_current()

    def _on_new(self) -> None:
        gateway_type = self.gateway_types[0]
        if len(self.gateway_types) > 1:
            from qgis.PyQt.QtWidgets import QInputDialog

            gateway_type, ok = QInputDialog.getItem(
                self,
                self.tr("New connection"),
                self.tr("Gateway type:"),
                self.gateway_types,
                0,
                False,
            )
            if not ok:
                return
        dialog = ConnectionEditDialog(gateway_type, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_source = dialog.get_source_config()
            if any(s.key == new_source.key for s in self._all_connections()):
                QMessageBox.warning(
                    self,
                    self.tr("Key already in use"),
                    self.tr("A connection with this key already exists."),
                )
                return
            add_or_replace_source(self.data_dir_provider(), new_source)
            self.reload_connections(select_key=new_source.key)

    def _on_edit(self) -> None:
        current = self.current_connection()
        if current is None:
            return
        if not self._is_user_added(current.key):
            QMessageBox.information(
                self,
                self.tr("Built-in connection"),
                self.tr(
                    "This is a built-in connection and can't be edited directly. Use New to add your own instead."
                ),
            )
            return
        # The actually-selected entry's own type, not a single fixed
        # self.gateway_type (removed - a tab can now allow more than
        # one type, e.g. Bulk Listing spanning atom and stac) - matches
        # current_gateway()'s own docstring reasoning: picking the
        # wrong gateway instance out of several on the same source
        # would silently edit the wrong one.
        current_gw = self.current_gateway()
        gateway_type = current_gw.gateway_type if current_gw else self.gateway_types[0]
        dialog = ConnectionEditDialog(gateway_type, existing=current, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            updated = dialog.get_source_config()
            add_or_replace_source(self.data_dir_provider(), updated)
            self.reload_connections(select_key=updated.key)

    def _on_delete(self) -> None:
        current = self.current_connection()
        if current is None or not self._is_user_added(current.key):
            return
        proceed = QMessageBox.question(
            self,
            self.tr("Delete connection"),
            self.tr('Delete connection "{}"?').format(current.display_name),
        )
        if proceed != QMessageBox.StandardButton.Yes:
            return
        remaining = [
            o
            for o in load_user_overrides(self.data_dir_provider())
            if o.key != current.key
        ]
        save_user_overrides(self.data_dir_provider(), remaining)
        self.reload_connections()

    def _on_load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("Load connection"), filter="JSON (*.json)"
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            gateways = [
                GatewayConfig(
                    gateway_type=gw["gateway_type"],
                    base_url=gw["base_url"],
                    auth=AuthConfig(**gw.get("auth", {})),
                    extra=dict(gw.get("extra", {})),
                )
                for gw in data.get("gateways", [])
            ]
            loaded = SourceConfig(
                key=data["key"],
                display_name=data["display_name"],
                country=data["country"],
                gateways=gateways,
                organisation=data.get("organisation", ""),
            )
        except Exception as e:
            QMessageBox.warning(self, self.tr("Load failed"), str(e))
            from qgis.core import Qgis, QgsMessageLog

            QgsMessageLog.logMessage(
                f"Failed to load connection from {path!r}: {e!r}",
                "SIGate",
                Qgis.MessageLevel.Warning,
            )
            return
        add_or_replace_source(self.data_dir_provider(), loaded)
        self.reload_connections(select_key=loaded.key)

    def _on_save(self) -> None:
        current = self.current_connection()
        if current is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Save connection"),
            f"{current.key}.json",
            filter="JSON (*.json)",
        )
        if not path:
            return
        from dataclasses import asdict

        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(current), f, indent=2, ensure_ascii=False)
