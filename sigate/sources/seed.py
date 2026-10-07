"""
sources.seed - bundled default source configs, shipped with the plugin.

IGN (France) is fully populated below, covering the four gateway
instances it exposes: an unauthenticated Atom download service, an
unauthenticated WFS endpoint, a public unauthenticated WMTS endpoint, and
a separate private WMTS endpoint gated behind an API key.

Switzerland (swisstopo), Germany (BKG TopPlusOpen), the Netherlands
(PDOK), and Austria (basemap.at) were added from a broader ~30-country
candidate list, as the "mature enough to integrate now" tier - free,
unauthenticated, standards-compliant WMTS/WFS endpoints needing only
gateway types this plugin already implements at the time (wmts_wms,
wfs), each independently confirmed via multiple official sources (not
just carried over from the candidate list unchecked). Switzerland later
gained real "stac" gateway instances too (see SWISSTOPO_CH below), once
gateways/stac.py existed to back them, and a second Swiss source
(GEODIENSTE_CH) once avalanche/flood/landslide/rockfall hazard-zone data
was specifically asked about - the same standard applied here
originally: a gateway type gets seeded only once its module exists and
its endpoints are confirmed, never speculatively. Norway followed the
same path once removed from "doesn't fit at all" to "actually does,
differently": kartkatalog.geonorge.no/api/search is still a flat
search-and-facets catalog with no tree of its own, but
gateways/geonorge_catalog.py builds a real two-level Theme -> Dataset
hierarchy on top of it from one CSV export of the whole catalog (see
NORWAY_GEONORGE below and that module's own docstring) - a source shape
not fitting this tab's design was a reason to defer, not a permanent
verdict. See docs/candidate_sources.md for the full list, including the
countries still deliberately deferred and exactly why (per-user
credentials embedded in the URL itself rather than a header, no single
machine-readable endpoint, fragmented per-region/per-dataset services,
non-OGC-standard tile schemes, or a gateway type - FTP - that still has
no implementation at all).

Other countries beyond these four are not seeded yet, for the same
reason already established here: a source config referencing a gateway
type with no implementation to back it, or an endpoint not actually
confirmed to work the way this plugin expects, would be misleading. Add
one here once its gateway module exists and its endpoints are confirmed.
"""

from .store import AuthConfig, GatewayConfig, SourceConfig

IGN_FRANCE = SourceConfig(
    key="ign_fr",
    display_name="IGN (France)",
    country="France",
    organisation="IGN",
    gateways=[
        GatewayConfig(
            gateway_type="atom",
            base_url="https://data.geopf.fr/telechargement/capabilities",
            # No authentication required for browsing or downloading.
        ),
        GatewayConfig(
            gateway_type="wfs",
            base_url="https://data.geopf.fr/wfs/ows",
            extra={
                # This endpoint is GeoServer-backed and accepts the
                # CQL_FILTER vendor parameter for server-side attribute
                # search, but that specific capability has not yet been
                # verified end-to-end against the live server.
                "cql_filter_confirmed": "false",
            },
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://data.geopf.fr/wmts",
            extra={"role": "public"},
            # Layer, style, and tile-matrix-set information is discovered
            # live from this endpoint's own GetCapabilities response
            # (gateways.wmts_wms), not hardcoded here.
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://data.geopf.fr/private/wmts",
            auth=AuthConfig(
                kind="apikey_header",
                header_name="apikey",
                # A published, shared key for this endpoint - not a
                # secret specific to any one user.
                value="ign_scan_ws",
            ),
            extra={"role": "private"},
        ),
        GatewayConfig(
            gateway_type="csw",
            base_url="https://data.geopf.fr/csw",
            extra={
                # This endpoint is reachable, but the metadata records it
                # returns have been found to lack a usable resource link
                # (no OnlineResource), which limits its usefulness as a
                # discovery mechanism pointing to an actual dataset. No
                # gateway module implements CSW yet.
                "router_mechanism_validated": "false",
            },
        ),
    ],
)


