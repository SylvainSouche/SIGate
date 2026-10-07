"""
Tests for sigate.sources - no Qt/QGIS dependency, no real filesystem
location assumed (uses pytest's tmp_path fixture rather than any real
QGIS profile directory).
"""

from sigate.sources.seed import (
    BASEMAP_AT,
    BKG_DE,
    GEODIENSTE_CH,
    IGN_FRANCE,
    NETHERLANDS_PDOK,
    NORWAY_GEONORGE,
    SWISSTOPO_CH,
    all_seed_sources,
)
from sigate.sources.store import (
    AuthConfig,
    GatewayConfig,
    SourceConfig,
    add_or_replace_source,
    load_user_overrides,
    merged_sources,
    save_user_overrides,
)


def test_seed_ign_france_has_expected_gateways():
    assert IGN_FRANCE.key == "ign_fr"
    atom_gw = IGN_FRANCE.gateway("atom")
    assert atom_gw.base_url == "https://data.geopf.fr/telechargement/capabilities"
    assert atom_gw.auth.kind == "none"

    wfs_gw = IGN_FRANCE.gateway("wfs")
    assert wfs_gw.base_url == "https://data.geopf.fr/wfs/ows"


def test_seed_ign_france_has_two_distinct_wmts_endpoints():
    """A source can expose more than one instance of the same gateway
    type - here, a public no-auth WMTS endpoint and a separate private
    apikey-gated one. A single gateway() lookup can't represent both."""
    wmts_gateways = IGN_FRANCE.gateways_of_type("wmts_wms")
    assert len(wmts_gateways) == 2
    roles = {gw.extra["role"] for gw in wmts_gateways}
    assert roles == {"public", "private"}
    private = next(gw for gw in wmts_gateways if gw.extra["role"] == "private")
    assert private.auth.kind == "apikey_header"
    assert private.auth.value == "ign_scan_ws"


def test_all_seed_sources_includes_ign():
    sources = all_seed_sources()
    assert any(s.key == "ign_fr" for s in sources)


def test_seed_swisstopo_has_wmts_and_wfs_no_auth_needed():
    assert SWISSTOPO_CH.key == "swisstopo_ch"
    wmts_gw = SWISSTOPO_CH.gateway("wmts_wms")
    assert wmts_gw.base_url == "https://wmts.geo.admin.ch/1.0.0/WMTSCapabilities.xml"
    assert wmts_gw.auth.kind == "none"

    wfs_gw = SWISSTOPO_CH.gateway("wfs")
    assert wfs_gw.base_url == "https://wfs.geo.admin.ch"
    assert wfs_gw.auth.kind == "none"


def test_seed_bkg_germany_has_wmts_and_wfs_no_auth_needed():
    assert BKG_DE.key == "bkg_de"
    wmts_gw = BKG_DE.gateway("wmts_wms")
    assert (
        wmts_gw.base_url
        == "https://sgx.geodatenzentrum.de/wmts_topplus_open/1.0.0/WMTSCapabilities.xml"
    )
    assert wmts_gw.auth.kind == "none"

    wfs_gw = BKG_DE.gateway("wfs")
    # Real bug, reported directly ("germany wfs is empty"): BKG's WFS
    # offering is fragmented per dataset, not one unified endpoint - a
    # bare ".../wfs" never corresponded to any real service. Fixed to
    # one specific, confirmed dataset (VG250, administrative
    # boundaries).
    assert wfs_gw.base_url == "https://sgx.geodatenzentrum.de/wfs_vg250"
    assert wfs_gw.auth.kind == "none"


def test_seed_pdok_netherlands_has_wmts_only_no_atom_yet():
    """PDOK's Atom-style bulk downloads are deliberately not seeded yet -
    an HTML landing page listing many per-dataset feeds, not one
    machine-readable capabilities document the way IGN's own Atom
    gateway is - see the module's own docstring and
    docs/candidate_sources.md."""
    assert NETHERLANDS_PDOK.key == "pdok_nl"
    wmts_gw = NETHERLANDS_PDOK.gateway("wmts_wms")
    assert (
        wmts_gw.base_url
        == "https://service.pdok.nl/brt/achtergrondkaart/wmts/v2_0/WMTSCapabilities.xml"
    )
    assert wmts_gw.auth.kind == "none"
    assert NETHERLANDS_PDOK.gateway("atom") is None


def test_seed_basemap_at_has_wmts_only_no_wfs():
    """basemap.at publishes WMTS only - no vector feature service was
    found in the source research."""
    assert BASEMAP_AT.key == "basemap_at"
    wmts_gw = BASEMAP_AT.gateway("wmts_wms")
    assert wmts_gw.base_url == "https://basemap.at/wmts/1.0.0/WMTSCapabilities.xml"
    assert wmts_gw.auth.kind == "none"
    assert BASEMAP_AT.gateway("wfs") is None


