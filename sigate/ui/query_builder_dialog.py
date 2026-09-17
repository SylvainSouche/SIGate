"""
ui.query_builder_dialog - wraps ui.expression_builder.ExpressionBuilderWidget
in its own QDialog, so a tab can offer filter-building as a separate
panel (opened via a "Filter..." button) rather than as a permanently
embedded section of the main widget.

The intended flow: browse a feature type's features in the results
grid, hit "Filter" to open the query builder as its own window,
construct the filter there, and have the grid re-query to show only
matching features once accepted.

Deliberately built fresh each time it's opened (set_fields_and_rows is
called again on every open) rather than kept as one persistent, reused
instance - this avoids any Qt widget-reparenting-in-and-out-of-a-dialog
lifetime fragility; the cost is just re-populating from whatever
fieldnames/rows the caller currently has cached, which is cheap (no
network fetch - the caller already has this from its last query).
"""

from typing import Callable, List, Optional

from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout

from .expression_builder import ExpressionBuilderWidget


class QueryBuilderDialog(QDialog):
    """A modal Filter panel: the same fields/values/SQL-editor widget as
    before, just presented as its own dialog instead of embedded inline.
    OK accepts whatever's currently in the SQL editor (even if empty -
    an empty accepted expression is a legitimate "no filter" choice, and
    is how a filter gets explicitly cleared from within the dialog
    itself, not just via a separate Clear button on the owning tab);
    Cancel discards any edits made in this dialog session."""

    def __init__(
        self, parent=None, get_canvas_extent_wkt: Optional[Callable[[], str]] = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Filter"))
        self.setModal(True)
        self.resize(760, 520)

        layout = QVBoxLayout(self)
        self.expression_builder = ExpressionBuilderWidget(
            self, get_canvas_extent_wkt=get_canvas_extent_wkt
        )
        layout.addWidget(self.expression_builder)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def set_fields_and_rows(
        self, fieldnames: List[str], rows: List[dict], geometry_field_name: str = "geom"
    ) -> None:
        """Populates the field list and sample-value source. Delegates
        directly to ExpressionBuilderWidget.set_fields_and_rows - see
        that method's own docstring for the full contract (an empty
        fieldnames/rows is accepted and simply yields an empty field
        list, not an error)."""
        self.expression_builder.set_fields_and_rows(
            fieldnames, rows, geometry_field_name=geometry_field_name
        )

    def set_expression_text(self, text: str) -> None:
        """Preloads the SQL editor with a previously-built expression -
        used so reopening Filter after already having one active shows
        what's currently applied, rather than an empty editor."""
        self.expression_builder.set_expression_text(text)

    def expression_text(self) -> str:
        """The raw, as-typed text - what the owning tab should store to
        preload the next time this dialog is reopened."""
        return self.expression_builder.expression_text()

    def resolved_expression_text(self) -> str:
        """The expression with $geom/@map_extent substituted - what the
        owning tab should actually use in a query or connection string."""
        return self.expression_builder.resolved_expression_text()