SWISSTOPO_CH = SourceConfig(
    key="swisstopo_ch",
    display_name="swisstopo (Switzerland)",
    country="Switzerland",
    organisation="swisstopo",
    gateways=[
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://wmts.geo.admin.ch/1.0.0/WMTSCapabilities.xml",
            extra={
                "role": "public",
                # A REST-style WMTS endpoint - the URL is already a
                # complete, static capabilities document, not a
                # KVP-driven service like IGN's. gateways.wmts_wms
                # always appends "?SERVICE=WMTS&VERSION=1.0.0&
                # REQUEST=GetCapabilities" regardless (matching IGN's
                # own KVP-style base_url convention); confirmed via
                # multiple independent official geo.admin.ch/GitHub
                # sources that this exact URL is the real, current
                # endpoint, but appending that query string to an
                # already-static resource has not been confirmed to
                # work by an actual fetch in this environment - expected
                # to be harmless (a static file server has no reason to
                # reject or vary on an unrecognized query string) but
                # flagged here rather than silently assumed.
                "capabilities_url_is_already_static": "true",
                # Defaults to the legacy CH1903/EPSG:21781 reference
                # frame unless a specific EPSG is requested via a
                # different URL path (wmts.geo.admin.ch/EPSG/{code}/...)
                # - not wired into this config; whichever TileMatrixSets
                # this default document actually declares are what
                # gateways.wmts_wms's real capabilities parsing surfaces.
                "defaults_to_legacy_epsg_21781": "true",
            },
        ),
        GatewayConfig(
            gateway_type="wfs",
            base_url="https://wfs.geo.admin.ch",
            extra={
                # Not confirmed to be GeoServer-backed (unlike IGN's own
                # WFS) - whether the CQL_FILTER vendor parameter this
                # plugin's WFS query-builder panel relies on is even
                # accepted here is genuinely unknown, not just unverified
                # end-to-end the way IGN's own flag means. Weaker
                # evidence than its WMTS sibling above: the only
                # reference found for this exact URL was an older
                # community mailing-list post noting "a few datasets are
                # published via a WFS service" - real, but describing
                # limited coverage, not confirmed current. Given a
                # similar, sibling guess for Germany's WFS turned out to
                # be wrong entirely (see BKG_DE below - fixed after a
                # direct report), this one is genuinely at risk of the
                # same problem and hasn't been proactively re-verified
                # to the same standard - report if this also comes back
                # empty.
                "cql_filter_confirmed": "false",
                "backend_implementation_unknown": "true",
                "endpoint_confidence": "weak_dated_reference_only",
            },
        ),
        # STAC gateways below - confirmed live and real this session
        # (data.geo.admin.ch's own /collections and /collections?
        # provider=swisstopo listings were fetched directly), unlike
        # most of the WMTS/WFS entries above which predate this and
        # carry their own, weaker confidence notes. A STAC connection
        # is scoped to one specific collection at construction time
        # (extra["collection_id"]) rather than one universal browsable
        # root - see gateways/stac.py's own module docstring for why:
        # data.geo.admin.ch alone has ~570 collections spanning every
        # federal office, not a curated single tree the way IGN's Atom
        # capabilities document is. Twelve of the datasets this
        # project's own research repeatedly returned to (topo/DEM
        # basics, plus - added in a later pass - outdoor-activity route
        # networks) are wired in below, each its own gateway instance
        # disambiguated by role - the same multi-instance pattern
        # already used for IGN's public/private WMTS. Not every one of
        # the ~570 collections belongs here; add another only once it's
        # actually been confirmed real and worth browsing, the same
        # standard applied to the twelve below.
        GatewayConfig(
            gateway_type="stac",
            base_url="https://data.geo.admin.ch/api/stac/v1",
            extra={
                "role": "swissALTI3D",
                "collection_id": "ch.swisstopo.swissalti3d",
            },
        ),
        GatewayConfig(
            gateway_type="stac",
            base_url="https://data.geo.admin.ch/api/stac/v1",
            extra={
                "role": "swissTLM3D",
                "collection_id": "ch.swisstopo.swisstlm3d",
            },
        ),
        GatewayConfig(
            gateway_type="stac",
            base_url="https://data.geo.admin.ch/api/stac/v1",
            extra={
                "role": "swissSURFACE3D",
                "collection_id": "ch.swisstopo.swisssurface3d",
            },
        ),
        GatewayConfig(
            gateway_type="stac",
            base_url="https://data.geo.admin.ch/api/stac/v1",
            extra={
                "role": "SWISSIMAGE",
                "collection_id": "ch.swisstopo.swissimage-dop10",
            },
        ),
        # Outdoor-activity datasets added directly in response to a
        # request for these specifically ("ski touring route, trekking
        # routes, huts... anything related to outdoor activities").
        # All confirmed real and current this session (opendata.swiss's
        # own listing confirms ch.swisstopo-karto.skitouren as
        # published/modified Dec 2024, vector format since a Dec 2024
        # format switch; the four ch.astra.* entries were seen directly
        # in this session's own live /collections fetch). Authored by
        # different federal offices (swisstopo-karto for the ski/
        # snowshoe route maps, ASTRA/FEDRO - the Federal Roads Office -
        # for the wanderland/veloland family) but served through the
        # same data.geo.admin.ch STAC API as everything else here, so
        # kept on this one source rather than split across several -
        # the "source" here represents the shared national platform,
        # not literally swisstopo-brand-only data (its existing wfs/
        # wmts_wms gateways above already aren't exclusively
        # swisstopo-authored either).
        #
        # Two real, explicitly requested items are NOT here, on
        # purpose, not by oversight - neither fits any gateway type
        # this plugin implements, and faking a collection_id for either
        # would be worse than leaving them out:
        #   - Avalanche hazard: real data exists (SLF's own
        #     aws.slf.ch/api/warningregion/, GeoJSON/KML), but it's a
        #     wholly separate API, not on data.geo.admin.ch's STAC
        #     catalog at all - would need a new gateway type, not just
        #     another collection_id here.
        #   - SAC mountain huts: confirmed NOT an independent
        #     swisstopo/FSDI dataset - swisstopo's own documentation on
        #     its Base Map app states partner-organisation POI layers
        #     like SAC hut data are "neither entered nor managed by
        #     swisstopo." No downloadable dataset found for this at all.
        GatewayConfig(
            gateway_type="stac",
            base_url="https://data.geo.admin.ch/api/stac/v1",
            extra={
                "role": "ski touring routes",
                "collection_id": "ch.swisstopo-karto.skitouren",
            },
        ),
        GatewayConfig(
            gateway_type="stac",
            base_url="https://data.geo.admin.ch/api/stac/v1",
            extra={
                "role": "snowshoe routes",
                "collection_id": "ch.swisstopo-karto.schneeschuhrouten",
            },
        ),
        GatewayConfig(
            gateway_type="stac",
            base_url="https://data.geo.admin.ch/api/stac/v1",
            extra={
                "role": "hiking network (Wanderland)",
                "collection_id": "ch.astra.wanderland",
            },
        ),
        GatewayConfig(
            gateway_type="stac",
            base_url="https://data.geo.admin.ch/api/stac/v1",
            extra={
                "role": "winter hiking trails",
                "collection_id": "ch.astra.winterwanderwege",
            },
        ),
        GatewayConfig(
            gateway_type="stac",
            base_url="https://data.geo.admin.ch/api/stac/v1",
            extra={
                # Distinct dataset from "snowshoe routes" above (a
                # different authoring agency, ASTRA vs swisstopo-karto)
                # - real possibility of overlapping/duplicate coverage
                # between the two, not independently checked this
                # session. Both kept in, disambiguated by role, rather
                # than guessing which one to drop.
                "role": "snowshoe trails (ASTRA)",
                "collection_id": "ch.astra.schneeschuhwanderwege",
            },
        ),
        GatewayConfig(
            gateway_type="stac",
            base_url="https://data.geo.admin.ch/api/stac/v1",
            extra={
                "role": "cycling network (Veloland)",
                "collection_id": "ch.astra.veloland",
            },
        ),
        GatewayConfig(
            gateway_type="stac",
            base_url="https://data.geo.admin.ch/api/stac/v1",
            extra={
                "role": "mountain bike network",
                "collection_id": "ch.astra.mountainbikeland",
            },
        ),
        GatewayConfig(
            gateway_type="stac",
            base_url="https://data.geo.admin.ch/api/stac/v1",
            extra={
                "role": "skating network",
                "collection_id": "ch.astra.skatingland",
            },
        ),
    ],
)


