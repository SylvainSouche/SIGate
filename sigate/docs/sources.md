# SIGate — Included Backends and the Content They Serve

What is bundled in `sources/seed.py`, backend by backend: the endpoint, which SIGate tab uses it, whether it needs a key, and — the part the code alone doesn't tell you — what it actually serves.

**How to read the counts.** Every number below comes from running SIGate's own gateway parsers (`gateways/wmts_wms.py`, `wfs.py`, `atom.py`, `stac.py`, `geonorge_catalog.py`) against the live endpoints on **2026-09-21**. Servers add and retire layers, so treat counts as a snapshot, not a contract. Anything not re-fetched that day is marked as coming from the seed file's own notes.

Sources that were researched but are *not* bundled live in [`candidate_sources.md`](candidate_sources.md); this file covers only what ships.

---

## At a glance

| Source (`key`) | Country | Gateways | Tab(s) | Auth | State on 2026-09-21 |
|---|---|---|---|---|---|
| IGN (`ign_fr`) | France | Atom, WFS, WMTS public, WMTS private, CSW | Bulk Download, WFS, WM(T)S | Private WMTS: `apikey` header (shared key, seeded) | Working. LiDAR HD now via `IGNF_LIDAR-HD_METADONNEE:metadata` (old `:dalle` layers retired); CSW configured but unused |
| swisstopo (`swisstopo_ch`) | Switzerland | WMTS, WFS, STAC ×12 | WM(T)S, WFS, Bulk Download | None | WMTS and STAC working. **WFS host does not resolve** |
| Gefahrenkarten (`geodienste_ch`) | Switzerland | "WM(T)S", WFS ×3, STAC | WM(T)S, WFS, Bulk Download | None | WFS (hazard maps + two Naturereigniskataster) and STAC working; the WFS filter panel does not narrow the query grid on this MapServer. **WM(T)S entry lists no layers** (server is WMS-only) |
| Geonorge kartkatalog (`geonorge_no`) | Norway | Geonorge catalog (over Atom) | Bulk Download | None | Working |
| BKG (`bkg_de`) | Germany | WMTS, WFS | WM(T)S, WFS | None | Working |
| PDOK (`pdok_nl`) | Netherlands | WMTS | WM(T)S | None | Working |
| basemap.at (`basemap_at`) | Austria | WMTS | WM(T)S | None | Working |
| Arpa Piemonte SIVA (`piemonte_arpa`) | Italy | ArcGIS REST, WMS | ArcGIS REST, WM(T)S | None | Working (live-checked 2026-10-06) |
| INRAE avalanches (`inrae_avalanches`) | France | WFS | WFS | None | Working; CLPA stand-in (live-checked 2026-10-06) |
| Friuli Venezia Giulia (`fvg_it`) | Italy | WFS, WMS | WFS, WM(T)S | None | Working (live-checked 2026-10-06) |
| NVE Skredhendelser (`nve_no`) | Norway | ArcGIS REST, WMS | ArcGIS REST, WM(T)S | None | Working (live-checked 2026-10-06) |
| Valle d'Aosta avalanche cadastre (`vda_it`) | Italy | WMS | WM(T)S | None | Working (live-checked 2026-10-06) |

The four tabs each accept certain gateway types: **ArcGIS REST** takes ArcGIS REST MapServer/FeatureServer services; **Bulk Download** takes Atom, STAC and the Geonorge catalog; **WFS** takes WFS; **WM(T)S** takes WMTS and plain WMS (a connection marked `service=wms` is read as WMS; an unmarked one tries WMTS first and falls back to WMS). Export as GeoTIFF is WMTS-only.

---

## France — IGN (`ign_fr`)

IGN's Géoplateforme, one host (`data.geopf.fr`) exposing several services.

### Atom download — Bulk Download tab
- Endpoint: `https://data.geopf.fr/telechargement/capabilities` · no auth
- **116 top-level products** (3 pages of 50). Each product is a folder → editions → downloadable files (`.zip`, `.7z`, split `.7z.001…`), with checksums where the entry links a `.md5`.
- Seen on the first page: ADMIN EXPRESS (COG, COG CARTO, CARTOPLUS variants), ADRESSE PREMIUM, BAN PLUS, BD ALTI, BD CARTO (and its État-major edition), BD FORET, BD ORTHO (and Historique), BD PARCELLAIRE, BD TOPO (plus Différentiel, EXPRESS, Historique), CORINE Land Cover, COSIA, Courbes de niveau, EuroGlobalMap, GEOFLA, France RELIEF, Contours IRIS, Cartes anciennes, geodesy/levelling data. Pages 2–3 were not inspected for this list.
- Archives follow IGN's numbered `{N}_{ROLE}_LIVRAISON` layout (DONNEES / METADONNEES / SUPPLEMENTS); see `spec.md` §4.3.

