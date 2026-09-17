"""
ui.target_picker - shared widget for selecting a central-repo target
(always required) and a destination target (required for archive
downloads, optional for plain files, per the archive/simple-file branch
in the download pipeline design).
"""

from pathlib import Path
from typing import Optional

from qgis.PyQt.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from sigate.download.targets import DownloadTargets

from . import settings as sigate_settings


class TargetPicker(QWidget):
    """A pair of folder pickers: central repository (always shown) and
    destination (shown, and required, only when require_destination is
    set - toggle it with set_require_destination once the caller knows
    whether the current selection is an archive)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.require_destination = False
        self._build_ui()
        self._load_last_used_paths()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        repo_row = QHBoxLayout()
        repo_row.addWidget(QLabel(self.tr("Central repository:")))
        self.repo_edit = QLineEdit(self)
        self.repo_edit.textChanged.connect(self._on_repo_changed)
        repo_row.addWidget(self.repo_edit)
        repo_browse_button = QPushButton(self.tr("Browse..."), self)
        repo_browse_button.clicked.connect(self._browse_repo)
        repo_row.addWidget(repo_browse_button)
        layout.addLayout(repo_row)

        dest_row = QHBoxLayout()
        self.dest_label = QLabel(self.tr("Destination (optional):"), self)
        dest_row.addWidget(self.dest_label)
        self.dest_edit = QLineEdit(self)
        self.dest_edit.textChanged.connect(self._on_dest_changed)
        dest_row.addWidget(self.dest_edit)
        dest_browse_button = QPushButton(self.tr("Browse..."), self)
        dest_browse_button.clicked.connect(self._browse_dest)
        dest_row.addWidget(dest_browse_button)
        layout.addLayout(dest_row)

    def _load_last_used_paths(self) -> None:
        """Pre-fills both fields from whatever was last used, in any
        previous session - without this, the folder(s) picked have to be
        re-entered every single time the tab is opened, since a bare
        QLineEdit remembers nothing on its own."""
        self.repo_edit.setText(sigate_settings.get_last_central_repo() or "")
        self.dest_edit.setText(sigate_settings.get_last_destination() or "")

    def _on_repo_changed(self, text: str) -> None:
        sigate_settings.set_last_central_repo(text.strip() or None)

    def _on_dest_changed(self, text: str) -> None:
        sigate_settings.set_last_destination(text.strip() or None)

    def _browse_repo(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, self.tr("Select central repository folder")
        )
        if path:
            self.repo_edit.setText(path)

    def _browse_dest(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, self.tr("Select destination folder")
        )
        if path:
            self.dest_edit.setText(path)

    def set_require_destination(self, required: bool) -> None:
        """Called by the owning widget once it knows whether the current
        selection is an archive (destination required) or a plain file
        (destination optional)."""
        self.require_destination = required
        label_text = (
            self.tr("Destination (required for archives):")
            if required
            else self.tr("Destination (optional):")
        )
        self.dest_label.setText(label_text)

    def validation_error(self) -> Optional[str]:
        """Returns an error message if the current selection is invalid
        given require_destination, or None if it is fine."""
        if not self.repo_edit.text().strip():
            return self.tr("A central repository folder is required.")
        if self.require_destination and not self.dest_edit.text().strip():
            return self.tr("A destination folder is required for archive downloads.")
        return None

    def get_targets(self) -> DownloadTargets:
        """Builds a DownloadTargets from the current field values.
        Assumes validation_error() has already been checked and returned
        None - does not itself validate that the repository field is
        non-empty (an empty string becomes Path(".") via Path's own
        behavior, not a raised error)."""
        repo_text = self.repo_edit.text().strip()
        dest_text = self.dest_edit.text().strip()
        central_repo = Path(repo_text)
        destination = Path(dest_text) if dest_text else None
        return DownloadTargets(central_repo=central_repo, destination=destination)