GEODIENSTE_CH = SourceConfig(
    key="geodienste_ch",
    display_name="Gefahrenkarten (geodienste.ch, Switzerland)",
    country="Switzerland",
    organisation="geodienste.ch",
    gateways=[
        # geodienste.ch is a genuinely different platform from
        # data.geo.admin.ch above - Switzerland's *intercantonal*
        # geodata portal ("Das interkantonale Portal für den Bezug von
        # Geodaten und -diensten"), harmonizing cantonal data under
        # shared national schemas, rather than swisstopo's federal
        # platform. Added directly in response to a follow-up question
        # about avalanche hazard zones specifically ("no wms nor wfs
        # layer with... identifies avalanche zone?") - the Gefahrenkarten
        # (hazard maps) dataset covers avalanche (Lawine) together with
        # flood, landslide, and rockfall hazard, near-nationally: 98% of
        # the area needing mapping is confirmed already covered for
        # avalanches specifically.
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geodienste.ch/db/gefahrenkarten_v1_3_0/deu",
            extra={
                "role": "public",
                # Best-confirmed of this source's three gateways: this
                # exact URL was fetched live this session and returned
                # a real, complete WMS 1.3.0 GetCapabilities document -
                # confirmed real avalanche-specific layers include
                # gefahrenhinweisgebiet_lawine (indicative hazard-hint
                # area), gefahrengebiet_lawine (the authoritative zone,
                # explicitly "behördenverbindlich" - legally binding),
                # and intensitaet_lawine_jaehrlichkeit_{0_30,30_100,
                # 100_300}_jahre plus intensitaet_lawine_extremereignis
                # (intensity by return period: <30/30-100/100-300 years
                # and extreme-event scenarios) - not just a binary
                # hazard/no-hazard zone.
                "capabilities_url_is_already_static": "false",
                # A plain WMS: read with the WMS GetCapabilities parser.
                "service": "wms",
            },
        ),
        GatewayConfig(
            gateway_type="wfs",
            base_url="https://wfs.geodienste.ch/gefahrenkarten_v1_3_0/deu",
            extra={
                # Confirmed live and real, but with a genuine, specific
                # caveat worth keeping: the WMS GetCapabilities document
                # above (fetched directly) declares its own WFS sibling
                # at exactly this wfs.geodienste.ch subdomain via its
                # <OnlineResource> element - a different domain than
                # geodienste.ch/db/... (which, tried directly with
                # SERVICE=WFS, returned WMS content regardless of the
                # query parameter - a real discrepancy from what an
                # external services.csv export for this same dataset
                # had listed as the WFS GetCapabilities URL). This
                # wfs.geodienste.ch URL itself was fetched and
                # confirmed live (responded with a real XML content
                # type), but its actual FeatureType names were not
                # independently readable this session - the WFS tab
                # discovers feature types live from GetCapabilities the
                # same way it already does for every other WFS source,
                # so this doesn't block the connection from working,
                # but the exact typenames aren't pre-verified the way
                # the WMS layer names above are.
                "endpoint_confidence": "domain_corrected_from_external_metadata_typenames_unverified",
            },
        ),
        # Naturereigniskataster: the cantons' register of past natural
        # events (floods, debris flows, landslides, rockfall and - the
        # reason it is here - recorded avalanches, "prozessraum_lawine").
        # Two datasets: the base one and the "umfassend" (comprehensive)
        # one with point/line/area observations. Both WFS 2.0.0, checked
        # live: 13 and 16 feature types in EPSG:2056 (LV95), 28,066
        # prozessraum_lawine features in the base dataset. MapServer
        # backed: it silently ignores a CQL_FILTER (the query returned
        # the full count for an impossible filter), so the WFS tab's
        # query grid is not narrowed by the Filter panel on these two;
        # "Add to map" is unaffected (QGIS translates the expression
        # itself).
        GatewayConfig(
            gateway_type="wfs",
            base_url="https://geodienste.ch/db/naturereigniskataster_v1_0_0/deu",
            extra={
                "role": "Naturereigniskataster (recorded natural events)",
                "cql_filter_supported": "false",
            },
        ),
        GatewayConfig(
            gateway_type="wfs",
            base_url="https://geodienste.ch/db/naturereigniskataster_umfassend_v1_0_0/deu",
            extra={
                "role": "Naturereigniskataster umfassend (with observations)",
                "cql_filter_supported": "false",
            },
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geodienste.ch/db/naturereigniskataster_v1_0_0/deu",
            extra={"role": "Naturereigniskataster (WMS)", "service": "wms"},
        ),
        GatewayConfig(
            gateway_type="stac",
            base_url="https://www.geodienste.ch/stac",
            extra={
                "role": "Gefahrenkarten (all hazard types)",
                "collection_id": "gefahrenkarten",
                # Weakest-confirmed of this source's three gateways:
                # inferred from a real, live-fetched per-canton STAC
                # Item URL (".../stac/collections/gefahrenkarten/items/
                # gefahrenkarten_v1_3-AG") rather than from an
                # independently fetched Collection or Items-listing
                # response - the {root}/collections/{id}/items shape
                # this infers matches the STAC API spec and this
                # project's own gateways/stac.py convention, but hasn't
                # been fetched and confirmed live the way the WMS
                # gateway above has.
                "items_url_inferred_not_fetched": "true",
            },
        ),
    ],
)


