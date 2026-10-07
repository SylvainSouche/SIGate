"""Tests for sigate.sources.organisations - pure Python."""

import pytest

from sigate.sources.organisations import (
    derive_organisation,
    entry_name,
    organisation_of,
    short_label,
)
from sigate.sources.seed import all_seed_sources
from sigate.sources.store import SourceConfig


def _source(name, organisation=""):
    return SourceConfig(
        key="k", display_name=name, country="X", organisation=organisation
    )


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Regione Piemonte - base cartography (Italy)", "Regione Piemonte"),
        ("Arpa Piemonte - SIVA avalanches (Italy)", "Arpa Piemonte"),
        ("IGN (France)", "IGN"),
        ("kartverket", "kartverket"),
        ("NVE avalanche events - Skredhendelser (Norway)", "NVE avalanche events"),
        ("Foo — bar", "Foo"),
    ],
)
def test_organisation_is_derived_from_the_display_name(name, expected):
    assert derive_organisation(name) == expected


def test_a_stated_organisation_wins():
    assert organisation_of(_source("Anything - at all", "NVE")) == "NVE"
    assert organisation_of(_source("Anything - at all")) == "Anything"


@pytest.mark.parametrize(
    "name, org, expected",
    [
        (
            "Regione Piemonte - base cartography (Italy)",
            "Regione Piemonte",
            "base cartography",
        ),
        ("BKG TopPlusOpen (Germany)", "BKG", "TopPlusOpen"),
        (
            "NVE avalanche events - Skredhendelser (Norway)",
            "NVE",
            "avalanche events - Skredhendelser",
        ),
        (
            "Gefahrenkarten (geodienste.ch, Switzerland)",
            "geodienste.ch",
            "Gefahrenkarten",
        ),
        ("IGN (France)", "IGN", ""),
        ("basemap.at (Austria)", "basemap.at", ""),
        ("ign - Foo", "IGN", "Foo"),
    ],
)
def test_entry_name_drops_the_organisation_prefix_and_country(name, org, expected):
    assert entry_name(_source(name, org)) == expected


def test_short_label_variants():
    source = _source("Regione Piemonte - base cartography (Italy)", "Regione Piemonte")
    assert (
        short_label(source, "BDTRE Hydrography", shared=True)
        == "base cartography — BDTRE Hydrography"
    )
    assert short_label(source, None, shared=False) == "base cartography"
    bare = _source("IGN (France)", "IGN")
    assert short_label(bare, "public", shared=True) == "public"
    assert short_label(bare, None, shared=False) == "IGN"


def test_every_bundled_source_states_its_organisation_and_it_prefixes_or_is_the_name():
    for source in all_seed_sources():
        assert source.organisation, source.key
