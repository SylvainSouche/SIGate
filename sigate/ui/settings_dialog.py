"""
ui.settings_dialog - SIGate's plugin-wide settings dialog: the
size-warning threshold, an optional manual override for the 7-Zip
executable path, the UI/data locale, and the data-content translation
memory (see translation.py's own module docstring for what this is and
isn't - it is not this plugin's own UI translation, which QGIS's usual
.ts/.qm mechanism under i18n/ already handles).

Source-config management (adding/editing a country's gateway
configuration) is not yet part of this dialog - it is deferred, per
sigate_dev_workflow.md's Step 7 scope, to a later pass once source
configs are actually editable through more than the bundled seed data.
"""

import json

from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from sigate.download.archive import find_7z_executable
from sigate.translation import TranslationStore

from . import settings as sigate_settings

_BYTES_PER_GB = 1024**3
_MAX_THRESHOLD_GB = 1_000_000  # a spin box needs some upper bound; this is high enough not to matter in practice

# Confirmed real on geo.admin.ch's own WMS/WMTS docs (?lang=) - the same
# four options are used here for the translation-memory target
# language, since one locale setting now serves both purposes (see
# gateways.wmts_wms and translation.py's own module docstrings for why
# that's a deliberate reuse, not a coincidence).
_LOCALE_CHOICES = [
    ("English", "en"),
    ("Deutsch", "de"),
    ("Français", "fr"),
    ("Italiano", "it"),
]


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("SIGate Settings"))
        self._build_ui()
        self._load_current_values()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.threshold_spin = QSpinBox(self)
        self.threshold_spin.setRange(0, _MAX_THRESHOLD_GB)
        self.threshold_spin.setSuffix(self.tr(" GB"))
        form.addRow(self.tr("Size warning threshold:"), self.threshold_spin)

        sevenzip_row = QHBoxLayout()
        self.sevenzip_edit = QLineEdit(self)
        sevenzip_row.addWidget(self.sevenzip_edit)
        browse_button = QPushButton(self.tr("Browse..."), self)
        browse_button.clicked.connect(self._browse_sevenzip)
        sevenzip_row.addWidget(browse_button)
        detect_button = QPushButton(self.tr("Auto-detect"), self)
        detect_button.clicked.connect(self._auto_detect_sevenzip)
        sevenzip_row.addWidget(detect_button)
        form.addRow(self.tr("7-Zip executable (optional override):"), sevenzip_row)

        self.locale_combo = QComboBox(self)
        for label, code in _LOCALE_CHOICES:
            self.locale_combo.addItem(label, code)
        form.addRow(self.tr("Preferred language:"), self.locale_combo)

        layout.addLayout(form)

        translation_group = QGroupBox(self.tr("Data translation"), self)
        translation_layout = QVBoxLayout(translation_group)
        translation_layout.addWidget(
            QLabel(
                self.tr(
                    "For sources with no native language option of their own "
                    "(e.g. Atom feed or catalog entry titles) - not this "
                    "plugin's own interface text. Discover exports the titles "
                    "seen so far with no translation yet; translate that file "
                    "with an LLM of your choice, then load the result back in."
                ),
                translation_group,
            )
        )
        translation_buttons = QHBoxLayout()
        discover_button = QPushButton(
            self.tr("Discover && Save untranslated strings..."), self
        )
        discover_button.clicked.connect(self._on_discover_and_save)
        translation_buttons.addWidget(discover_button)
        load_button = QPushButton(self.tr("Load translations..."), self)
        load_button.clicked.connect(self._on_load_translations)
        translation_buttons.addWidget(load_button)
        translation_layout.addLayout(translation_buttons)
        layout.addWidget(translation_group)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _load_current_values(self) -> None:
        threshold_bytes = sigate_settings.get_size_warning_threshold_bytes()
        self.threshold_spin.setValue(round(threshold_bytes / _BYTES_PER_GB))
        self.sevenzip_edit.setText(sigate_settings.get_sevenzip_path_override() or "")
        current_locale = sigate_settings.get_locale()
        index = self.locale_combo.findData(current_locale)
        if index == -1:
            # A locale saved by an older version, or hand-edited outside
            # this dialog, that isn't one of the four options offered -
            # fall back to the default rather than silently showing
            # whatever findData's -1 would otherwise leave selected.
            index = self.locale_combo.findData(sigate_settings.DEFAULT_LOCALE)
        self.locale_combo.setCurrentIndex(max(index, 0))

    def _browse_sevenzip(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr("Select 7-Zip executable"))
        if path:
            self.sevenzip_edit.setText(path)

    def _auto_detect_sevenzip(self) -> None:
        """Fills in the path field if a 7z executable is found on PATH;
        leaves the field untouched (not cleared) if not, so a manually
        typed path isn't silently wiped by a failed auto-detect."""
        found = find_7z_executable()
        if found:
            self.sevenzip_edit.setText(found)

    def _on_accept(self) -> None:
        sigate_settings.set_size_warning_threshold_bytes(
            self.threshold_spin.value() * _BYTES_PER_GB
        )
        sigate_settings.set_sevenzip_path_override(
            self.sevenzip_edit.text().strip() or None
        )
        sigate_settings.set_locale(self.locale_combo.currentData())
        self.accept()

    def _current_translation_store(self) -> TranslationStore:
        # The combo's own current selection, not the persisted setting -
        # so Discover/Load act on whichever language is selected right
        # now in this dialog, even before Ok/Cancel is pressed.
        lang = self.locale_combo.currentData() or sigate_settings.DEFAULT_LOCALE
        return TranslationStore(sigate_settings.get_sigate_data_dir(), lang)

    def _on_discover_and_save(self) -> None:
        store = self._current_translation_store()
        pending = store.pending()
        if not pending:
            QMessageBox.information(
                self,
                self.tr("Nothing to translate"),
                self.tr(
                    "No untranslated strings have been discovered yet for this "
                    "language. Browse the WM(T)S, WFS, or Bulk Download tabs "
                    "first, then come back here."
                ),
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Save untranslated strings"),
            "sigate_translations_pending.json",
            self.tr("JSON files (*.json)"),
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(pending, f, ensure_ascii=False, indent=2, sort_keys=True)
        except OSError as e:
            QMessageBox.warning(self, self.tr("Could not save file"), str(e))
            return
        QMessageBox.information(
            self,
            self.tr("Saved"),
            self.tr("Saved {} untranslated string(s) to {}.").format(
                len(pending), path
            ),
        )

    def _on_load_translations(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Load translations"),
            "",
            self.tr("JSON files (*.json)"),
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                incoming = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            QMessageBox.warning(self, self.tr("Could not read file"), str(e))
            return
        if not isinstance(incoming, dict):
            QMessageBox.warning(
                self,
                self.tr("Unexpected file content"),
                self.tr(
                    "Expected a JSON object mapping original text to its translation."
                ),
            )
            return
        store = self._current_translation_store()
        updated = store.import_translations(
            {str(k): str(v) for k, v in incoming.items()}
        )
        QMessageBox.information(
            self,
            self.tr("Loaded"),
            self.tr("Updated {} translation(s).").format(updated),
        )