### WFS — WFS tab
- Endpoint: `https://data.geopf.fr/wfs/ows` · no auth · GeoServer-backed
- **806 feature types**. Main families (count of feature types):

| Family | Count | What it is |
|---|---|---|
| `ADMINEXPRESS-COG`, `-CARTO`, `-CARTO-PE` | 123 / 18 / 48 | Administrative boundaries, one set per edition (2021 … 2026, `.LATEST`) |
| `BDTOPO_V3` | 55 | BD TOPO topographic vector database (roads, buildings, hydrography, vegetation, paths such as `itineraire_autre`, …) |
| `BDTOPO_V3_DIFF` | 50 | BD TOPO change layers |
| `BDCARTO_V5`, `BDCARTO_ETAT-MAJOR` | 36 / 22 | BD CARTO, and the historical État-major map data |
| `LANDCOVER`, `RPG` | 23 / 23 | Land cover; agricultural parcels (RPG) |
| `LIMITES_ADMINISTRATIVES_EXPRESS` | 17 | Continuously updated boundaries |
| `wfs_du`, `wfs_sup`, `wfs_scot` | 17 / 10 / 4 | Urban-planning documents (communes, *documents d'urbanisme*), public-utility easements (*servitudes d'utilité publique*), SCoT planning |
| `CADASTRALPARCELS`, `ORTHOIMAGERY` | 12 / 14 | Cadastral parcels (PCI Vecteur) and cadastre mosaic graphs; orthophoto mosaic graphs (which source image is used where, for BDORTHO, historical series and SPOT satellite mosaics) |
| `IGNF_GEODESIE`, `IGNF_LIDAR-HD_METADONNEE` | 7 / 1 | Geodesy; LiDAR HD metadata |

  The list also carries per-department OCS GE layers, flood-warning layers (`VIGICRUES*`), and a number of test or obsolete layers (`test_*`, `ZZZ_*`, titles marked "(obsolete)").
- **LiDAR HD tile index — `IGNF_LIDAR-HD_METADONNEE:metadata`** (507,791 tiles on 2026-09-21; EPSG:4326, geometry field `geom`). One feature per 1 km tile, with four download links: `url_mnt` (terrain model), `url_mns` (surface model), `url_mnh` (canopy height model) — each a WMS GetMap link returning a 1 km, 0.5 m GeoTIFF whose name is in the link's `FILENAME=` — and `url_npl`, a `.copc.laz` point cloud (about 340 MB for a mainland tile). Also carries `capteur`, `code_mission`, acquisition dates, `nombre_points`, `procede_classement`, and the height/planimetric systems. The WFS tab downloads from it (product choice dialog; see `user_guide.md`). IGN staff's guidance in the LiDAR HD user workgroup chat (informal, not published documentation): use only this layer; the vector-tile feed exists only to draw the cartes.gouv.fr download interface; speak of *dalles* (1 km tiles with their metadata) rather than *blocs*.
- **Retired: the LiDAR HD `…:dalle` layers** (`IGNF_NUAGES-DE-POINTS-LIDAR-HD:dalle`, `IGNF_MNS-LIDAR-HD:dalle`, `IGNF_MNT-LIDAR-HD:dalle`, `IGNF_MNH-LIDAR-HD:dalle`). Absent from `GetCapabilities`; requesting one returns "Unknown namespace". They had one `url` per feature. Still available on the Atom side (Bulk Download tab): `LiDARHD-NUALID` point clouds (224 entries), `MNS Correl`, `RGE ALTI`.

### WMTS, public — WM(T)S tab
- Endpoint: `https://data.geopf.fr/wmts` · no auth
- **710 layers** in EPSG:3857, EPSG:2154 (Lambert-93) and IGNF:WGS84G. Largest families:

