"""Tests for sigate.sources.countries - pure Python."""

import pytest

from sigate.sources.countries import canonical_country
from sigate.sources.seed import all_seed_sources


@pytest.mark.parametrize(
    "text, expected",
    [
        ("NO", "Norway"),
        ("no", "Norway"),
        ("nor", "Norway"),
        ("Norge", "Norway"),
        ("norway", "Norway"),
        ("  Norway ", "Norway"),
        ("de", "Germany"),
        ("DEU", "Germany"),
        ("Deutschland", "Germany"),
        ("FR", "France"),
        ("IT", "Italy"),
        ("Italia", "Italy"),
        ("CH", "Switzerland"),
        ("Österreich", "Austria"),
        ("osterreich", "Austria"),
        ("UK", "United Kingdom"),
    ],
)
def test_codes_and_local_names_map_to_one_english_name(text, expected):
    assert canonical_country(text) == expected


def test_unknown_text_is_kept_as_typed_and_empty_stays_empty():
    assert canonical_country("Atlantis") == "Atlantis"
    assert canonical_country(" Atlantis ") == "Atlantis"
    assert canonical_country("") == ""
    assert canonical_country(None) == ""


def test_every_bundled_source_country_is_already_canonical():
    for source in all_seed_sources():
        assert canonical_country(source.country) == source.country, source.key