NORWAY_GEONORGE = SourceConfig(
    key="geonorge_no",
    display_name="Geonorge kartkatalog (Norway)",
    country="Norway",
    organisation="Geonorge",
    gateways=[
        # A genuinely different shape from every other Bulk Listing
        # source: kartkatalog.geonorge.no/api/search is otherwise a
        # flat search-and-facets catalog with no browsable tree of its
        # own - originally the concrete example this project used for
        # "a source shape this tab's design doesn't fit at all" (see
        # the historical note further down in this file and in
        # docs/candidate_sources.md). Added once gateways/
        # geonorge_catalog.py existed to build a real two-level Theme
        # -> Dataset hierarchy on top of it, derived from one live CSV
        # fetch of the whole catalog rather than anything curated or
        # guessed - see that module's own docstring for the full
        # mechanism (in short: Level 1 is the real, distinct Theme
        # values among catalog rows that actually carry an Atom Feed
        # distribution; Level 2 is those rows themselves, each one's
        # href being its own real per-dataset Atom feed URL; anything
        # past that delegates straight to gateways.atom, unchanged).
        #
        # base_url intentionally duplicates gateways.geonorge_catalog.
        # CATALOG_URL as a literal string rather than importing that
        # module here - sources/seed.py holds plain config data with no
        # gateway-implementation dependency anywhere else in this file,
        # and this entry doesn't need to be the exception. Keep the two
        # in sync if this URL (limit/mediatype params in particular)
        # ever changes.
        GatewayConfig(
            gateway_type="geonorge_catalog",
            base_url="https://kartkatalog.geonorge.no/api/search?limit=10000&text=&mediatype=csv",
            extra={
                # Confirmed live this session: this exact URL returns
                # the entire catalog (8,637 records at fetch time) as
                # one flat CSV, with a dedicated "Atom-feed" column per
                # row - real, not inferred. The ~400-of-8,637 subset
                # that actually carries an Atom feed is what Level 2
                # restricts itself to; most of the catalog (WMS/WFS-
                # only services, REST APIs, etc.) simply doesn't appear
                # in this Bulk Listing connection at all - a real,
                # stated limitation, not a bug.
                "row_count_at_confirmation": "8637",
            },
        ),
    ],
)