| Prefix | Count | Content |
|---|---|---|
| `ORTHOIMAGERY` | 174 | Current orthophotos (`ORTHOIMAGERY.ORTHOPHOTOS`, `.BDORTHO`), false-colour infrared (`.IRC`, `.IRC-EXPRESS.2023–2026`), historical aerial series (`1950-1965`, `1965-1980`, `1980-1995`), regional/thematic sets |
| `GEOGRAPHICALGRIDSYSTEMS` | 68 | Mostly historical and educational maps: EDUGEO regional maps (1950s–90s), a 1906 Paris topographic map, First World War front maps, coastal maps. Plan IGN sits under its own `PLANIGN` prefix (2 layers); the SCAN 25 maps are on the private endpoint below |
| `LANDCOVER`, `LANDUSE`, `OCSGE` | 67 / 19 / 19 | Land cover and land use, including OCS GE |
| `ELEVATION` | 10 | `CONTOUR.LINE`, `ELEVATIONGRIDCOVERAGE` (+ `.HIGHRES`, `.HIGHRES.MNS`, `.SHADOW` hillshade, `.SRTM3`, `.THRESHOLD`), `LEVEL0`, `SLOPES`, `SLOPES.HIGHRES` |
| `INSEE`, `ADMINEXPRESS-COG`, `HYDROGRAPHY`, `CADASTRALPARCELS`, `TRANSPORTNETWORKS` | 20 / 11 / 9 / 7 / 4 | Statistics units, boundaries, hydrography, cadastre, transport |
| energy, forestry and other thematic sets | remainder | `POTENTIEL`, `ENREZO`, `SECUROUTE`, `ADEME_*` hedgerow layers, … |

### WMTS, private — WM(T)S tab
- Endpoint: `https://data.geopf.fr/private/wmts` · header `apikey: ign_scan_ws`
- The key is IGN's published shared key for this endpoint, seeded in `sources/seed.py` on purpose; SIGate provisions a QGIS authentication configuration for it (`ui/authcfg.py`).
- **4 layers**, EPSG:3857 and `IGNF:LAMB93`: `GEOGRAPHICALGRIDSYSTEMS.MAPS` (classic IGN maps), `…MAPS.SCAN-OACI` (aeronautical VFR chart), `…MAPS.SCAN25TOUR` (TOPO 25 / SCAN 25), `…MAPS.SCAN25TOUR.L93` (SCAN 25 Touristique, Lambert-93 — the layer the GeoTIFF export was built around).

### CSW — configured, not used
- `https://data.geopf.fr/csw` is in the config so it isn't lost, but no gateway module implements CSW and the records seen lacked usable resource links.

---

## Switzerland — swisstopo (`swisstopo_ch`)

The seed treats this as the shared **federal platform** (`data.geo.admin.ch`), so it also carries data authored by other federal offices (ASTRA, BAFU, …).

### WMTS — WM(T)S tab
- Endpoint: `https://wmts.geo.admin.ch/1.0.0/WMTSCapabilities.xml` · no auth
- **688 layers**, from about 24 data providers: BAFU 291, swisstopo 136, BAKOM 45, BLW 36, BFE 31, ASTRA 24, ARE 21, BAZL 20, BFS 16, VBS 14, BAV 12, MeteoSchweiz 12, Agroscope 10, and a long tail.
- Useful ones for outdoor and terrain work: `ch.swisstopo.pixelkarte-farbe` (national map; also `-winter`, `-grau`, and fixed scales PK25 to PK1000), `ch.swisstopo.swissimage` (orthophoto; plus a 1946 set), `ch.swisstopo.swissalti3d-reliefschattierung` (hillshade), `ch.swisstopo.hangneigung-ueber_30` (slopes above 30°), `ch.swisstopo-karto.skitouren`, `ch.bafu.silvaprotect-lawinen` (SilvaProtect avalanche layer), `ch.swisstopo.swisstlm3d-wanderwege` (hiking trails).
- **CRS caveat:** the default capabilities document declares only the legacy **CH1903 / EPSG:21781** tile matrix set. Newer LV95 sets live under a different URL path that the seed does not use.

### WFS — WFS tab
- Endpoint: `https://wfs.geo.admin.ch`
- **Not usable: the hostname does not resolve** (no DNS record, checked 2026-09-21). The seed already flagged this endpoint as weakly evidenced (`endpoint_confidence: weak_dated_reference_only`). Nothing is served.

