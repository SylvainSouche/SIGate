"""
ui.expression_builder - a custom filter-expression builder widget, built
from scratch after confirming no existing QGIS widget was a genuine fit:
QgsExpressionBuilderWidget produces QGIS-expression syntax (a different
language from the CQL our own direct queries need); QgsQueryBuilder and
the real QGIS-native "SQL Query Composer" used for WFS's own "Build
Query" button (QgsSQLComposerDialog) both require - or in the latter
case, are not even exposed to Python at all.

Layout: two sections.
  - Top: a Fields list (real layer fields only) and, alongside it, a
    filterable Values list, populated on demand via "Load some"/"Load
    all" buttons for whichever single field is selected.
  - Bottom: a SQL expression editor (QgsCodeEditorSQL - a real,
    QScintilla-based widget with working SQL syntax highlighting and
    autocompletion already built in) where the actual WHERE-clause
    expression is written directly, in SQLite syntax.

Two special tokens are supported as plain literal text substitutions,
not real QGIS expression evaluation (confirmed necessary: QGIS's own
`@map_extent` variable is documented as unavailable inside a comparable
SQL execution context, so relying on QGIS's built-in variable
resolution here would be unconfirmed at best):
  - `$geom` - substituted with the actual geometry field name.
  - `@map_extent` - substituted with the current map canvas extent.

Sample/all value loading requires a real QgsVectorLayer (confirmed:
QgsExpressionBuilderWidget.loadFieldsAndValues() has been a documented
no-op since QGIS 3.14 - the real mechanism is QgsVectorLayer.uniqueValues()
against an actual layer). Since WFS query rows are plain dicts, not a
real layer, an in-memory ("memory" provider, geometry type "None" since
only attribute values are needed here) layer is built from whatever rows
are currently available.
"""

import re
from typing import Callable, List, Optional

from qgis.core import QgsFeature, QgsField, QgsVectorLayer
from qgis.gui import QgsCodeEditorSQL
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

GEOM_TOKEN = "$geom"
MAP_EXTENT_TOKEN = "@map_extent"

# Sample size for "Load some", as opposed to "Load all" (no limit).
DEFAULT_SAMPLE_SIZE = 25

# A starting set of spatial predicate functions, registered as extra
# keywords in the SQL editor for highlighting/autocompletion. Confirmed,
# after a real reported failure ("ST_Within($geom,@map_extent) error 400
# bad request"), that neither of this expression's two real targets uses
# SQLite/SpatiaLite/PostGIS-style "ST_"-prefixed function names: GeoServer's
# CQL_FILTER uses bare WITHIN/INTERSECTS/... (confirmed against GeoServer's
# own ECQL documentation), and QGIS's own native expression engine uses
# bare, lowercase within()/intersects()/... (confirmed against QGIS's own
# expression function reference) - "ST_Within" is valid in neither
# language, despite looking like ordinary SQL. Named here without the
# prefix specifically so the editor's own suggestions are something that
# actually works, not just something that looks like standard SQL.
DEFAULT_SPATIAL_FUNCTIONS = [
    "WITHIN",
    "INTERSECTS",
    "CONTAINS",
    "DWITHIN",
    "BBOX",
    "OVERLAPS",
    "TOUCHES",
    "DISJOINT",
]


def _string_field(name: str) -> QgsField:
    """Builds a String-typed QgsField without QGIS 3.38+'s deprecation
    warning (QgsField(name, QVariant.String) - QGIS switched to a
    QMetaType.Type-based constructor overload in 3.38, per
    https://api.qgis.org/api/deprecated.html). That overload may not
    exist on every QGIS the plugin could be run on (qgisMinimumVersion is
    3.40 in metadata.txt, which has it, but the fallback costs nothing) -
    tries the new constructor first and falls back to the old one on any
    failure (an old QGIS with no such overload raises at the QgsField(...)
    call itself, not just a warning) rather than branching on a QGIS
    version string, which would need updating every time support range
    changes."""
    try:
        from qgis.PyQt.QtCore import QMetaType

        return QgsField(name, QMetaType.Type.QString)
    except Exception:
        return QgsField(name, QVariant.String)


