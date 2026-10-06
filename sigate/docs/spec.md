# SIGate (Spatialized Infrastructure Gateway) — QGIS Plugin Spec

Functional spec for **SIGate**, a QGIS plugin exposing multi-country geodata discovery (originally built and tested as the standalone `atom_explorer_gui.py`/`gis.py`/`fetch_zone_tile.py` tools, IGN-focused) as new categories in QGIS's native Data Source Manager, rather than a standalone plugin window.

**Status: implemented, packaged, and iterated on real reported behavior** — three tabs (WFS, WM(T)S, Bulk Listing) registered and working against real IGN endpoints, `metadata.txt` at `0.0.33` under this project's own build-counter versioning convention (see `dev_workflow.md`'s coding rules). This spec describes the current, real architecture; `dev_workflow.md` is the detailed, append-only history of every fix and feature addition, in the order they actually happened — read that alongside this document for *why* something is shaped the way it is, not just *what* it is. License: BSD-3-Clause.

---

## 1. Integration mechanism (verified — live-tested, not just documented)

QGIS's Data Source Manager supports third-party categories via a documented, stable (since QGIS 3.10) extension point. Confirmed against real, current QGIS core source (`qgslayermetadatasourceselectprovider.cpp`, itself an in-tree example of this exact pattern), **and confirmed live** in a headless QGIS 3.34 install (Ubuntu 24.04's own repo package, tested with `QT_QPA_PLATFORM=offscreen`): three real `QgsSourceSelectProvider` subclasses are registered, found by key, and their widgets correctly returned by `registry.createSelectionWidget()` — the exact call QGIS's own Data Source Manager dialog makes internally. Full mechanics:

- Subclass `QgsSourceSelectProvider`, implementing `providerKey()` (globally-unique string, namespaced `sigate_wfs`/`sigate_wmts_wms`/`sigate_bulk_listing`, matching QGIS core's own lowercase/plain convention), `text()`, `toolTip()`, `icon()`, `ordering()`, `createDataSourceWidget(parent, fl, widgetMode)`.
- Subclass `QgsAbstractDataSourceWidget` for the actual UI content; emits signals (`addVectorLayer`, `addRasterLayer`, etc.) to tell QGIS to add a layer.
- Registered in `plugin.py`'s `initGui()` via `QgsGui.sourceSelectProviderRegistry().addProvider(...)`; unregistered in `unload()` via `removeProvider(...)` — confirmed by a real test that the registry's provider count returns to its exact pre-load value after unload.

Pure fetch/parse/pipeline logic lives entirely under `gateways/` and `download/`, with no Qt dependency at all — tested directly, without a live QGIS install, the same way the original standalone tools' functions were. Only `ui/` is Qt/QGIS-dependent.

---

## 2. Tab architecture: three tabs, one shared connection-management component

Five gateway types were originally identified (WFS, Atom, FTP, WM(T)S, STAC), grouped into three tabs by shared shape rather than built as five separate ones:

| Tab | Gateway types covered | Why grouped / kept separate |
|---|---|---|
| **Bulk Listing** | Atom, STAC, `geonorge_catalog` (FTP not yet implemented) | Shares the "list entries at a URL, drill into folders, download a file" shape with the download pipeline (§4) doing the heavy lifting generically. STAC's Collection→Item→Asset hierarchy maps onto this same tree shape (a Collection's Items page is a directory listing, each Item a sub-directory containing its Assets) - see `gateways/stac.py`'s own module docstring for the detailed mapping and the real divergences (link-based pagination, inline checksum) that needed handling rather than assuming Atom's shape universally. `geonorge_catalog` is a different kind of fit entirely: Norway's `kartkatalog.geonorge.no` is natively a flat search-and-facets catalog with no tree at all, but `gateways/geonorge_catalog.py` derives a real two-level Theme→Dataset hierarchy from one CSV export of the whole catalog, then delegates wholesale to `gateways/atom.py` the moment a dataset's own real Atom feed URL is reached - "this source shape doesn't fit the tab" turned out to describe the source's native shape, not a permanent limit on what could be built on top of it. |
| **WFS** | WFS | Standalone — its distinguishing capability (server-side attribute query via a real query-builder panel, §3) has no equivalent in the others. |
| **WM(T)S** | WMS, WMTS | Standalone: its primary action is a *handoff* to QGIS's own native "wms" provider (browse-and-handoff, not a reimplementation) — see §3 — plus a second, genuinely different action, "Export clipped area as GeoTIFF" (§3, §4). |