### STAC — Bulk Download tab
- Root: `https://data.geo.admin.ch/api/stac/v1` · no auth · **12 gateway instances**, each scoped to one collection. Each collection's items are tiles or features you can download; STAC carries a checksum inline, which SIGate verifies (sha256).
- All collections report the STAC license value `proprietary`, which STAC uses for "see the dataset's own terms".

| Role in the connection list | Collection id | What it is |
|---|---|---|
| swissALTI3D | `ch.swisstopo.swissalti3d` | Very precise digital elevation model (terrain without vegetation or buildings), Switzerland and Liechtenstein, 6-year update cycle |
| swissTLM3D | `ch.swisstopo.swisstlm3d` | Large-scale 3D topographic landscape model (vector: natural and man-made features, names) |
| swissSURFACE3D | `ch.swisstopo.swisssurface3d` | Digital surface model (DSM) in 1 km² tiles |
| SWISSIMAGE | `ch.swisstopo.swissimage-dop10` | Orthophoto mosaic, 10 cm in lowlands and main valleys, 25 cm over the Alps, 3-year cycle |
| ski touring routes | `ch.swisstopo-karto.skitouren` | Digital ski routes, produced with the Swiss Alpine Club, updated annually |
| snowshoe routes | `ch.swisstopo-karto.schneeschuhrouten` | Snowshoe routes. The server publishes the **same title and description as the ski routes** ("Ski routes"), so the two entries look alike in metadata |
| hiking network (Wanderland) | `ch.astra.wanderland` | SchweizMobil national, regional and local hiking routes, incl. barrier-free trails |
| winter hiking trails | `ch.astra.winterwanderwege` | Signposted winter hiking trails |
| snowshoe trails (ASTRA) | `ch.astra.schneeschuhwanderwege` | Snowshoe trails; may overlap the swisstopo snowshoe routes above |
| cycling network (Veloland) | `ch.astra.veloland` | National, regional and local cycling routes |
| mountain bike network | `ch.astra.mountainbikeland` | Mountain-bike routes |
| skating network | `ch.astra.skatingland` | Inline-skating routes |