BKG_DE = SourceConfig(
    key="bkg_de",
    display_name="BKG TopPlusOpen (Germany)",
    country="Germany",
    organisation="BKG",
    gateways=[
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://sgx.geodatenzentrum.de/wmts_topplus_open/1.0.0/WMTSCapabilities.xml",
            extra={
                "role": "public",
                # Same REST-style-endpoint caveat as swisstopo above -
                # confirmed real and current via multiple independent
                # BKG PDF documentation sources, but the exact request
                # this plugin builds (appending a KVP query string to an
                # already-static capabilities URL) has not been directly
                # fetched in this environment.
                "capabilities_url_is_already_static": "true",
                # Free under "Datenlizenz Deutschland - Namensnennung
                # 2.0" (attribution required, no cost, no key) -
                # confirmed directly from BKG's own product
                # documentation.
                "license": "dl-de-by-2.0",
            },
        ),
        GatewayConfig(
            gateway_type="wfs",
            base_url="https://sgx.geodatenzentrum.de/wfs_vg250",
            extra={
                # Real bug, caught by direct report ("germany wfs is
                # empty") and confirmed on investigation: BKG has no
                # single, unified WFS the way IGN does - unlike the
                # WMTS above, its WFS offering is fragmented per
                # dataset, each with its own distinctly-named endpoint
                # (wfs_vg250, wfs_vg1000, wfs_gnde,
                # wfs_vertriebseinheiten, ...), the same "one country,
                # many endpoints" pattern already correctly identified
                # for Belgium/Slovakia/Ireland in
                # docs/candidate_sources.md - a bare "/wfs" was a
                # guessed URL that never corresponded to any real BKG
                # service at all, hence returning nothing. Replaced
                # with one specific, real, independently confirmed
                # dataset: VG250 (Verwaltungsgebiete 1:250 000 -
                # administrative boundaries, state through municipality
                # level), confirmed via multiple independent BKG/MIS
                # metadata-catalog sources - free, "Es gelten keine
                # Zugriffsbeschränkungen" (no access restrictions). This
                # is genuinely one dataset, not "Germany's WFS" in
                # general - other BKG WFS datasets exist at their own
                # separate wfs_* endpoints, not seeded here.
                "cql_filter_confirmed": "false",
                "represents_single_dataset_not_general_wfs": "true",
            },
        ),
    ],
)


NETHERLANDS_PDOK = SourceConfig(
    key="pdok_nl",
    display_name="PDOK (Netherlands)",
    country="Netherlands",
    organisation="PDOK",
    gateways=[
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://service.pdok.nl/brt/achtergrondkaart/wmts/v2_0/WMTSCapabilities.xml",
            extra={
                "role": "public",
                "capabilities_url_is_already_static": "true",
                # BRT Achtergrondkaart, the base topographic map -
                # confirmed real and current via multiple independent
                # official PDOK sources. PDOK also exposes this same
                # dataset as OGC API Vector Tiles
                # (api.pdok.nl/kadaster/brt-achtergrondkaart/ogc/v1),
                # a different, not-yet-implemented gateway type -
                # WMTS is what this config uses.
                "license": "cc-by-4.0",
            },
        ),
        # PDOK's Atom-style bulk downloads are deliberately not seeded
        # here yet: pdok.nl/en/products/pdok-downloads/atomfeeds is an
        # HTML landing page listing separate per-dataset Atom feed URLs,
        # not one single machine-readable capabilities document the way
        # IGN's own Atom gateway (one root URL covering every product)
        # is. Wiring this in properly would need either picking one
        # specific dataset's real feed URL, or extending the atom
        # gateway/UI to handle a source exposing many independent feeds
        # rather than one - neither decided yet. See
        # docs/candidate_sources.md.
    ],
)


BASEMAP_AT = SourceConfig(
    key="basemap_at",
    display_name="basemap.at (Austria)",
    country="Austria",
    organisation="basemap.at",
    gateways=[
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://basemap.at/wmts/1.0.0/WMTSCapabilities.xml",
            extra={
                "role": "public",
                "capabilities_url_is_already_static": "true",
                # Confirmed to an unusually high degree for this
                # project's own "independently re-checked via search,
                # not a live fetch" standard: one search result's own
                # source URL was this exact endpoint, and its returned
                # content was the real capabilities document itself
                # (OGC WMTS 1.0.0, real layer identifiers like
                # "geolandbasemap"/"bmapoverlay", real bounding boxes,
                # explicit "none" auth, City of Vienna contact details) -
                # independently corroborated by geoland.at's own official
                # nationwide-geoservices page describing the same URL
                # and listing the same real layer names. Operated by the
                # City of Vienna on behalf of Austria's federal-state
                # geoland.at cooperation; free, no key.
                "license": "cc-by-4.0",
            },
        ),
        # No WFS found for basemap.at in the source research - it
        # publishes WMTS only, no vector feature service.
    ],
)