def build_memory_layer_from_rows(
    fieldnames: List[str],
    rows: List[dict],
    layer_name: str = "expression_builder_sample",
):
    """Builds a real, in-memory QgsVectorLayer from plain dict rows, so
    QgsVectorLayer.uniqueValues() - the confirmed, current mechanism for
    sample/all value loading - has something real to operate on. No
    geometry is attached (geometry type "None"): only attribute values
    are needed here, not spatial data.

    Fields are added via the provider API (QgsField/addAttributes)
    rather than encoded into the memory provider's own connection-string
    syntax ("field=NAME:TYPE&field=..."), which has no escaping
    mechanism for characters that syntax itself uses as delimiters ('&',
    '=') - a real WFS field/attribute name containing either would
    silently corrupt the string, leaving the resulting layer's actual
    field names mismatched against `fieldnames`, and later value-loading
    lookups (fields().lookupField(name)) failing to find the field at
    all. Adding fields via the API sidesteps the connection-string
    escaping question entirely rather than attempting to sanitize or
    escape names into it."""
    layer = QgsVectorLayer("None", layer_name, "memory")
    provider = layer.dataProvider()
    provider.addAttributes([_string_field(name) for name in fieldnames])
    layer.updateFields()
    features = []
    for row in rows:
        feature = QgsFeature()
        feature.setAttributes([row.get(name, "") for name in fieldnames])
        features.append(feature)
    if features:
        provider.addFeatures(features)
    layer.updateExtents()
    return layer


# Maps common SQL/PostGIS-style "ST_"-prefixed spatial predicate names
# to CQL_FILTER's own bare names - added after a *second* real reported
# 400 Bad Request from this exact class of mistake ("st_intersect(...)"),
# following the first one ("ST_Within(...)") that DEFAULT_SPATIAL_FUNCTIONS'
# own autocomplete list above was meant to steer people away from.
# Autocomplete hints alone didn't stop a second person from typing a
# SQL-style name anyway - most people reaching for spatial-filter syntax
# already know PostGIS's ST_-prefixed names from elsewhere and reach for
# them out of habit, so normalizing the mistake at the point of use,
# not just hinting away from it at the point of typing, is the fix that
# actually holds. Restricted to exactly the predicate names this module
# already confirms real (DEFAULT_SPATIAL_FUNCTIONS above) - not
# extended with additional PostGIS names (e.g. ST_Crosses, ST_Equals)
# that haven't been independently confirmed as real CQL_FILTER
# functions for this project's actual sources.
_ST_PREFIXED_ALIASES = {
    "st_within": "WITHIN",
    "st_intersects": "INTERSECTS",
    "st_intersect": "INTERSECTS",  # not a real PostGIS name itself, but an easy, unambiguous typo of st_intersects
    "st_contains": "CONTAINS",
    "st_dwithin": "DWITHIN",
    "st_overlaps": "OVERLAPS",
    "st_touches": "TOUCHES",
    "st_disjoint": "DISJOINT",
}
_ST_PREFIXED_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _ST_PREFIXED_ALIASES) + r")\s*\(",
    re.IGNORECASE,
)


def normalize_spatial_function_names(expression_text: str) -> str:
    """Rewrites common SQL/PostGIS-style ST_-prefixed spatial predicate
    names (ST_Intersects, st_within, ...) to CQL_FILTER's own bare names
    (INTERSECTS, WITHIN, ...) - see _ST_PREFIXED_ALIASES's own comment
    above for why this exists as an active rewrite rather than only an
    autocomplete hint. Matches case-insensitively (SQL conventionally
    isn't case-sensitive for function names) and only where the name is
    immediately followed by an opening parenthesis (a real function
    call), so this won't misfire mid-identifier (e.g.
    "field_st_intersects_something") the way a bare substring match
    would. Not a real CQL/ECQL tokenizer, though: a quoted string
    literal value that happens to itself contain e.g. "st_intersect("
    would still be rewritten, since this only looks at surrounding
    characters, not string-literal context. Accepted as a real but
    vanishingly unlikely edge case for what this expression language is
    actually used for, rather than justifying a full parser for it."""

    def _replace(match: "re.Match") -> str:
        alias = match.group(1).lower()
        return _ST_PREFIXED_ALIASES[alias] + "("

    return _ST_PREFIXED_PATTERN.sub(_replace, expression_text)