**Requested but not available** (from the seed's notes): SAC hut data — swisstopo states partner POI data is "neither entered nor managed by swisstopo", and no downloadable dataset was found. SLF's live avalanche bulletin is a separate API with no gateway type here.

---

## Switzerland — Gefahrenkarten, geodienste.ch (`geodienste_ch`)

The **intercantonal** geodata portal: cantonal hazard maps harmonised under one national schema. Added for avalanche hazard zones; it also covers flood, landslide, rockfall and subsidence. Per the seed's research, avalanche coverage reaches about 98% of the area that needs mapping (not re-checked today).

### "WM(T)S" — WM(T)S tab
- Endpoint: `https://geodienste.ch/db/gefahrenkarten_v1_3_0/deu` · no auth · **plain WMS 1.3.0** (`service=wms` in the seed): 51 layers, including `gefahrenhinweisgebiet_lawine`, the legally binding `gefahrengebiet_lawine`, and `intensitaet_lawine_*` by return period. Layers are offered in EPSG:3857, 4326 or 2056.
- Also WMS: `https://geodienste.ch/db/naturereigniskataster_v1_0_0/deu` (the Naturereigniskataster, 21 layers; same URL as its WFS below).

### WFS — WFS tab
- Endpoint: `https://wfs.geodienste.ch/gefahrenkarten_v1_3_0/deu` · no auth
- **40 feature types**, all in the `ms:` namespace: indicative hazard-hint areas (`gefahrenhinweisgebiet_*`) and authoritative hazard zones (`gefahrengebiet_*`) for water, landslide (`rutschung`), rockfall (`sturz`) and avalanche (`lawine`); intensity layers by return period (0–30, 30–100, 100–300 years, extreme event) for water, sudden and permanent landslides, rockfall, avalanche and subsidence; flood depth and velocity; debris-flow height and velocity; survey areas.
- Names are the server's own, typos included (for example `gefahrengebiet_ruschtung`, and a `…_300_100_jahre` intensity layer).

### WFS — Naturereigniskataster (WFS tab, two connections)
The cantons' register of past natural events - floods, debris flows, landslides, rockfall and **recorded avalanches** (`prozessraum_lawine`).
- Base dataset: `https://geodienste.ch/db/naturereigniskataster_v1_0_0/deu` · 13 feature types, EPSG:2056 (LV95): `sammelereignis`, `basisinformationen` and one `prozessraum_*` per process (water ×4, `rutschung`, `sturz`, `lawine`, `einsturz`, `absenkung`, `anderer_prozess`...). `prozessraum_lawine` holds 28,066 features.
- Comprehensive dataset: `https://geodienste.ch/db/naturereigniskataster_umfassend_v1_0_0/deu` · 16 feature types, the same plus point, line and area observations (`beobachtung_*`).
- No auth. **The server ignores CQL filters** (MapServer): an impossible `CQL_FILTER` still returned the full count. The Filter panel therefore does not narrow the query grid on these (nor on the Gefahrenkarten WFS above); "Add to map" is unaffected, since QGIS translates the expression itself.

### STAC — Bulk Download tab
- Root: `https://www.geodienste.ch/stac` · collection `gefahrenkarten` ("Gefahrenkarten", extent covers Switzerland)
- One item per canton (for example `gefahrenkarten_v1_3-AG`). The collection metadata was fetched today; the per-canton items URL shape is inferred (`items_url_inferred_not_fetched` in the seed).

---

## Norway — Geonorge kartkatalog (`geonorge_no`)

- Endpoint: `https://kartkatalog.geonorge.no/api/search?limit=10000&text=&mediatype=csv` · no auth · Bulk Download tab
- The source is a flat catalog with no tree, so `gateways/geonorge_catalog.py` builds one: **Theme → Dataset**, from a single CSV export of the whole catalog. Once you open a dataset, you are browsing its own INSPIRE Atom feed exactly like IGN's.
- **18 themes** today: Annen, Basis geodata, Befolkning, Eiendom, Energi, Flyfoto, Forurensning, Friluftsliv, Geologi, Høydedata, Kulturminner, Kyst og fiskeri, Landbruk, Landskap, Natur, Plan, Samferdsel, Samfunnssikkerhet.
- **Only a small part of the catalog appears.** Just records that carry an Atom feed are listed; the seed's own figure is roughly 400 of 8,637 records. WMS/WFS-only services and REST APIs are not shown here.

---

## Germany — BKG (`bkg_de`)

Licence for both services: *Datenlizenz Deutschland – Namensnennung 2.0* (attribution, free, no key).

- **WMTS — TopPlusOpen.** `https://sgx.geodatenzentrum.de/wmts_topplus_open/1.0.0/WMTSCapabilities.xml` · 6 layers, EPSG:3857: `web`, `web_grau`, `web_scale`, `web_scale_grau`, `web_light`, `web_light_grau` (colour, grey, upscale and light variants of the national base map).
- **WFS — VG250 only.** `https://sgx.geodatenzentrum.de/wfs_vg250` · 8 feature types: Staat, Bundesland, Regierungsbezirk, Kreis, Verwaltungsgebiete, Gemeinden, Grenzlinien, Gemeindepunkte (administrative boundaries at 1:250 000). BKG has no single WFS; this is one dataset, and its other datasets sit at their own `wfs_*` endpoints that are not seeded.

## Netherlands — PDOK (`pdok_nl`)

- **WMTS — BRT Achtergrondkaart.** `https://service.pdok.nl/brt/achtergrondkaart/wmts/v2_0/WMTSCapabilities.xml` · 5 layers, EPSG:28992 (RD): `standaard`, `grijs`, `pastel`, `water`, `labels`. Licence CC-BY-4.0 (from the seed).
- PDOK's Atom downloads and its OGC API vector tiles are not integrated.

## Austria — basemap.at (`basemap_at`)

- **WMTS.** `https://basemap.at/wmts/1.0.0/WMTSCapabilities.xml` · 7 layers, EPSG:3857: `geolandbasemap`, `bmapoverlay`, `bmapgrau`, `bmaphidpi`, `bmaporthofoto30cm`, `bmapgelaende` (terrain shading), `bmapoberflaeche` (surface). Free, no key; CC-BY-4.0 (from the seed). No WFS exists for it.
- The BEV INSPIRE services are deliberately not included: they need a manual, form-based registration (see `candidate_sources.md`).

## Italy — Arpa Piemonte SIVA (`piemonte_arpa`)

Piemonte's regional environmental agency; the avalanche information system (SIVA).

### ArcGIS REST — ArcGIS REST tab
- Service: `https://webgis.arpa.piemonte.it/server/rest/services/rischi_naturali/SIVA/MapServer` · no auth
- 21 layers including group layers; **14 can be queried** (the rest are groups). The service also has a WMS but **no WFS**, which is why this source needs the ArcGIS REST tab.
- **WMS** (WM(T)S tab, drawn map only): `https://webgis.arpa.piemonte.it/server/services/rischi_naturali/SIVA/MapServer/WMSServer` · 14 layers.
- Queries use a SQL `where` clause and an optional bounding box; one page is capped by the server's `maxRecordCount`. The tab hands "Add to map" to QGIS's own `arcgisfeatureserver` provider, with the filter passed as `sql=`.

## France — INRAE avalanches (`inrae_avalanches`)

INRAE's own GeoServer: the CLPA (avalanche location map) and the EPA avalanche events survey.

### WFS — WFS tab
- Endpoint: `https://carto-service.inrae.fr/geoserver/siavalanches/wfs` · no auth · CQL filters work.
- 38 feature types, in EPSG:4326, 3857 or 2154 depending on the layer. `clpa_zonpi` (50,089 probable-avalanche zones), `clpa_linpi` (26,946 lines), `clpa_zont` / `clpa_lint` (the same for the "T" series), `epa_*` (survey sites and events), `bda_*` and `massifs` (communes, departments, mountain massifs) and many `matvue_*` / `vue_*` database views.
- This is the stand-in for the CLPA layers that were served by the IGN Géoplateforme before the geoportail → cartes.gouv.fr migration; they are expected to come back.
- Its **WMS capabilities hang** at the workspace level (GetMap works), so there is no WMS route.

## Italy — Friuli Venezia Giulia (`fvg_it`)

### WFS — WFS tab
- Endpoint: `https://serviziogc.regione.fvg.it/geoserver/ZONE_RISC/wfs` · no auth · CQL filters work.
- 14 feature types: avalanches (`CV_VALANGHE_RILEVATE` surveyed, 3,875 features; `CV_VALANGHE_FOTOINT` photo-interpreted), avalanche danger zones (`CV_ZONE_PERIC_*`), probable avalanche tracks (`VAL_POS_CONT_*`), local hazard (`CV_PERICOLO_LOCAL_*`), plus wildfire layers (`SITFOR_PERICOLO_INCENDI`, `SUPERFICIE_*_BRUCIATA`, `V_INCENDI_*`).
- Feature types declare EPSG:6708 as their default CRS.
- **WMS** (WM(T)S tab): `https://serviziogc.regione.fvg.it/geoserver/ZONE_RISC/wms` · 14 layers.

## Norway — NVE Skredhendelser (`nve_no`)

### ArcGIS REST — ArcGIS REST tab
- Service: `https://kart.nve.no/enterprise/rest/services/Skredhendelser1/MapServer` · no auth
- 10 layers, EPSG:25833: Skredtype, release and run-out points and areas (`Skredhendelse_Utlosnings*`, `Skredhendelse_Utlops*`), fatalities, consequences (transport, forest/agriculture, buildings) and snow avalanches. Layer 9 holds 26,218 records. Page size capped at 2,000.
- **WMS** (WM(T)S tab): `https://kart.nve.no/enterprise/services/Skredhendelser1/MapServer/WMSServer` · 10 layers. There is no WFS.

## Italy — Valle d'Aosta (`vda_it`)

### WMS — WM(T)S tab
- Endpoint: `https://servizisct.regione.vda.it/ows/public/CatastoValanghe` · no auth · WMS 1.3.0, 34 layers: the avalanche cadastre (Catasto Valanghe) and snow-gauge poles (Paline nivometriche). No WFS found.

### Known but not yet usable
- Catalonia (ICGC) avalanche data: WMS-only, endpoint not seeded.

---

## Keeping this document current

`sources/seed.py` is the source of truth for *what is configured*. This file adds *what is served*, which only a live fetch can show, so counts drift.

A test (`tests/test_sources.py`) checks that every seeded source key and every gateway URL appears here, so adding or changing a source without updating this document fails the pure-Python suite. It cannot check the counts and descriptions; refresh those by running the plugin's parsers against each endpoint, as this snapshot was made.