def test_all_seed_sources_includes_all_thirteen_seeded_sources():
    sources = all_seed_sources()
    keys = {s.key for s in sources}
    assert keys == {
        "ign_fr",
        "swisstopo_ch",
        "geodienste_ch",
        "geonorge_no",
        "bkg_de",
        "pdok_nl",
        "basemap_at",
        "piemonte_arpa",
        "inrae_avalanches",
        "fvg_it",
        "nve_no",
        "vda_it",
        "regione_piemonte",
    }


def test_save_and_load_user_overrides_round_trip(tmp_path):
    custom = SourceConfig(
        key="test_source",
        display_name="Test Source",
        country="Testland",
        gateways=[
            GatewayConfig(
                gateway_type="atom",
                base_url="https://example.test/atom",
                auth=AuthConfig(
                    kind="apikey_header", header_name="X-Api-Key", value="secret123"
                ),
            )
        ],
    )
    save_user_overrides(tmp_path, [custom])

    loaded = load_user_overrides(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].key == "test_source"
    assert loaded[0].gateway("atom").auth.value == "secret123"


def test_load_user_overrides_returns_empty_list_when_no_file_exists(tmp_path):
    # A fresh install, or a user who has never added anything - not an error.
    assert load_user_overrides(tmp_path / "does_not_exist_yet") == []


def test_add_or_replace_source_adds_new_entry(tmp_path):
    new_source = SourceConfig(key="new_one", display_name="New One", country="Nowhere")
    updated = add_or_replace_source(tmp_path, new_source)
    assert len(updated) == 1
    assert load_user_overrides(tmp_path)[0].key == "new_one"


def test_add_or_replace_source_replaces_existing_entry_with_same_key(tmp_path):
    v1 = SourceConfig(key="dup", display_name="Version 1", country="X")
    v2 = SourceConfig(key="dup", display_name="Version 2", country="X")
    add_or_replace_source(tmp_path, v1)
    updated = add_or_replace_source(tmp_path, v2)
    assert len(updated) == 1  # replaced, not appended
    assert updated[0].display_name == "Version 2"


def test_merged_sources_user_override_replaces_seed_entry_with_same_key(tmp_path):
    """A user override with the same key as a seed source replaces it
    entirely; it is not merged field by field."""
    seed = [
        SourceConfig(key="ign_fr", display_name="IGN (France) [seed]", country="France")
    ]
    override = SourceConfig(
        key="ign_fr", display_name="IGN (France) [override]", country="France"
    )
    save_user_overrides(tmp_path, [override])

    combined = merged_sources(seed, tmp_path)
    assert len(combined) == 1
    assert combined[0].display_name == "IGN (France) [override]"


def test_merged_sources_keeps_seed_entries_not_overridden(tmp_path):
    seed = [
        SourceConfig(key="ign_fr", display_name="IGN (France)", country="France"),
        SourceConfig(key="untouched", display_name="Untouched Seed", country="Y"),
    ]
    override = SourceConfig(
        key="ign_fr", display_name="IGN (France) [override]", country="France"
    )
    save_user_overrides(tmp_path, [override])

    combined = merged_sources(seed, tmp_path)
    keys = {s.key for s in combined}
    assert keys == {"ign_fr", "untouched"}


def test_seed_swisstopo_has_four_topo_dem_stac_instances_each_with_a_collection_id():
    """Confirmed real (data.geo.admin.ch /collections and /collections?
    provider=swisstopo both fetched live) - one gateway instance per
    dataset, disambiguated by role, the same multi-instance pattern
    IGN's public/private WMTS already established."""
    stac_gateways = SWISSTOPO_CH.gateways_of_type("stac")
    topo_dem_roles = {"swissALTI3D", "swissTLM3D", "swissSURFACE3D", "SWISSIMAGE"}
    topo_dem_gateways = [
        gw for gw in stac_gateways if gw.extra.get("role") in topo_dem_roles
    ]
    assert len(topo_dem_gateways) == 4
    for gw in topo_dem_gateways:
        assert gw.base_url == "https://data.geo.admin.ch/api/stac/v1"
        assert gw.extra.get("collection_id", "").startswith("ch.swisstopo.")


