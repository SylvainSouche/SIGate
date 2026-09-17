"""
Tests for translation.py - the LLM-assisted data-content translation
memory. All pure-Python (Path-based, no QGIS dependency), matching the
module's own design goal of staying independently testable.
"""

import json

from sigate.translation import TranslationStore, load_store, save_store


def test_load_store_returns_empty_dict_when_no_file_exists(tmp_path):
    assert load_store(tmp_path, "fr") == {}


def test_load_store_returns_empty_dict_for_corrupted_json(tmp_path):
    (tmp_path / "translations_fr.json").write_text("not valid json{{{")
    assert load_store(tmp_path, "fr") == {}


def test_load_store_returns_empty_dict_when_json_is_not_an_object(tmp_path):
    (tmp_path / "translations_fr.json").write_text("[1, 2, 3]")
    assert load_store(tmp_path, "fr") == {}


def test_save_store_then_load_store_round_trips(tmp_path):
    save_store(tmp_path, "fr", {"Hello": "Bonjour"})
    assert load_store(tmp_path, "fr") == {"Hello": "Bonjour"}


def test_save_store_creates_the_data_directory_if_missing(tmp_path):
    nested = tmp_path / "does" / "not" / "exist" / "yet"
    save_store(nested, "en", {"a": "b"})
    assert (nested / "translations_en.json").is_file()


def test_stores_are_kept_separate_per_language(tmp_path):
    save_store(tmp_path, "fr", {"Hello": "Bonjour"})
    save_store(tmp_path, "de", {"Hello": "Hallo"})
    assert load_store(tmp_path, "fr") == {"Hello": "Bonjour"}
    assert load_store(tmp_path, "de") == {"Hello": "Hallo"}


def test_translation_store_translate_returns_source_text_when_untranslated(tmp_path):
    store = TranslationStore(tmp_path, "fr")
    assert store.translate("Naturvernområder") == "Naturvernområder"


def test_translation_store_translate_returns_real_translation_when_available(tmp_path):
    save_store(tmp_path, "fr", {"Naturvernområder": "Zones naturelles protégées"})
    store = TranslationStore(tmp_path, "fr")
    assert store.translate("Naturvernområder") == "Zones naturelles protégées"


def test_translation_store_translate_records_new_strings_as_seen(tmp_path):
    store = TranslationStore(tmp_path, "fr")
    store.translate("Aktsomhetskart for snøskred")
    store.flush()
    assert load_store(tmp_path, "fr") == {"Aktsomhetskart for snøskred": ""}


def test_translation_store_does_not_write_to_disk_before_flush(tmp_path):
    store = TranslationStore(tmp_path, "fr")
    store.translate("some title")
    assert load_store(tmp_path, "fr") == {}, (
        "a newly-seen string must stay in memory until flush() is called"
    )


def test_translation_store_flush_is_a_no_op_when_nothing_changed(tmp_path):
    save_store(tmp_path, "fr", {"Hello": "Bonjour"})
    store = TranslationStore(tmp_path, "fr")
    store.translate("Hello")  # already known, translated - nothing new
    store.flush()
    # Confirm flush() didn't rewrite the file unnecessarily by checking
    # the content is still exactly what was saved (a real bug here
    # would more likely show up as an exception on a read-only
    # filesystem, but this at least confirms content didn't change).
    assert load_store(tmp_path, "fr") == {"Hello": "Bonjour"}


def test_translation_store_handles_empty_string_gracefully(tmp_path):
    store = TranslationStore(tmp_path, "fr")
    assert store.translate("") == ""
    store.flush()
    assert load_store(tmp_path, "fr") == {}


def test_translation_store_pending_returns_only_untranslated_entries(tmp_path):
    save_store(tmp_path, "fr", {"Hello": "Bonjour", "Goodbye": ""})
    store = TranslationStore(tmp_path, "fr")
    store.translate("New title")
    assert store.pending() == {"Goodbye": "", "New title": ""}


def test_translation_store_import_translations_fills_in_pending_entries(tmp_path):
    save_store(tmp_path, "fr", {"Hello": "", "Goodbye": ""})
    store = TranslationStore(tmp_path, "fr")
    updated = store.import_translations({"Hello": "Bonjour", "Goodbye": "Au revoir"})
    assert updated == 2
    assert store.pending() == {}
    assert load_store(tmp_path, "fr") == {"Hello": "Bonjour", "Goodbye": "Au revoir"}


def test_translation_store_import_translations_ignores_unknown_keys(tmp_path):
    """A key the LLM (or a person) added that this plugin never
    actually saw is not silently adopted - this store's own keys are
    the authority on what was actually encountered."""
    save_store(tmp_path, "fr", {"Hello": ""})
    store = TranslationStore(tmp_path, "fr")
    updated = store.import_translations({"Hello": "Bonjour", "Made up key": "Invente"})
    assert updated == 1
    assert "Made up key" not in load_store(tmp_path, "fr")


def test_translation_store_import_translations_does_not_blank_out_existing_good_ones(
    tmp_path,
):
    """Re-importing a partially-filled-in file (e.g. the user only
    translated half the pending list) must not overwrite already-good
    translations with an empty value for the untranslated half."""
    save_store(tmp_path, "fr", {"Hello": "Bonjour"})
    store = TranslationStore(tmp_path, "fr")
    updated = store.import_translations({"Hello": ""})
    assert updated == 0
    assert load_store(tmp_path, "fr") == {"Hello": "Bonjour"}


def test_translation_store_import_translations_only_overwrites_when_value_differs(
    tmp_path,
):
    save_store(tmp_path, "fr", {"Hello": "Bonjour"})
    store = TranslationStore(tmp_path, "fr")
    updated = store.import_translations({"Hello": "Bonjour"})
    assert updated == 0


def test_load_store_coerces_non_string_values_to_strings(tmp_path):
    """Defensive: a hand-edited or LLM-mangled JSON file might carry a
    non-string value (a number, null already handled by json.load as
    None which str() turns into "None" - acceptable, not a crash)."""
    (tmp_path / "translations_fr.json").write_text(
        json.dumps({"Hello": 123}), encoding="utf-8"
    )
    result = load_store(tmp_path, "fr")
    assert result == {"Hello": "123"}