**Shared across all three**: `ui/connection_manager.py`'s `ConnectionManager` — a combo box (New/Edit/Delete/Load/Save, matching QGIS's own convention for WMS/WFS/PostGIS connections) backed by `sources/store.py`'s persistence. Lists **one entry per gateway *instance*, not one per source** — a real, reported bug fixed during development: a source declaring more than one instance of a tab's gateway type (confirmed real: IGN's WM(T)S gateway has a separate public and apikey-gated private instance) used to collapse into a single entry with every instance's results silently merged; it now shows one distinct, individually-selectable entry per instance (`"IGN (France) — public"` / `"IGN (France) — private"`), while a source with only one instance (every seeded WFS/Bulk Listing source today) is completely unaffected — one plain entry, exactly as before.

**Open, deferred (§7)**: this tab architecture implicitly assumes one endpoint exposes many layers/collections (confirmed true for IGN). Some countries' WFS layer may instead be many endpoints with few layers each (203 separate WFS endpoints found in a real Norwegian Kartkatalogen export) — a structurally different discovery problem that may need an aggregation step in front of the WFS tab. Not yet confirmed as the dominant pattern; no France-specific data suggests it applies there.

---

## 3. Final action per gateway type

| Gateway / use pattern | Final action |
|---|---|
| **Atom** (Bulk Listing tab) | Full download pipeline, §4. |
| **WFS — vector-layer use** (boundaries, hydrography, etc.) | "Add to map": construct a connection URI (typename + `filter`) → `addVectorLayer` signal → QGIS's native WFS provider handles paging/geometry/CRS. Now **selection-aware**: if rows are selected in the results grid, the added layer is restricted to exactly those rows via a `field IN (...)` filter built from a best-effort-guessed identifier field (`fid`/`gml_id`/`id`/`objectid`); with nothing selected, falls back to whatever filter is currently active in the query-builder panel (or none). If a selection exists but no identifier field can be guessed, falls back to the whole-filter behavior with an explicit status message rather than silently adding more than was selected. |
| **WFS — attribute filtering** | A dedicated **Filter…** panel (`ui/query_builder_dialog.py` wrapping `ui/expression_builder.py`), opened on demand rather than permanently embedded — a real UX request. Builds a raw SQLite-syntax expression against real field names/sample values pulled from an actual query result (an in-memory `QgsVectorLayer` built from the last page fetched, since `QgsExpressionBuilderWidget.loadFieldsAndValues()` has been a documented no-op since QGIS 3.14); "Load some"/"Load all" values are always distinct (`QgsVectorLayer.uniqueValues()` deduplicates by design), differing only in count. Auto-loads a first page if the current feature type hasn't been queried yet, so the panel is never shown empty. Applied to both "Query features" (as CQL, sent as `CQL_FILTER`) and "Add to map" (as a QGIS expression sent as `filter`) — the same raw text reused for both, though a general equivalence between the two syntaxes for arbitrary expressions is unconfirmed (§7). A spatial-predicate `@map_extent` token resolves to the current map canvas extent, reprojected via `QgsCoordinateTransform` into the selected feature type's own declared CRS (`gateways/wfs.py` now parses each `FeatureType`'s real `DefaultCRS`/`DefaultSRS`, the same CRS-safety discipline already applied to the WM(T)S export feature) — genuinely wired up only after a real reported failure traced this token to having never actually been connected anywhere (§7). Querying always clears the results grid immediately, before the fetch starts, rather than only once new data arrives — a query in progress never leaves stale rows looking current. A query failure shows a message box (not just a status label) naming the real error and whether a filter was involved, since the grid staying empty after a failed *filtered* query looked identical to "the filter did nothing" before this was added. |
| **WFS — file-index use** (e.g. IGN's LiDAR HD `IGNF_LIDAR-HD_METADONNEE:metadata`: geometry is a tile footprint, payload is one or more link attributes) | Same download pipeline as Atom (§4) — each file being a "simple file" in that pipeline's terms. A column is a download link when its *values* are single `http(s)` URIs (`ui/wfs_widget.is_download_uri`) — decided by value, not name or schema type, since WFS schemas declare such columns as plain `xsd:string`. When a row carries several links (LiDAR HD: `url_mnt`/`url_mns`/`url_mnh`/`url_npl`), `ui/product_choice_dialog.py` asks which products to fetch (rasters pre-ticked, point cloud not). Filenames come from a WMS GetMap link's `FILENAME=` parameter, else the URL path. Each `DownloadItem` carries its `product`, and downloaded tiles are grouped per product before the mosaic offer so MNT/MNS/MNH are never mosaicked together. A `.copc.laz` result is added through `addPointCloudLayer` (provider `copc`). The retired `:dalle` layers (one `url` each) are gone from IGN's server; the same code still handles any single-link layer. Layer-tree placement: everything added to the project goes into groups via `ui/layer_groups.py` (`group_scope`/`emit_in_scope`) — source › layer › product for downloaded files, source › archive name for archives, source for WM(T)S/export/WFS add — the one place a future user-defined organisation module would hook in. |
| **WFS/Bulk Listing — downloads generally** | Now run on a background `QgsTask` (`ui/download_task.py` + a two-bar `ui/download_progress_dialog.py`: overall "File N of M", plus the current file's own byte progress) rather than blocking QGIS's UI thread for the whole batch — a real reported gap ("is the download launched in the background? does it manage concurrent connections?" — no to both, previously). Concurrency remains sequential, one file at a time; only the blocking behavior was fixed. Cancellable, cooperatively, between files and mid-file (chunk-granularity) for a plain download. Downloaded files land in a `source/category/layer` subdirectory structure under the central-repo target (`download.pipeline.build_download_subdirectory` — `category` is a broad raster/vector/point_cloud/other classification, not a per-product key) rather than flatly, a real reported gap ("now we d/l everything in the same directory"). Every external command this pipeline shells out to (7-Zip listing/extraction, `gdalbuildvrt`, `gdaladdo`) is logged to QGIS's own Log Messages panel under a "SIGate" tag before it runs, requested directly ("can you output all issued commands in the console?"). |
| **CSW / general catalog search** (Kartkatalogen's `servicedirectory`, etc.) | **Router only, not yet built** — search descriptions, jump to the matching Bulk Listing/WFS/WM(T)S tab pre-filtered to the identified product. IGN's own CSW, the only one tested so far, returned thin/link-less summary records — needs a richer real-world capabilities test case before finalizing (§7). |
| **WMS/WMTS — "Add to map"** | Browse-and-handoff to QGIS's own native "wms" provider (a plain connection-string build, not a reimplementation — QGIS's own dialog already reads real `GetCapabilities` and negotiates genuinely available Style/TileMatrixSet combinations). A source with an apikey-gated instance is fully supported: `ui/authcfg.py` provisions a QGIS Authentication Configuration (method `"APIHeader"`) idempotently, embedded via the standard `authcfg=` connection-string parameter (§7 for what this still needs live-verified). |
| **WMS/WMTS — "Export clipped area as GeoTIFF"** | A second, genuinely different action from the live handoff: materializes a real, standalone GeoTIFF clipped to the *current map canvas extent*, ported from the standalone `fetch_zone_tile.py` script (`gateways/wmts_export.py`, shelling out to GDAL's own CLI tools — `gdal raster clip`, `gdal_translate` — deliberately, matching the proven script rather than reimplementing via QGIS's bundled GDAL Python bindings). The canvas extent is reprojected into the selected layer's own CRS via `QgsCoordinateTransform` before clipping — a real improvement over the standalone script, which didn't do this and just warned about the risk — and that same CRS is passed *explicitly* to GDAL's own `--bbox-crs` flag rather than left as an unstated assumption that the reprojected bbox already matches whatever CRS GDAL derives for the WMTS dataset internally (checked directly against GDAL's documentation after being flagged as a real risk: "we must be careful not to mix crs"). A pre-flight size estimate (`estimate_export_size`, computed from the layer's own parsed finest resolution — real `TileMatrix`/`ScaleDenominator` data via OGC's standardized 0.28mm-pixel formula, not assumed) warns before a genuinely huge area is clipped, reusing the same size-warning threshold already configured for downloads. When a layer offers more than one real zoom level, a picker (`ui/wmts_zoom_level_dialog.py`) lets the user choose one explicitly — each level labelled with its own resolution and estimated size for the current view — rather than always using the finest; the chosen level genuinely constrains what GDAL fetches (via its own `tilematrix=` connection-string parameter), not a display estimate followed by downsampling. Runs on a background `QgsTask` with a simpler, single indeterminate-bar dialog (GDAL's CLI tools expose no per-byte progress); cooperative cancellation is weaker than the download pipeline's, only checked between the two possible GDAL steps, since GDAL's CLI tools offer no clean way to interrupt a command already running. Both the scratch clip file and the cached WMTS XML definition are written next to the user's chosen output file — not a hidden plugin/profile settings directory — since that location is the one thing already confirmed writable (the user just picked it via a real save dialog), and two real bugs (a "read only /tmp" failure, and a definition file landing somewhere unacceptable) were both traced to *not* doing this originally. |

---

## 4. Download pipeline (Bulk Listing tab: Atom and STAC, and WFS file-index downloads)

### 4.1 Central distinction: archive vs. simple file

This is a hard branch, not a preference — **an archive can never be a QGIS layer directly; extraction is structurally required**, so both a central-repo target and a destination target are inherent to that case. **A simple file has no such barrier** — it's already loadable exactly as downloaded, so the destination target is *optional*; when omitted, the central-repo location doubles as the destination, and the file is added as a layer directly from there with no copy step.

Consequence: the simple-file path is the cheapest and most common case in practice — the archive path is the only one that ever needs the full two-location machinery.

Format branching (archive vs. simple, and which archive format) is a **second, independent axis from gateway type** — an Atom or STAC source can serve either a bare raster or a zipped bundle. Gateway type only determines how a listing is parsed and whether a query panel is shown; format determines the download pipeline's branch.

### 4.2 Full pipeline

1. **Discovery** (Bulk Listing tab, or a WFS query result carrying `url`-bearing rows) — one or more entries selected (multi-select supported). **One logical entry can resolve to multiple physical files** — confirmed real for split 7z archives (`archive.7z.001`, `archive.7z.002`, ...). The pipeline treats all parts of one split archive as a single unit throughout.
2. **Target choice**, one combined choice for the whole batch, not per-entry (`ui/target_picker.py`):
   - **Central-repo target**: always required. One of: predefined (plugin-wide cache dir under the QGIS profile folder), user-defined named bookmark (persisted as JSON in the profile plugin-data folder), simple folder chooser (one-off, no persistence).
   - **Destination target**: same three kinds, **required for archives, optional for simple files**.
3. **Already-downloaded check**, per entry, against the central-repo target — matched against the *actual* nested path a file will land at (`source/category/layer/filename`, not the flat legacy path), since the check and the real download destination now share one function (`_item_dest_path`) specifically so they can never disagree. **For a split archive, this means checking that every part is present**, not just `.001` — a partial set counts as not-yet-downloaded. On full match: offer use-existing / overwrite.
4. **Size lookup + free-space + size-warning checks**, combined across the whole batch:
   - Size lookup: prefer the source's own declared length, fall back to HTTP `HEAD`/`Content-Length`, proceed without a pre-check if neither is available.
   - **Free-space check** (against the central-repo target): `shutil.disk_usage`, hard block, +5%-or-100MB safety margin.
   - **Size-warning check**: configurable threshold (default 1GB, `sigate_settings.get_size_warning_threshold_bytes()`), Abort/Continue, human-readable total.
5. **Download** each entry to the central-repo target, on a background `QgsTask` with a two-bar progress dialog, under a `source/category/layer` subdirectory structure (`download.pipeline.build_download_subdirectory` — `sanitize_path_component` keeps a source's own display name readable as a folder name, e.g. `"IGN (France)"`, only replacing characters actually illegal on some OS). Split-archive parts all download to the same location, co-located as 7z's own multi-volume convention requires — unaffected by the subdirectory structure, since every part of one split archive shares the same source/layer context and so the same subdirectory.
6. **Integrity check** per file: byte count vs. declared size; **hash verification when a checksum is actually available**, via `download.integrity.verify_hash` (a generic `(algorithm, hex_digest)` dispatcher covering both md5 and sha256, added once STAC needed a second algorithm - `DownloadItem.expected_hash`/`DownloadOutcome.hash_ok`, additive alongside the original md5-only `expected_md5`/`md5_ok` fields, which every pre-existing caller keeps using unchanged) - for a Bulk Listing Atom download, a sibling link within the same entry whose href ends in `.md5` and whose own filename corresponds to the real download link (`gateways/atom.py`'s `find_checksum_link` - a real, common sidecar-link convention, not a confirmed universal one) is fetched and parsed as md5; for a Bulk Listing STAC download, the checksum is already inline on the asset itself (`file:checksum`, a multihash - `"1220"` + 64 hex chars = sha2-256, confirmed real and live against `data.geo.admin.ch`) and needs no separate fetch, decoded by `gateways/stac.py`'s `parse_sha256_multihash`; for a WFS file-index download, a row carrying an md5/checksum-like field (`ui/wfs_widget.py`'s `md5_field_for`) is used directly as md5. Verified end-to-end, not merely supported in principle: `download.pipeline.download_items` only ever calls a hash check when one of these paths actually supplied a real hash to check against. Archive-specific test (`zipfile.testzip()`, `7z t`) where applicable. On any integrity-check failure: clear message naming which check failed, delete the bad file, offer retry.
7. **Branch on format** (independent of gateway type, per §4.1):
   - **Archive** → list contents (`zipfile`/`tarfile`/`7z l -slt`, always pointed at `.001` for a split archive) → filter out non-data artifacts (macOS `._*`, `.DS_Store`) → extraction checklist, config-driven pre-selection (§4.3) + interactive confirmation → free-space check against the destination → extract (`7z x`, again at `.001`) → post-extraction handling (§4.4) → add layer(s) from destination.
   - **Simple file, destination given** → copy/move to destination → add layer from destination.
   - **Simple file, no destination** → add layer directly from the central-repo location.

Every external command any of these steps shells out to (`7z`, `gdalbuildvrt`, `gdaladdo`) is logged to QGIS's own Log Messages panel (tag "SIGate") immediately before it runs.

### 4.3 Extraction structure config (archives only)

**Confirmed against real extracted IGN archives** (orthophoto and RGE ALTI deliveries) — IGN uses a consistent, numbered packaging convention: `.../{N}_{ROLE}_LIVRAISON_{delivery-id}/...`, `N`/`ROLE` = `1`/`DONNEES`, `2`/`METADONNEES`, `3`/`SUPPLEMENTS`, plus a top-level readme outside the numbered folders. Treated as a **universal default rule**, checked before any per-product lookup. Detection is a **recursive scan for the `{N}_{ROLE}_LIVRAISON` naming pattern anywhere in the tree** — the folders' depth varies by product (orthophoto at archive root, RGE ALTI one level deeper under a product-name subfolder).

**Fallback order, at the classification layer** (`download/extraction_structure.py`, real and implemented): (1) universal numbered-folder rule → (2) a saved per-product-key override, re-applying the same folder-membership *pattern* rather than the exact saved paths (bundled seed + a user-profile JSON override file) → (3) a heuristic keyword-based fallback (`DATA`/`DONNEES` folder-name matching → `ROLE_DATA`, `DOC`/`METADATA`/`METADONNEES` → `ROLE_METADATA`, anything else → `ROLE_UNCLASSIFIED`).

**What the UI (`ui/download_flow.py`) actually does with this classification**: every member across all four roles is extracted — requested directly ("keep all") after this project's own earlier, narrower behavior (extracting only `DONNEES`, silently discarding `METADONNEES`/`SUPPLEMENTS`) was found to not match either what was intended or what this section itself already claimed. Confirmed once yes/no (not an interactive per-file checklist — none currently exists in the UI, despite an earlier version of this section describing one) before extracting. `save_product_override` (the mechanism for persisting a new per-product classification as a config entry) exists at the classification layer but is not currently called from anywhere in the UI — there's no way to actually save a new override through the interface yet, only the bundled seed seeded ahead of time.

### 4.4 Post-extraction handling

- **Extracted into a subfolder named after the archive itself** (`download/archive.py`'s `archive_base_name`, correctly stripping a split 7z part's own two-suffix naming, e.g. `RGEALTI_D073.7z.001` → `RGEALTI_D073`, not just its last segment) — requested directly, so extracting more than one archive into the same destination can't mix their contents together, and every extracted file stays traceable back to its source archive by folder name alone.
- **VRT mosaic offer, with pyramids, covering both delivery shapes.** If a batch of raster files looks like a same-format/same-resolution tileable set, a single `gdalbuildvrt` mosaic is offered (`download/mosaic.py`) rather than defaulting to every tile as its own layer. Runs for **both** ways tiles can arrive: extracted from one archive, and independent plain-file downloads with no archive at all (a WFS file-index download, e.g. LiDAR HD's `:dalle` layer, delivers each tile as its own separate download). The candidate set for mosaic-building is filtered to just the recognized raster-tile extensions (`raster_tile_paths`) even when the extracted batch also includes non-raster metadata/supplement files, since passing one of those to `gdalbuildvrt` directly would either fail or produce a broken mosaic. Building a fresh mosaic also builds external overviews (`gdaladdo`, `resampling="average"`, no forced compression — deliberately not the JPEG settings a separate imagery-specific script used, since a mosaic here could just as easily be elevation/DEM data) immediately afterward. A `gdaladdo` failure is reported but doesn't block adding the mosaic itself.
- **Idempotent-revisit check, including for pyramids.** An existing matching VRT (predictable naming, `find_existing_mosaic`, checked within the archive-named subfolder) is reused rather than re-proposed; if it's missing its own `.ovr` (from before pyramid-building existed, or a previously-failed attempt), one is built lazily on this revisit.
- Every extracted file (data, metadata, supplements alike) is offered to `add_layer`, which silently does nothing for a file with no recognized raster/vector kind (a metadata readme, an XML sidecar) — the file is still kept on disk, just not added as a QGIS layer, which was never a meaningful thing to do with a plain-text or XML file in the first place.
- **The archive itself is never deleted** after extraction, at any point — it remains in the central-repo location it was downloaded to, alongside its extracted contents.

---

## 5. Spatial extent

Declared per gateway/source as one of two flavors:

- **Coordinate query** (WFS `filter`, WM(T)S export's clip bbox) — arbitrary geometry in, matching results out. Implemented for the WM(T)S export path using the **current QGIS map canvas extent**, reprojected via `QgsCoordinateTransform` into whatever CRS the target actually needs — avoiding a repeat of the Lambert-93/Web-Mercator mismatch risk the standalone `fetch_zone_tile.py` script carried (it never reprojected and just warned about it); a live QGIS session has real transform machinery available that a standalone script run from a shell didn't.
- **Categorical browse** (Atom's département/tile-ID resource naming) — spatialization baked into the resource hierarchy itself, navigated by named division rather than coordinates. Would require an additional declared dependency (a boundary reference layer) if built out further — not yet needed for anything currently implemented.

Other extent-input mechanisms QGIS itself already provides (draw on canvas, an existing loaded layer's extent, a named bookmark) remain a real option for future work but are not wired into any current action beyond "use the current canvas extent."

---

## 6. Multilingual

Two genuinely different problems, handled differently:

- **Our own UI** (labels, buttons, dialogs): standard QGIS/Qt mechanism — every user-facing string wrapped in `self.tr(...)`, English source string as the translation key. Enforced by an automated check (`tests/test_tr_discipline.py`), not just a stated convention. `i18n/sigate_en.ts` is a real, generated file; the compiled `.qm` is a build artifact, not tracked.
- **The data's own content** (service descriptions, in whatever language the producer published it): no general solution. Where a source genuinely supports a preferred content locale, a gateway config may declare one; otherwise content displays as-is.

---

## 7. Explicitly deferred / open items

- **B.7 (many-small-endpoints pattern)**: real evidence (Kartkatalogen: 203 separate Norwegian WFS endpoints) that some countries' WFS layer may be structurally inverted from IGN's one-endpoint-many-layers shape. Not yet confirmed as the dominant pattern outside Norway; no France-specific work needed here yet.
- **CSW/catalog-as-router mechanism** (§3): needs a genuinely rich real-world capabilities test case (IGN's own was too thin/link-less) before finalizing.
- **Multi-select crossing multiple underlying endpoints**: whether "one single combined target choice" still holds, or needs its own rule — not yet resolved, and not yet a real scenario for any currently-configured source.
- **QGIS expression vs. CQL_FILTER general equivalence** (WFS Filter panel) — simple `field = 'value'` expressions are valid in both languages, so the same raw text is reused for "Query features" (CQL) and "Add to map" (QGIS expression), but this is a syntax overlap for simple cases, not a confirmed general equivalence — a `LIKE` wildcard or a complex boolean expression could plausibly still diverge, not yet tested. **Spatial predicates specifically are now a confirmed, precisely-understood divergence, not just a suspected one**: a real reported failure (`ST_Within($geom,@map_extent)` → 400 Bad Request) traced to three compounding bugs, all fixed for the CQL path - `@map_extent` was never actually wired to a real canvas extent anywhere (hardcoded `None`, both in the Filter panel and in the real query-time resolution); this project's own SQL-editor autocomplete suggested `ST_`-prefixed (PostGIS/SpatiaLite) function names that neither target language accepts (CQL and QGIS's native expression engine both use bare `WITHIN`/`INTERSECTS`, no prefix); and the geometry literal was being substituted as a quoted string (`'POLYGON(...)'`) when CQL expects it bare (`POLYGON(...)`, unquoted - a type mismatch, not just a syntax one). Fixing the geometry-literal quoting for CQL, however, is the *wrong* form for the "Add to map" (QGIS native expression) path specifically: QGIS's own expression engine needs a geometry literal wrapped as `geom_from_wkt('POLYGON(...)')` (a function call over a quoted string), not bare WKT text, which isn't valid QGIS expression syntax on its own. This is now handled by giving each path its own resolved text (`ui/expression_builder.substitute_tokens` for the CQL query, `substitute_tokens_for_qgis` for "Add to map"): for "Add to map" `$geom` becomes `$geometry`, `@map_extent` becomes `geom_from_wkt('...')` in the feature type's CRS with QGIS's own x,y order (no authority-order axis swap, unlike the CQL path), and the request now states `srsname` so the layer's CRS matches the literal. Confirmed live against IGN's EPSG:4326 LiDAR HD metadata layer: before, QGIS's native provider silently dropped the CQL text and loaded every feature; with the expression it returns exactly the server's own counts (102 WITHIN, 151 INTERSECTS for one extent).
- **`QgsNetworkAccessManager` was never actually wired in, despite being a stated coding rule.** The `fetch=`/`download_fn=` injection points throughout `gateways/`/`ui/` are real and genuinely useful for testing, but production use falls back to plain `urllib` everywhere, meaning downloads don't respect QGIS's own proxy configuration — something the QGIS plugin repository's own guidelines specifically call out as a reason to prefer `QgsNetworkAccessManager`. Needs an actual implementation wired into each provider's widget construction.
- "Don't ask again this session" checkbox on the size-warning dialog, and on the mosaic/pyramid-offer prompts — not decided either way.
- Elevation color ramp applied automatically on layer add — explicitly skipped, not decided against.
- WM(T)S curated-list content itself (which layers, how categorized, across which configured countries beyond France) — not yet built out.
- **"Export clipped area as GeoTIFF"'s GDAL CLI dependency has no detection or graceful-degradation check** — unlike 7z (`download/archive.py`'s `find_7z_executable`, checked before any 7z-dependent action is even offered), `gateways/wmts_export.py` assumes `gdal`/`gdal_translate` (GDAL ≥ 3.11, for the `gdal raster clip` subcommand) are simply on PATH and surfaces whatever raw OS error results if not, through the export dialog's own failure path — not a proactive, clear "GDAL not found" message the way a missing 7z executable already gets. Cooperative cancellation for this feature is also explicitly weaker than the download pipeline's (checked only between the two possible GDAL steps, never mid-command) — an accepted limitation given GDAL's CLI tools offer no clean interrupt mechanism, not something worth faking a finer-grained progress indicator over.
- **Authentication for the WM(T)S tab's auth-gated layers — implemented, not yet fully live-verified.** `ui/authcfg.py` provisions a QGIS Authentication Configuration of method `"APIHeader"` idempotently; two things remain unconfirmed against a real QGIS install: (1) the method's own config-key names (`headername`/`headervalue`, inferred from public usage discussion, not QGIS's own source), and (2) whether authcfg expansion is actually wired through for the `"wms"` provider specifically with this method — a real bug exists in QGIS's own tracker showing at least one other provider silently dropping APIHeader auth. Test by adding a private-endpoint layer through the tab and confirming the tile request actually succeeds, not just that the connection string looks right.
- **GDAL's handling of `.tab`-sidecar-referenced raster tiles in `gdalbuildvrt`** (§4.4) — expected to work (a well-supported GDAL auxiliary format) but not yet directly confirmed against a real file.
- **"Add to map" for a selection with no guessable identifier field** — falls back to the whole-filter behavior rather than restricting to the selected rows, with a status message explaining why; the identifier-field guess list (`fid`/`gml_id`/`id`/`objectid`) is best-effort and not configurable per source yet.
- **No way to actually save a new per-product extraction-classification override through the UI** — `download/extraction_structure.py`'s `save_product_override` exists and is real, but nothing in `ui/download_flow.py` calls it; only the bundled seed overrides are ever consulted (§4.3).
- **CSW/general catalog search router** and its generalization beyond CSW (Kartkatalogen's `servicedirectory` REST catalog plays a structurally similar role for Norway via a simpler protocol) — not yet built.

---

## 8. Actual file/module layout

The git repository root sits one level above `sigate/` itself - `sigate/` is specifically the directory that gets zipped and installed into QGIS; `LICENSE`, `README.md`, `Makefile`, `pyproject.toml` (ruff configuration - deliberately pinned to ruff's own real bare-default rule selection, confirmed to change nothing about existing behavior, not an expanded rule set), and `.gitignore` all live at the repo root, one level above, and are not part of the distributed plugin zip (except `LICENSE`, deliberately also copied into `sigate/LICENSE` so it ships with the installed plugin, matching common practice for published QGIS plugins).

Everything under `gateways/`, `download/`, and `sources/` is pure Python with no Qt dependency — testable without a live QGIS install. Only `ui/` is Qt/QGIS-dependent, and correspondingly the only layer whose tests need a real (headless) QGIS install to run (`tests/conftest.py`'s `qgis_app` fixture) - `make test-pure`/`make verify-pure` run everything except those specifically, for an environment (like the one this whole project was built in) with no real QGIS install at all.

```
sigate/
├── metadata.txt                    # version follows this project's own 0.0.# build-counter convention
├── __init__.py
├── icon.png
├── plugin.py                       # initGui()/unload(), registers the 3 providers

├── gateways/                       # pure fetch/parse/build logic, no Qt
│   ├── base.py                     #   GatewayError/DownloadCancelled, GatewayCapabilities declaration
│   ├── ogc_constants.py            #   shared WFS/WMS/WMTS query-parameter constants
│   ├── atom_constants.py           #   Atom-feed-specific constants
│   ├── atom.py                     #   Atom feed parsing/pagination; the default HTTP transport (http_get, fetch_with_header) other gateways reuse
│   ├── wfs.py                      #   GetCapabilities/GetFeature URL building, CSV/GML response parsing, CQL filter helpers
│   ├── wmts_wms.py                 #   WMTS/WMS GetCapabilities parsing (layers, tile matrix sets, real per-level resolution), QGIS-native connection-string building
│   └── wmts_export.py              #   the "Export clipped area as GeoTIFF" pipeline: WMTS XML definition generation/caching, GDAL CLI clip commands, size estimation

├── sources/                        # named to avoid QGIS's own unrelated "profile" concept (QgsUserProfileManager)
│   ├── seed.py                     #   bundled defaults as Python dataclasses (IGN France populated; reviewed, shipped code)
│   └── store.py                    #   SourceConfig/GatewayConfig/AuthConfig models; loads/merges seed.py + the user's own JSON override file at runtime

├── download/                       # the §4 pipeline, gateway-agnostic by design
│   ├── pipeline.py                 #   DownloadItem/DownloadOutcome, the full download-through-integrity-check sequence, source/category/layer subdirectory building
│   ├── targets.py                  #   central-repo/destination target model; named-bookmark persistence; free-space checking
│   ├── archive.py                  #   zip/tar via stdlib; 7z detection + `7z l -slt`/`7z x` wrapper; split-archive grouping; external-command logging
│   ├── integrity.py                #   byte-count check + MD5 verification
│   ├── extraction_structure.py     #   recursive {N}_{ROLE}_LIVRAISON scan -> per-product config -> heuristic checklist fallback (§4.3)
│   └── mosaic.py                   #   VRT-mosaic-offer logic, gdalbuildvrt/gdaladdo wrappers, idempotent-revisit check (§4.4)

├── ui/                             # Qt widgets - the only QGIS-dependent layer
│   ├── arcgis_rest_provider.py / arcgis_rest_widget.py    #   ArcGIS REST tab: layer list, where/extent filter, paging, selection-aware Add to map (provider `arcgisfeatureserver`, filter in `sql=`)
│   ├── wfs_provider.py / wfs_widget.py                    #   query/filter/download/add-to-map, selection-aware Add to map
│   ├── wmts_wms_provider.py / wmts_wms_widget.py          #   Add to map handoff + Export clipped area as GeoTIFF
│   ├── bulk_listing_provider.py / bulk_listing_widget.py
│   ├── connection_manager.py       #   shared New/Edit/Delete/Load/Save combo, one entry per gateway instance
│   ├── expression_builder.py       #   the field/value/SQL-editor widget backing the WFS Filter panel
│   ├── query_builder_dialog.py     #   wraps expression_builder as its own modal panel
│   ├── authcfg.py                  #   QGIS Authentication Configuration provisioning for apikey-gated WMTS gateways
│   ├── download_task.py / download_progress_dialog.py     #   background QgsTask + two-bar progress popup for downloads
│   ├── wmts_export_task.py / wmts_export_dialog.py        #   background QgsTask + single-bar progress popup for GeoTIFF export
│   ├── wmts_zoom_level_dialog.py   #   zoom-level choice for GeoTIFF export, with per-level size estimates
│   ├── download_flow.py            #   shared download-through-add-layer orchestration used by both WFS and Bulk Listing
│   ├── target_picker.py            #   shared widget: central-repo + destination target selection
│   ├── settings.py                 #   typed get/set wrappers around QgsSettings (size-warning threshold, 7z path override, locale, data-dir helper)
│   ├── settings_dialog.py          #   the settings dialog itself
│   └── icons.py                    #   shared icon loading

├── i18n/
│   └── sigate_en.ts                #   real, generated translation source file; .qm compiled output is a build artifact, not tracked

├── docs/
│   ├── spec.md                     #   this file
│   ├── dev_workflow.md             #   coding rules + the detailed, append-only history of every fix/feature
│   ├── version.md                  #   one entry per delivered version - "what shipped in version X"
│   ├── sources.md                  #   every bundled backend and the content it serves; kept in step with sources/seed.py by a test
│   ├── candidate_sources.md        #   research on further national sources: what's integrated, what's deferred and why
│   ├── install_guide.md            #   end-user install instructions and troubleshooting
│   ├── user_guide.md               #   end-user feature walkthrough
│   └── test_scenarios.md           #   manual test plan for anything needing a real QGIS install + real network/GDAL access, including a condition-matrix appendix

└── tests/                          # one file per module under gateways/download/sources/ui, plus:
    ├── conftest.py                 #   qgis_app fixture (headless QGIS instance) for every ui/ test
    ├── test_tr_discipline.py       #   automated check that every user-facing string is wrapped in self.tr(...)
    └── test_icons.py
```

Config storage, per module: `sources/seed.py` and other bundled defaults are plain reviewed Python; anything user-growable (`sources/store.py`'s override file, `download/targets.py`'s bookmarks) is JSON, saved under the active QGIS profile's plugin data folder (`QgsApplication.qgisSettingsDirPath()`) — never `QgsSettings` (scalar-only by design) and never importable `.py` (arbitrary-code-execution risk for data meant to be shared between users). The legitimate small scalar settings that *do* belong in `QgsSettings` (size-warning threshold, detected 7z path, UI locale) are namespaced `SIGate/...`.