def test_seed_swisstopo_has_eight_outdoor_activity_stac_instances():
    """Added directly in response to a request for ski touring, hiking,
    and outdoor-activity route networks specifically - confirmed real
    this session (opendata.swiss's own listing, and this session's own
    live /collections fetch). Spans two different authoring agencies
    (swisstopo-karto for the ski/snowshoe route maps, ASTRA for the
    wanderland/veloland family) but all through the same
    data.geo.admin.ch STAC API as everything else on this source."""
    stac_gateways = SWISSTOPO_CH.gateways_of_type("stac")
    outdoor_roles = {
        "ski touring routes",
        "snowshoe routes",
        "hiking network (Wanderland)",
        "winter hiking trails",
        "snowshoe trails (ASTRA)",
        "cycling network (Veloland)",
        "mountain bike network",
        "skating network",
    }
    outdoor_gateways = [
        gw for gw in stac_gateways if gw.extra.get("role") in outdoor_roles
    ]
    assert len(outdoor_gateways) == 8
    collection_ids = {gw.extra.get("collection_id") for gw in outdoor_gateways}
    # A real mix of prefixes - not everything on this source is
    # literally swisstopo-brand-authored, only served via the same
    # platform (see the config's own comment on why that's fine here).
    assert any(cid.startswith("ch.swisstopo-karto.") for cid in collection_ids)
    assert any(cid.startswith("ch.astra.") for cid in collection_ids)


def test_seed_swisstopo_stac_and_wmts_wms_coexist_as_separate_gateway_types():
    """SWISSTOPO_CH now has both wmts_wms and stac instances - a source
    is not limited to one gateway type, and adding stac must not have
    displaced the pre-existing wmts_wms/wfs entries."""
    assert SWISSTOPO_CH.gateway("wmts_wms") is not None
    assert len(SWISSTOPO_CH.gateways_of_type("stac")) == 12


def test_seed_geodienste_ch_is_a_distinct_source_from_swisstopo_ch():
    """geodienste.ch (intercantonal hazard-map platform) and
    data.geo.admin.ch (swisstopo's federal platform) are genuinely
    different platforms - added directly in response to a follow-up
    question about avalanche hazard zone data specifically."""
    assert GEODIENSTE_CH.key == "geodienste_ch"
    assert GEODIENSTE_CH.key != SWISSTOPO_CH.key
    assert GEODIENSTE_CH.country == "Switzerland"


def test_seed_geodienste_ch_has_all_three_confirmed_gateway_types():
    wmts_gw = GEODIENSTE_CH.gateway("wmts_wms")
    assert wmts_gw.base_url == "https://geodienste.ch/db/gefahrenkarten_v1_3_0/deu"

    wfs_gw = GEODIENSTE_CH.gateway("wfs")
    # Confirmed to live on a different subdomain than the WMS gateway -
    # a real correction made mid-session after the WMS GetCapabilities
    # document itself (fetched live) declared this exact URL as its own
    # WFS sibling, contradicting an external services.csv export's
    # listed URL for the same dataset.
    assert wfs_gw.base_url == "https://wfs.geodienste.ch/gefahrenkarten_v1_3_0/deu"

    stac_gw = GEODIENSTE_CH.gateway("stac")
    assert stac_gw.base_url == "https://www.geodienste.ch/stac"
    assert stac_gw.extra.get("collection_id") == "gefahrenkarten"


def test_seed_norway_geonorge_uses_the_geonorge_catalog_gateway_type():
    """Added directly in response to a follow-up question about
    building a two-level hierarchy from Geonorge's otherwise-flat
    catalog specifically."""
    assert NORWAY_GEONORGE.key == "geonorge_no"
    assert NORWAY_GEONORGE.country == "Norway"
    gw = NORWAY_GEONORGE.gateway("geonorge_catalog")
    assert gw is not None
    assert "kartkatalog.geonorge.no/api/search" in gw.base_url
    assert "mediatype=csv" in gw.base_url


def test_seed_norway_geonorge_base_url_matches_the_gateway_modules_own_constant():
    """sources/seed.py deliberately duplicates this URL as a literal
    string rather than importing gateways.geonorge_catalog (config data
    has no gateway-implementation dependency anywhere else in this
    file) - this test is what actually keeps the two in sync, since
    nothing else would catch them silently drifting apart."""
    from sigate.gateways.geonorge_catalog import CATALOG_URL

    gw = NORWAY_GEONORGE.gateway("geonorge_catalog")
    assert gw.base_url == CATALOG_URL


def test_sources_doc_lists_every_seeded_source_endpoint_and_collection():
    """docs/sources.md is the human-readable list of every bundled backend
    and what it serves. Its counts and descriptions come from live
    fetches and can't be checked here, but the *inventory* can: a source,
    gateway URL or STAC collection added to (or renamed in) seed.py
    without a matching entry in that document fails here."""
    from pathlib import Path

    doc = (Path(__file__).parent.parent / "docs" / "sources.md").read_text(
        encoding="utf-8"
    )
    missing = []
    for source in all_seed_sources():
        if source.key not in doc:
            missing.append(f"source key {source.key}")
        for gateway in source.gateways:
            if gateway.base_url not in doc:
                missing.append(f"{source.key}: {gateway.base_url}")
            collection_id = gateway.extra.get("collection_id")
            if collection_id and collection_id not in doc:
                missing.append(f"{source.key}: collection {collection_id}")
    assert missing == [], "docs/sources.md is missing: " + "; ".join(missing)
