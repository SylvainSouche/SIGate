"""
translation - a lightweight, LLM-assisted translation memory for *data
content* strings (dataset/layer/feature-type titles discovered from
external servers at runtime) - NOT for this plugin's own UI text (QGIS's
usual .ts/.qm mechanism, under i18n/, already handles that entirely
differently: those strings are known at build time and extracted by
`make i18n`, whereas the strings this module handles aren't known until
a real server response is actually parsed).

Some sources already provide real, human-translated content in multiple
languages via a request parameter - e.g. WMTS/WMS on geo.admin.ch honours
?lang=de/fr/it/en directly (see gateways.wmts_wms and
GatewayCapabilities.supports_locale) - and for those, this module has
nothing to do: asking the server directly for the language you want is
always better than translating its response after the fact. This
mechanism exists specifically for sources with no such option at all -
Atom feed entry titles, STAC asset titles, Geonorge's Tema/Tittel fields,
WFS feature type names - where there is no server-side language
parameter to ask for in the first place.

Workflow: as the user browses normally, every displayed title is routed
through TranslationStore.translate(), which also records the string as
"seen" the first time it's encountered - no separate "discovery mode" is
needed; discovery is a side effect of ordinary use. When ready, "Discover
& Save" (see ui.settings_dialog) exports every seen-but-not-yet-
translated string to a plain JSON file; the user feeds that file to an
LLM of their own choosing outside this plugin (no API key or network
call happens here - this module only ever reads/writes a local JSON
file) and gets back a JSON with the same keys and real translations as
values; "Load translations" merges that back into the persistent store.
From then on, translate() transparently substitutes the translated text
for that exact source string (no fuzzy matching), leaving anything
without a translation as-is.

Stored per target language as a flat JSON dict {source: translated}, one
file per language code, in the plugin's data directory
(get_sigate_data_dir()). This module itself takes that directory as a
plain argument rather than importing ui.settings directly, matching
gateways/'s own convention of staying import-light and independently
testable - the caller (which already has QGIS available) resolves the
real path.

An empty string value means "seen, not yet translated" - simpler than
tracking a second file/set separately, and keeps the Discover-and-Save
export a one-line filter over the same store.
"""

import json
from pathlib import Path
from typing import Dict


def _store_path(data_dir: Path, lang: str) -> Path:
    return data_dir / f"translations_{lang}.json"


def load_store(data_dir: Path, lang: str) -> Dict[str, str]:
    """Every known source string for this language, translated or not
    (empty value = not yet translated). Returns {} if no store exists
    yet for this language - a fresh install, or a language never used
    before, isn't an error - and also {} for a corrupted file (bad JSON,
    or JSON that isn't an object) rather than raising, since a broken
    translation file is exactly the kind of thing that shouldn't be
    able to break ordinary browsing."""
    path = _store_path(data_dir, lang)
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def save_store(data_dir: Path, lang: str, store: Dict[str, str]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = _store_path(data_dir, lang)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2, sort_keys=True)


class TranslationStore:
    """Loaded once and reused for the lifetime of whatever holds it
    (typically one widget instance) - a bare load_store()/save_store()
    pair alone would cost a full file read on every single title
    displayed, which matters when rendering dozens of entries per page.
    New strings discovered during that lifetime are kept in memory and
    only written back to disk via flush() (called once per render pass,
    not once per string)."""

    def __init__(self, data_dir: Path, lang: str):
        self.data_dir = data_dir
        self.lang = lang
        self._data = load_store(data_dir, lang)
        self._dirty = False

    def translate(self, source_text: str) -> str:
        """The translated text if a real (non-empty) translation is on
        file, else source_text unchanged - a missing or not-yet-
        translated entry is not an error, just "nothing to substitute
        yet". Also records source_text as seen (in memory only, until
        flush()) if this is the first time it's been encountered."""
        if not source_text:
            return source_text
        if source_text not in self._data:
            self._data[source_text] = ""
            self._dirty = True
            return source_text
        return self._data[source_text] or source_text

    def flush(self) -> None:
        """Persists any newly-discovered strings recorded since this
        store was loaded (or since the last flush). Cheap to call
        often - a no-op if nothing changed since the last flush."""
        if self._dirty:
            save_store(self.data_dir, self.lang, self._data)
            self._dirty = False

    def pending(self) -> Dict[str, str]:
        """Every known source string for this language that has no
        translation yet - the JSON to hand to an LLM. Returns a dict
        rather than writing a file itself, so the caller (a Qt
        file-save dialog) controls exactly where it lands."""
        return {k: v for k, v in self._data.items() if not v}

    def import_translations(self, incoming: Dict[str, str]) -> int:
        """Merges a completed translation JSON (from an LLM) into the
        store - only fills in values for keys that are already known
        (source strings this plugin has actually seen) and only
        overwrites an existing value if the incoming one is non-empty,
        so re-importing a partially-filled-in file never blanks out an
        already-good translation. Returns the number of entries
        actually updated, for a status message. A key in the incoming
        file that this store has never seen is silently ignored rather
        than added - this store's own keys are the authority on what
        this plugin actually encountered, not whatever a person or LLM
        included in the file."""
        updated = 0
        for key, value in incoming.items():
            if key in self._data and value and self._data[key] != value:
                self._data[key] = value
                updated += 1
        if updated:
            self._dirty = True
            self.flush()
        return updated