def substitute_tokens(
    expression_text: str, geometry_field_name: str, map_extent_wkt: Optional[str]
) -> str:
    """Prepares a raw, user-typed expression for actual use: replaces
    the two special literal tokens with real values (plain text
    substitution, not real QGIS expression evaluation), and normalizes
    any SQL/PostGIS-style ST_-prefixed spatial predicate name to
    CQL_FILTER's own bare form (see normalize_spatial_function_names).
    Applied at the point an expression is actually used (sent as a
    query, or handed to QGIS's native WFS provider), not stored
    pre-substituted, so the editor always displays what the user
    actually typed.

    map_extent_wkt is substituted unquoted - confirmed, after the same
    real reported failure that fixed DEFAULT_SPATIAL_FUNCTIONS above,
    that CQL's own spatial predicate functions take a bare geometry
    literal directly (WITHIN(geom, POLYGON((...))), not a quoted string
    (WITHIN(geom, 'POLYGON((...))') was a real, confirmed 400 Bad
    Request - CQL's parser expects a geometry there, not a string, and a
    quoted value is the wrong type entirely, not just wrong syntax)."""
    result = expression_text.replace(GEOM_TOKEN, geometry_field_name)
    if map_extent_wkt is not None:
        result = result.replace(MAP_EXTENT_TOKEN, map_extent_wkt)
    return normalize_spatial_function_names(result)


def substitute_tokens_for_qgis(
    expression_text: str, map_extent_wkt: Optional[str]
) -> str:
    """The counterpart of substitute_tokens for the "Add to map" path,
    where the filter is handed to QGIS's own native WFS provider, which
    parses a QGIS *expression*, not CQL.

    Confirmed live against IGN's LiDAR HD metadata layer: given SIGate's
    CQL text (WITHIN(geom, POLYGON((...)))) the provider silently drops
    the filter and loads every feature, with no error; given the same
    predicate as a QGIS expression it returns exactly what the server's
    own CQL query does (102 features WITHIN, 151 INTERSECTS for the same
    extent). So here $geom becomes $geometry (QGIS's geometry reference,
    not a server-side field name) and @map_extent becomes
    geom_from_wkt('...') - a function call over a quoted string, where
    CQL wants a bare literal. The spatial function names themselves
    (within, intersects, contains, ...) are identical in both languages;
    ST_-prefixed aliases are normalized the same way. map_extent_wkt must
    be in the layer's CRS with QGIS's own x,y order - not the
    authority-order swap the CQL path applies for geographic CRSes."""
    result = expression_text.replace(GEOM_TOKEN, "$geometry")
    if map_extent_wkt is not None:
        result = result.replace(MAP_EXTENT_TOKEN, f"geom_from_wkt('{map_extent_wkt}')")
    return normalize_spatial_function_names(result)