PIEMONTE_ARPA = SourceConfig(
    key="piemonte_arpa",
    display_name="Arpa Piemonte - SIVA avalanches (Italy)",
    country="Italy",
    organisation="Arpa Piemonte",
    gateways=[
        # Piemonte's regional environmental agency publishes its avalanche
        # information system (SIVA: documented avalanche events, the
        # Catasto Valanghe, etc.) through an ArcGIS Server MapServer. It
        # also has a WMS, but no WFS - the ArcGIS REST query endpoint is
        # what lets features be filtered and downloaded rather than only
        # drawn. Found by analysing the service's own web viewer; the
        # listing was fetched live (21 layers, 14 queryable).
        GatewayConfig(
            gateway_type="arcgis_rest",
            base_url="https://webgis.arpa.piemonte.it/server/rest/services/rischi_naturali/SIVA/MapServer",
            extra={"role": "public"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://webgis.arpa.piemonte.it/server/services/rischi_naturali/SIVA/MapServer/WMSServer",
            extra={"role": "WMS (drawn map)", "service": "wms"},
        ),
    ],
)


INRAE_AVALANCHES = SourceConfig(
    key="inrae_avalanches",
    display_name="INRAE avalanches - CLPA, EPA (France)",
    country="France",
    organisation="INRAE",
    gateways=[
        # The French avalanche location map (CLPA) and the avalanche
        # events survey (EPA) from INRAE's own GeoServer, 38 feature
        # types (clpa_zonpi: 50,089 polygons; clpa_linpi: 26,946 lines).
        # This is the stand-in while the CLPA layers that used to be
        # served by the IGN Geoplateforme are missing from it after the
        # geoportail -> cartes.gouv.fr migration. GeoServer: CQL_FILTER
        # confirmed live (an impossible filter returns 0). Several layers
        # are published in EPSG:3857 or EPSG:4326 - the WFS tab reads each
        # feature type's own default CRS.
        GatewayConfig(
            gateway_type="wfs",
            base_url="https://carto-service.inrae.fr/geoserver/siavalanches/wfs",
            extra={"role": "public", "cql_filter_confirmed": "true"},
        ),
    ],
)


FVG_IT = SourceConfig(
    key="fvg_it",
    display_name="Friuli Venezia Giulia - risk zones (Italy)",
    country="Italy",
    organisation="Friuli Venezia Giulia",
    gateways=[
        # The region's ZONE_RISC workspace: surveyed and photo-interpreted
        # avalanches (CV_VALANGHE_RILEVATE: 3,875 features), avalanche
        # danger zones, probable avalanche tracks, plus wildfire layers.
        # 14 feature types, GeoServer: CQL_FILTER confirmed live. Feature
        # types declare EPSG:6708 as default CRS (a geographic datum
        # variant) - not a case the plugin has been exercised on.
        GatewayConfig(
            gateway_type="wfs",
            base_url="https://serviziogc.regione.fvg.it/geoserver/ZONE_RISC/wfs",
            extra={"role": "public", "cql_filter_confirmed": "true"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://serviziogc.regione.fvg.it/geoserver/ZONE_RISC/wms",
            extra={"role": "WMS (drawn map)", "service": "wms"},
        ),
    ],
)


NVE_NO = SourceConfig(
    key="nve_no",
    display_name="NVE avalanche events - Skredhendelser (Norway)",
    country="Norway",
    organisation="NVE",
    gateways=[
        # NVE's landslide/avalanche event register. The ArcGIS Enterprise
        # service publishes a WMS but no WFS (the WFSServer path answers
        # with an error page); its REST endpoint is open: 10 layers
        # (release and run-out points/areas, fatalities, consequences,
        # snow avalanche), 26,218 records in layer 9, EPSG:25833.
        GatewayConfig(
            gateway_type="arcgis_rest",
            base_url="https://kart.nve.no/enterprise/rest/services/Skredhendelser1/MapServer",
            extra={"role": "public"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://kart.nve.no/enterprise/services/Skredhendelser1/MapServer/WMSServer",
            extra={"role": "WMS (drawn map)", "service": "wms"},
        ),
    ],
)


VDA_IT = SourceConfig(
    key="vda_it",
    display_name="Valle d'Aosta - avalanche cadastre (Italy)",
    country="Italy",
    organisation="Valle d'Aosta",
    gateways=[
        # The region's avalanche cadastre (Catasto Valanghe) and snow
        # gauge poles. WMS only (no WFS found); answers a 1.3.0
        # GetCapabilities live.
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://servizisct.regione.vda.it/ows/public/CatastoValanghe",
            extra={"role": "Catasto valanghe (avalanche cadastre)", "service": "wms"},
        ),
        # The region's other public QGIS Server projects, one URL each.
        # Found through the catalogue at geoportale.regione.vda.it/wms-nuovi
        # (123 entries over 25 projects). WMS only: WFS, WMTS and OGC API
        # Features answer but list nothing; downloads need a login.
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://servizisct.regione.vda.it/ows/public/CartaDissesti",
            extra={"role": "Carta dei dissesti (slope failures)", "service": "wms"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://servizisct.regione.vda.it/ows/public/CartaPAI",
            extra={"role": "Carta PAI (hydrogeological risk plan)", "service": "wms"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://servizisct.regione.vda.it/ows/public/Ambiti",
            extra={
                "role": "Ambiti inedificabili (building-restricted zones)",
                "service": "wms",
            },
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://servizisct.regione.vda.it/ows/public/CatastoGhiacciai",
            extra={"role": "Catasto ghiacciai (glaciers)", "service": "wms"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://servizisct.regione.vda.it/ows/public/CTRN",
            extra={"role": "CTRN (regional topographic map)", "service": "wms"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://servizisct.regione.vda.it/ows/public/Viabilita",
            extra={"role": "Viabilita (roads)", "service": "wms"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://servizisct.regione.vda.it/ows/public/AreeTutelate",
            extra={"role": "Aree tutelate (parks and Natura 2000)", "service": "wms"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://servizisct.regione.vda.it/ows/public/CartaGeologicaContinua",
            extra={"role": "Carta geologica continua", "service": "wms"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://servizisct.regione.vda.it/ows/public/CartaDeiSuoli",
            extra={"role": "Carta dei suoli", "service": "wms"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://servizisct.regione.vda.it/ows/public/WMS_DTM",
            extra={"role": "Modello altimetrico (DTM)", "service": "wms"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://servizisct.regione.vda.it/ows/public/WMS_CTR2005",
            extra={"role": "CTR 2005 (topographic map)", "service": "wms"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://servizisct.regione.vda.it/ows/public/WMS_Ortofoto2024",
            extra={"role": "Ortofoto 2024", "service": "wms"},
        ),
    ],
)


REGIONE_PIEMONTE = SourceConfig(
    key="regione_piemonte",
    display_name="Regione Piemonte - base cartography (Italy)",
    country="Italy",
    organisation="Regione Piemonte",
    gateways=[
        # The Region's own geoportal (IGR), distinct from Arpa Piemonte
        # above. Base maps: WMTS and WMS capabilities are static files,
        # one layer per file (EPSG:32632 tile sets, one EPSG:3857). Only
        # the current-year 2026 products and the BDTRE background maps
        # are seeded; yearly 1:10,000 B/W editions 2014-2025 exist at
        # wmts_regp_basecarto10bn_<year>.xml. Source page:
        # igr.piemonte.it/scheda-informativa/allestimenti-cartografici
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geomap.reteunitaria.piemonte.it/WEBCAT/CAPABILITIES/wmts_regp_basecarto10bn_2026.xml",
            extra={
                "role": "Base map 1:10,000 B/W 2026",
                "capabilities_url_is_already_static": "true",
            },
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geomap.reteunitaria.piemonte.it/WEBCAT/CAPABILITIES/wmts_regp_basecarto25col_2026.xml",
            extra={
                "role": "Base map 1:25,000 colour 2026",
                "capabilities_url_is_already_static": "true",
            },
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geomap.reteunitaria.piemonte.it/WEBCAT/CAPABILITIES/wmts_regp_basecarto50col_2026.xml",
            extra={
                "role": "Base map 1:50,000 colour 2026",
                "capabilities_url_is_already_static": "true",
            },
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geomap.reteunitaria.piemonte.it/WEBCAT/CAPABILITIES/wmts_regp_basecarto50_geol_3col_2026.xml",
            extra={
                "role": "Base map 1:50,000 3-colour 2026",
                "capabilities_url_is_already_static": "true",
            },
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geomap.reteunitaria.piemonte.it/WEBCAT/CAPABILITIES/wmts_regp_basecarto250col_2026.xml",
            extra={
                "role": "Base map 1:250,000 colour 2026",
                "capabilities_url_is_already_static": "true",
            },
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geomap.reteunitaria.piemonte.it/WEBCAT/CAPABILITIES/wmts_regp_sfondo_bdtre.xml",
            extra={
                "role": "BDTRE background, colour (EPSG:32632)",
                "capabilities_url_is_already_static": "true",
            },
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geomap.reteunitaria.piemonte.it/WEBCAT/CAPABILITIES/wmts_regp_sfondo_bdtre_epsg3857.xml",
            extra={
                "role": "BDTRE background, colour (EPSG:3857)",
                "capabilities_url_is_already_static": "true",
            },
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geomap.reteunitaria.piemonte.it/WEBCAT/CAPABILITIES/wmts_regp_sfondo_bdtre_bn.xml",
            extra={
                "role": "BDTRE background, B/W (EPSG:32632)",
                "capabilities_url_is_already_static": "true",
            },
        ),
        # BDTRE: the base-data series in 11 themes, WFS + WMS each (page
        # igr.piemonte.it/scheda-informativa/dati-servizi). MapServer:
        # capabilities say WFS 1.0 but 2.0 requests work; CQL_FILTER is
        # ignored and the hit count is capped at 1,000 (paging still works
        # and QGIS loads well past 1,000 features). EPSG:32632.
        GatewayConfig(
            gateway_type="wfs",
            base_url="https://geoservices.csi.it/ms/wfs/bdtre/rp-01/bdtrewfs/bdtre_aggr",
            extra={
                "role": "BDTRE Aggregated structure",
                "cql_filter_supported": "false",
            },
        ),
        GatewayConfig(
            gateway_type="wfs",
            base_url="https://geoservices.csi.it/ms/wfs/bdtre/rp-01/bdtrewfs/bdtre_amm",
            extra={
                "role": "BDTRE Administrative boundaries",
                "cql_filter_supported": "false",
            },
        ),
        GatewayConfig(
            gateway_type="wfs",
            base_url="https://geoservices.csi.it/ms/wfs/bdtre/rp-01/bdtrewfs/bdtre_geofoto",
            extra={
                "role": "BDTRE Aerial-photo network",
                "cql_filter_supported": "false",
            },
        ),
        GatewayConfig(
            gateway_type="wfs",
            base_url="https://geoservices.csi.it/ms/wfs/bdtre/rp-01/bdtrewfs/bdtre_idro",
            extra={"role": "BDTRE Hydrography", "cql_filter_supported": "false"},
        ),
        GatewayConfig(
            gateway_type="wfs",
            base_url="https://geoservices.csi.it/ms/wfs/bdtre/rp-01/bdtrewfs/bdtre_imm",
            extra={"role": "BDTRE Buildings", "cql_filter_supported": "false"},
        ),
        GatewayConfig(
            gateway_type="wfs",
            base_url="https://geoservices.csi.it/ms/wfs/bdtre/rp-01/bdtrewfs/bdtre_oro",
            extra={
                "role": "BDTRE Relief (spot heights, breaklines)",
                "cql_filter_supported": "false",
            },
        ),
        GatewayConfig(
            gateway_type="wfs",
            base_url="https://geoservices.csi.it/ms/wfs/bdtre/rp-01/bdtrewfs/bdtre_pert",
            extra={"role": "BDTRE Related structures", "cql_filter_supported": "false"},
        ),
        GatewayConfig(
            gateway_type="wfs",
            base_url="https://geoservices.csi.it/ms/wfs/bdtre/rp-01/bdtrewfs/bdtre_serv",
            extra={"role": "BDTRE Utility networks", "cql_filter_supported": "false"},
        ),
        GatewayConfig(
            gateway_type="wfs",
            base_url="https://geoservices.csi.it/ms/wfs/bdtre/rp-01/bdtrewfs/bdtre_topo",
            extra={"role": "BDTRE Toponymy", "cql_filter_supported": "false"},
        ),
        GatewayConfig(
            gateway_type="wfs",
            base_url="https://geoservices.csi.it/ms/wfs/bdtre/rp-01/bdtrewfs/bdtre_veg",
            extra={"role": "BDTRE Vegetation", "cql_filter_supported": "false"},
        ),
        GatewayConfig(
            gateway_type="wfs",
            base_url="https://geoservices.csi.it/ms/wfs/bdtre/rp-01/bdtrewfs/bdtre_viab",
            extra={"role": "BDTRE Roads and paths", "cql_filter_supported": "false"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geoservices.csi.it/ms/wms/bdtre/rp-01/bdtrewms/bdtre_aggr",
            extra={"role": "BDTRE Aggregated structure (WMS)", "service": "wms"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geoservices.csi.it/ms/wms/bdtre/rp-01/bdtrewms/bdtre_amm",
            extra={"role": "BDTRE Administrative boundaries (WMS)", "service": "wms"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geoservices.csi.it/ms/wms/bdtre/rp-01/bdtrewms/bdtre_geofoto",
            extra={"role": "BDTRE Aerial-photo network (WMS)", "service": "wms"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geoservices.csi.it/ms/wms/bdtre/rp-01/bdtrewms/bdtre_idro",
            extra={"role": "BDTRE Hydrography (WMS)", "service": "wms"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geoservices.csi.it/ms/wms/bdtre/rp-01/bdtrewms/bdtre_imm",
            extra={"role": "BDTRE Buildings (WMS)", "service": "wms"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geoservices.csi.it/ms/wms/bdtre/rp-01/bdtrewms/bdtre_oro",
            extra={
                "role": "BDTRE Relief (spot heights, breaklines) (WMS)",
                "service": "wms",
            },
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geoservices.csi.it/ms/wms/bdtre/rp-01/bdtrewms/bdtre_pert",
            extra={"role": "BDTRE Related structures (WMS)", "service": "wms"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geoservices.csi.it/ms/wms/bdtre/rp-01/bdtrewms/bdtre_serv",
            extra={"role": "BDTRE Utility networks (WMS)", "service": "wms"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geoservices.csi.it/ms/wms/bdtre/rp-01/bdtrewms/bdtre_topo",
            extra={"role": "BDTRE Toponymy (WMS)", "service": "wms"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geoservices.csi.it/ms/wms/bdtre/rp-01/bdtrewms/bdtre_veg",
            extra={"role": "BDTRE Vegetation (WMS)", "service": "wms"},
        ),
        GatewayConfig(
            gateway_type="wmts_wms",
            base_url="https://geoservices.csi.it/ms/wms/bdtre/rp-01/bdtrewms/bdtre_viab",
            extra={"role": "BDTRE Roads and paths (WMS)", "service": "wms"},
        ),
    ],
)


def all_seed_sources():
    """Every bundled default source config. A function rather than a
    module-level list, so future seed data can be assembled conditionally
    without changing this module's public shape."""
    return [
        IGN_FRANCE,
        SWISSTOPO_CH,
        GEODIENSTE_CH,
        NORWAY_GEONORGE,
        BKG_DE,
        NETHERLANDS_PDOK,
        BASEMAP_AT,
        PIEMONTE_ARPA,
        INRAE_AVALANCHES,
        FVG_IT,
        NVE_NO,
        VDA_IT,
        REGIONE_PIEMONTE,
    ]