class ExpressionBuilderWidget(QWidget):
    """The composite fields/values/expression-editor widget."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        get_canvas_extent_wkt: Optional[Callable[[], str]] = None,
    ) -> None:
        super().__init__(parent)
        # Injected rather than importing iface/canvas directly, keeping
        # this widget testable without a real map canvas - a test can
        # supply a fixed extent; the real plugin supplies the live one.
        self.get_canvas_extent_wkt = get_canvas_extent_wkt
        self.fieldnames: List[str] = []
        self.geometry_field_name: str = "geom"
        self._memory_layer: Optional[QgsVectorLayer] = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        top_row = QHBoxLayout()

        fields_col = QVBoxLayout()
        fields_col.addWidget(QLabel(self.tr("Fields")))
        self.fields_list = QListWidget(self)
        self.fields_list.itemDoubleClicked.connect(self._on_field_double_clicked)
        self.fields_list.currentItemChanged.connect(self._on_field_selection_changed)
        fields_col.addWidget(self.fields_list)
        top_row.addLayout(fields_col)

        values_col = QVBoxLayout()
        values_col.addWidget(QLabel(self.tr("Values")))
        self.value_filter_edit = QLineEdit(self)
        self.value_filter_edit.setPlaceholderText(self.tr("Filter values..."))
        self.value_filter_edit.textChanged.connect(self._apply_value_filter)
        values_col.addWidget(self.value_filter_edit)
        self.values_list = QListWidget(self)
        self.values_list.itemDoubleClicked.connect(self._on_value_double_clicked)
        values_col.addWidget(self.values_list)
        self.values_status_label = QLabel(self)
        values_col.addWidget(self.values_status_label)
        load_buttons_row = QHBoxLayout()
        self.load_some_button = QPushButton(self.tr("Load some"), self)
        self.load_some_button.setEnabled(False)
        self.load_some_button.clicked.connect(self._load_sample_values)
        load_buttons_row.addWidget(self.load_some_button)
        self.load_all_button = QPushButton(self.tr("Load all"), self)
        self.load_all_button.setEnabled(False)
        self.load_all_button.clicked.connect(self._load_all_values)
        load_buttons_row.addWidget(self.load_all_button)
        values_col.addLayout(load_buttons_row)
        top_row.addLayout(values_col)

        layout.addLayout(top_row)

        token_row = QHBoxLayout()
        token_row.addWidget(QLabel(self.tr("Insert:")))
        geom_token_button = QPushButton(GEOM_TOKEN, self)
        geom_token_button.clicked.connect(lambda: self._insert_text(GEOM_TOKEN))
        token_row.addWidget(geom_token_button)
        extent_token_button = QPushButton(MAP_EXTENT_TOKEN, self)
        extent_token_button.clicked.connect(lambda: self._insert_text(MAP_EXTENT_TOKEN))
        token_row.addWidget(extent_token_button)
        token_row.addWidget(
            QLabel(
                self.tr(
                    "(both are substituted with real values when the expression is used)"
                )
            )
        )
        layout.addLayout(token_row)

        layout.addWidget(QLabel(self.tr("Expression (SQLite syntax):")))
        self.sql_editor = QgsCodeEditorSQL(self)
        self.sql_editor.setExtraKeywords(DEFAULT_SPATIAL_FUNCTIONS)
        layout.addWidget(self.sql_editor)

    # --------------------------------------------------------------- population

    def set_fields_and_rows(
        self, fieldnames: List[str], rows: List[dict], geometry_field_name: str = "geom"
    ) -> None:
        """(Re)populates the widget for a new feature type / query
        result: rebuilds the in-memory sample layer, refreshes the field
        list, and updates the SQL editor's known field names.

        Building the sample layer is wrapped defensively: a field name
        containing a character the memory provider's own connection-
        string syntax treats as a delimiter (e.g. '&' or '=' - real WFS
        field/attribute names have been observed to be unpredictable) can
        make the provider string unparseable. Any failure in this step is
        reported directly in values_status_label, and the field list
        still gets populated so raw SQL can still be typed manually even
        if sample-value loading itself is broken for a given dataset."""
        self.fieldnames = fieldnames
        self.geometry_field_name = geometry_field_name
        try:
            self._memory_layer = build_memory_layer_from_rows(fieldnames, rows)
            if not self._memory_layer.isValid():
                self._memory_layer = None
                self.values_status_label.setText(
                    self.tr(
                        "Could not build a sample layer for this data - value "
                        "loading below is unavailable, but fields can still be "
                        "typed into the expression manually."
                    )
                )
            else:
                self.values_status_label.setText("")
        except Exception as e:
            self._memory_layer = None
            self.values_status_label.setText(
                self.tr("Could not build a sample layer: {}").format(e)
            )
            from qgis.core import Qgis, QgsMessageLog

            QgsMessageLog.logMessage(
                f"Could not build sample layer from fieldnames={fieldnames!r}: {e!r}",
                "SIGate",
                Qgis.MessageLevel.Warning,
            )

        self.fields_list.clear()
        for name in fieldnames:
            self.fields_list.addItem(name)

        self.values_list.clear()
        self.load_some_button.setEnabled(False)
        self.load_all_button.setEnabled(False)

        self.sql_editor.setFieldNames(fieldnames)

    # --------------------------------------------------------------- fields / values

    def _current_field(self) -> Optional[str]:
        item = self.fields_list.currentItem()
        return item.text() if item else None

    def _on_field_selection_changed(
        self,
        _current: Optional[QListWidgetItem],
        _previous: Optional[QListWidgetItem],
    ) -> None:
        has_selection = self._current_field() is not None
        self.load_some_button.setEnabled(has_selection)
        self.load_all_button.setEnabled(has_selection)
        self.values_list.clear()
        self.values_status_label.setText("")

    def _on_field_double_clicked(self, item: QListWidgetItem) -> None:
        field = item.text()
        token = GEOM_TOKEN if field == self.geometry_field_name else field
        self._insert_text(token)

    def _on_value_double_clicked(self, item: QListWidgetItem) -> None:
        escaped = item.text().replace("'", "''")
        self._insert_text(f"'{escaped}'")

    def _load_values(self, limit: Optional[int]) -> None:
        """Loads distinct values for the selected field - both "Load
        some" and "Load all" always show distinct values, never raw
        duplicates (QgsVectorLayer.uniqueValues() deduplicates by
        design, matching QGIS's own native expression builder
        convention); they differ only in how many distinct values are
        shown - up to DEFAULT_SAMPLE_SIZE, or every one.

        Every way this can come up empty is reported explicitly in
        values_status_label rather than leaving the list silently
        unchanged."""
        field = self._current_field()
        if not field:
            return
        if self._memory_layer is None:
            self.values_status_label.setText(
                self.tr(
                    "No sample layer available for this data - see the message above."
                )
            )
            return
        field_index = self._memory_layer.fields().lookupField(field)
        if field_index < 0:
            self.values_status_label.setText(
                self.tr('Field "{}" was not found in the sample data.').format(field)
            )
            return
        try:
            if limit is not None:
                values = self._memory_layer.uniqueValues(field_index, limit)
            else:
                values = self._memory_layer.uniqueValues(field_index)
        except Exception as e:
            self.values_status_label.setText(
                self.tr("Could not load values: {}").format(e)
            )
            from qgis.core import Qgis, QgsMessageLog

            QgsMessageLog.logMessage(
                f"Could not load unique values for field {field!r}: {e!r}",
                "SIGate",
                Qgis.MessageLevel.Warning,
            )
            return
        self._all_loaded_values = sorted(str(v) for v in values)
        self._apply_value_filter()
        if not self._all_loaded_values:
            self.values_status_label.setText(
                self.tr('No values found for "{}" in the current sample.').format(field)
            )
        else:
            self.values_status_label.setText(
                self.tr("{} distinct value(s).").format(len(self._all_loaded_values))
            )

    def _load_sample_values(self) -> None:
        self._load_values(limit=DEFAULT_SAMPLE_SIZE)

    def _load_all_values(self) -> None:
        self._load_values(limit=None)

    def _apply_value_filter(self) -> None:
        needle = self.value_filter_edit.text().strip().lower()
        self.values_list.clear()
        for value in getattr(self, "_all_loaded_values", []):
            if needle and needle not in value.lower():
                continue
            self.values_list.addItem(value)

    # --------------------------------------------------------------- expression editor

    def _insert_text(self, text: str) -> None:
        self.sql_editor.insertText(text)
        self.sql_editor.setFocus()

    def expression_text(self) -> str:
        """The raw text as typed - special tokens are not substituted
        here; use resolved_expression_text() for that."""
        return self.sql_editor.text().strip()

    def resolved_expression_text(self) -> str:
        """The expression with $geom/@map_extent substituted with real
        values, ready to actually use. Canvas extent substitution is
        skipped (token left as-is) if no extent provider was supplied."""
        extent_wkt = (
            self.get_canvas_extent_wkt() if self.get_canvas_extent_wkt else None
        )
        return substitute_tokens(
            self.expression_text(), self.geometry_field_name, extent_wkt
        )

    def set_expression_text(self, text: str) -> None:
        self.sql_editor.setText(text)
