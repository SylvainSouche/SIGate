# SIGate — Version History

One entry per `metadata.txt` version increment actually packaged and
delivered, under this project's own build-counter convention (see
`dev_workflow.md`'s coding rules — one bump per delivered zip, not per
code change). Full technical detail for each change lives in
`dev_workflow.md`'s own chronological log; this file exists specifically
to answer "what shipped in version X," which `dev_workflow.md`'s
per-feature (not per-version) organization doesn't directly answer.

**A note on accuracy**: versions `0.0.17` through `0.0.21` are
reconstructed retrospectively from the development log rather than
recorded at the time each was delivered — the `0.0.#` convention itself
was only adopted partway through this project's development, so nothing
earlier was tracked by version number at all. The *content* of each
entry below is accurate; the exact boundary between two versions
delivered in quick succession on the same real-world day (specifically
`0.0.22`/`0.0.23`, both part of finishing the zoom-level-choice feature)
could not be reconstructed with full confidence and is presented as a
single combined entry rather than a falsely precise split. From `0.0.24`
through `0.0.33`, every entry was recorded at the time of delivery.

A second reconstruction gap exists for `0.0.34` through `0.0.66`: this
project's working environment was reset partway through, which broke
the "updated in the same turn as every version bump" practice this file
depends on for several versions running - those entries were only
condensed into this file afterward, from `dev_workflow.md`'s own
chronological log (which *was* kept current throughout, entry by entry,
regardless of the environment reset). The content is accurate - each
entry below was checked directly against `dev_workflow.md`'s own
detailed record for that version, not written from memory - but, as
with the `0.0.17`-`0.0.21` gap above, it wasn't captured live. Going
forward from `0.0.67`, this file is updated in the same turn as every
version bump again.

---

## 0.0.17

Fixed a real plugin-breaking installability bug: `metadata.txt` had a
duplicate `icon=icon.png` line, which QGIS's own metadata reader
(built on Python's `configparser`, strict mode by default) fails to
parse at all — `DuplicateOptionError`. This is exactly why QGIS's
Plugin Manager showed "Error reading metadata: general" and "Installed
version: ?" — once metadata parsing throws, QGIS can't even read the
`version` field. Reproduced and confirmed the exact failure mode before
fixing it. This version also establishes the `0.0.#` build-counter
versioning convention itself, and the `sigate-0.0.#.zip` archive-naming
convention, both requested directly.

## 0.0.18

Built "Export clipped area as GeoTIFF" for the WM(T)S tab from scratch
— a second, genuinely different action alongside the existing "Add to
map" live-connection handoff, materializing a real, standalone GeoTIFF
file clipped to an area rather than a live map layer. Ported from the
standalone `fetch_zone_tile.py` script rather than built fresh. Two
decisions were elicited directly before building, since neither was
inferable from what already existed: the clip area comes from the
current QGIS map canvas extent (not a France-specific grid file), and
the clip itself shells out to GDAL's own CLI tools (`gdal raster clip`,
`gdal_translate`) rather than reimplementing via QGIS's bundled GDAL
Python bindings. New: `gateways/wmts_export.py`, `ui/wmts_export_task.py`,
`ui/wmts_export_dialog.py`, wired into `ui/wmts_wms_widget.py`.

## 0.0.19

Fixed a real CRS-safety gap in the export feature, flagged directly
("we must be careful not to mix crs between canvas extent and
gdal_translate coordinates"). The clip's bounding box was reprojected
into the target layer's CRS before clipping, but never told to GDAL
explicitly — relying on an unstated assumption that GDAL's own derived
CRS for the WMTS dataset would already match. Checked against GDAL's
real documentation: `gdal raster clip` supports an explicit `--bbox-crs`
flag for exactly this. Added and wired through.

## 0.0.20

Fixed two related, both real, both directly reported bugs in the export
feature:
- **"error : read only /tmp...could not export the clip area"** — the
  scratch clip's temporary directory defaulted to the process's current
  working directory (`"."`), which for a GUI application is frequently
  unrelated to user data and not guaranteed writable. Fixed to default
  to the OS's real temp directory, and further hardened by having the
  export dialog pass the actual chosen output directory explicitly
  (guaranteed writable, since the user just picked it via a save
  dialog).
- **"the xml file used by gdal raster clip is actually generated within
  the plugin directory... first behavior is not acceptable"** — the
  cached WMTS XML definition file was written to a hidden QGIS
  profile-settings directory rather than somewhere the user chose or
  could see. Moved to sit alongside the output file too, matching the
  same principle just applied to the scratch directory above.

## 0.0.21

Added a pre-flight size warning for "Export clipped area as GeoTIFF",
requested after a manual size estimate for one real-world example
(10km × 20km at 20cm resolution) came out to double-digit gigabytes
uncompressed. Reused the size-warning threshold already configured for
regular downloads rather than inventing a second mechanism. Required
extending `gateways/wmts_wms.py`'s capabilities parser to capture each
layer's real per-level resolution (`TileMatrix`/`ScaleDenominator`,
previously unparsed) so the estimate is genuine, not guessed.

## 0.0.22–0.0.23

Finished and shipped the zoom-level-choice feature for GeoTIFF export
(letting a layer's available resolution levels be picked explicitly,
each labelled with its own size estimate, rather than always defaulting
to the finest) — verified beforehand against GDAL's own documentation
that its WMTS connection string supports selecting a specific
`tilematrix=` level. Two real bugs were caught and fixed while
finalizing this for delivery (a missing `Optional` import causing a
guaranteed `NameError`, and an editing slip that had merged two
unrelated tests into one function, leaving a fixture undeclared) — both
caught by linting, not by running the test suite, since the affected
files need a live QGIS install this environment doesn't have.

## 0.0.24

A comprehensive rewrite of `docs/spec.md`, requested directly ("update
specs to take all discussion into account"). The previous version had
drifted substantially — it still described the plugin as
"design discussion complete, implementation not yet started" despite
having been built, packaged, and iterated on real feedback many times
over. Rewritten section by section against the actual current
architecture and a fresh read of the real file tree, rather than
patched incrementally again.

## 0.0.25

Added three new seeded sources — Switzerland (swisstopo), Germany (BKG
TopPlusOpen), and the Netherlands (PDOK, WMTS only) — from an uploaded
~30-country candidate survey. Applied the project's existing policy for
seeding a new source (needs an already-implemented gateway type, and an
independently confirmed endpoint) rather than adding all thirty
unchecked. New `docs/candidate_sources.md` catalogs the full survey by
integration status, so the research isn't lost.

## 0.0.26

Added a fifth source, Austria (basemap.at, WMTS only), on direct
request immediately after the previous three shipped.

## 0.0.27

Fixed a real bug reported directly ("germany wfs is empty"): the
seeded BKG WFS endpoint was a guessed, generic URL that didn't
correspond to any real BKG service — BKG's WFS offering is fragmented
per dataset, the same pattern already correctly identified and
deferred for in Belgium/Slovakia/Ireland, except this one had been
shipped as a guess instead of deferred. Replaced with one specific,
confirmed real dataset (VG250, administrative boundaries).

## 0.0.28

Fixed a real, reported WFS spatial-filter failure ("`ST_Within($geom,
@map_extent)` error 400 bad request"), traced to three compounding bugs
in this project's own code, not the server: `@map_extent` had never
actually been wired to a real canvas extent anywhere (hardcoded `None`
in two places); this project's own SQL-editor autocomplete suggested
`ST_`-prefixed function names that neither GeoServer's CQL nor QGIS's
native expression engine accepts; and the geometry literal was
substituted as a quoted string, which CQL rejects outright (a type
mismatch, not just bad syntax). All three fixed for the CQL path;
surfaced a further, precisely-documented remaining gap for the
"Add to map" (QGIS-native-expression) path specifically, tracked in
`docs/spec.md` §7.

---

## 0.0.29

Two things bundled into this delivery:

- **The download directory structure feature from the previous "Unreleased" entry**, finally packaged: downloads now land under a `source/category/layer` subdirectory structure instead of flatly in one directory.
- **A full code-review remediation pass**, applied by principle rather than literally against the review's own (partially stale) snapshot: no development-history narrative left in docstrings/comments across the whole codebase; every public function/method documents its real parameter contract; every `except Exception` in `ui/` now logs full diagnostic detail via `QgsMessageLog`, not just a user-facing message; public functions in `download/`/`gateways/`/`sources/` validate their preconditions with typed errors; type hints added throughout `ui/`. Caught several genuine bugs along the way, not just style issues - most notably, `DownloadItemsTask.exception` (set on a genuinely unexpected crash) was never read anywhere at all, so the download progress dialog would silently close on a real crash with zero indication anything had gone wrong; and the same stale-selection-index risk turned out to exist in three separate places, not just the one originally flagged. Full detail in `dev_workflow.md`'s own entry for this pass.

---

## 0.0.30

Archive extraction changed on direct request, following a factual question about how it currently worked ("where and how are archives expanded, what's kept vs. deleted, is the archive itself kept or deleted"). Answering that by tracing the real code surfaced a genuine discrepancy: `docs/spec.md` had described `METADONNEES`/`SUPPLEMENTS` as surfaced/offered and an interactive checklist as if both existed, when the real code only ever extracted `DONNEES` and had just a single yes/no confirmation. Requested directly afterward: keep everything the archive contains, extracted into a folder named after the archive itself. Fixed spec.md's inaccuracy to match. A real bug was caught and fixed before shipping while building the archive-naming helper: a hardcoded `[:-4]` slice assumed `.7z` was 4 characters when it's actually 3, which would have silently dropped one extra real character from every plain-`.7z` archive's derived folder name. Also fixed: building a mosaic from a "keep everything" batch now filters to just the actual raster tiles first, since passing a metadata/supplement file straight to `gdalbuildvrt` would fail or produce a broken mosaic. Full detail in `dev_workflow.md`.

---

## 0.0.31

MD5 verification actually wired to a real hash for the first time, following a direct factual question ("do we check the md5 as we said we would?"). The checking mechanism (`download/integrity.py`) had always existed and worked correctly, but nothing in production code ever populated a real hash for it to check against - so the check had never once actually triggered in real use, despite `docs/spec.md` describing it as a working, confirmed feature. Requested directly afterward: verify integrity whenever a checksum happens to be available. WFS rows carrying an md5-like attribute now pass it straight through; Atom/Bulk Listing downloads now look for a `.md5` sidecar link within the same feed entry, fetch it, and parse the real hash out of it. `docs/spec.md` corrected to match. Full detail in `dev_workflow.md`.

---

## 0.0.32

A comprehensive review of `docs/spec.md` against the real implementation, requested directly after two real discrepancies had already turned up in quick succession. Checked a wide set of specific, verifiable claims against the actual code rather than the prose - every one held up this time, no new discrepancy found. One real, adjacent gap surfaced along the way: `ui/download_task.py`, `ui/wmts_export_task.py`, and `ui/wmts_export_dialog.py` had zero dedicated test files. Closed with 24 new tests across three new test files, exercising each class's own logic directly rather than only through another widget that happens to construct one - including catching and correcting a wrong assumption about exactly how cancellation behaves before writing the tests down as fact. Also: a full rewrite of `docs/test_scenarios.md`, the manual test plan, which had gone stale against nearly everything built this session (the WFS Filter panel's real dialog-based UX, `@map_extent` now genuinely working for one of its two actions, "keep everything" archive extraction into a per-archive subfolder, MD5 verification, the download directory structure, background progress dialogs, and command-visibility logging all had no scenarios at all) - rewritten scenario by scenario, with a closing condition-matrix table covering the specific combinations requested directly (repo-only vs. repo+destination, already-downloaded, network/permission failures, checksum present/matching/mismatched, mid-operation cancellation, size warnings, selection vs. none). Full detail in `dev_workflow.md`.

---

## 0.0.33

A full audit of the project's own file tree for whether it's actually a clean, consistent, git-able project, requested directly. Real gaps found by just listing every real file on disk: no `.gitignore` at all despite real build clutter (`__pycache__/`, `.ruff_cache/`) the whole session; no `README.md` or `LICENSE` at the repo root despite this project's own coding rules requiring both, and despite `metadata.txt`/`docs/spec.md` already naming BSD-3-Clause as the license without the actual license text existing anywhere; and - the same "documented but never actually built" pattern already caught twice this session - a Makefile `dev_workflow.md` itself already claimed was added, with a specific target list, that genuinely does not exist anywhere. All fixed: `.gitignore`, a real `LICENSE` (copyright holder name requested directly rather than fabricated, copied into `sigate/` too so it ships with the installed plugin), `README.md`, and the Makefile actually built this time - plus `pyproject.toml` for a real, pinned ruff config, corrected after an initial draft was found to silently enable a rule (`E501`) this codebase was never actually held to, confirmed byte-for-byte behavior-identical to no config at all before being trusted. Every new Makefile target actually run and confirmed working before being trusted, including a `test-pure`/`verify-pure` pair added specifically because this same sandbox's own lack of a real QGIS install (the limitation behind every `ui/` test's own caveat all session) makes the plain `test`/`verify` targets fail outright here.

## 0.0.34

Two real fixes to test infrastructure reported directly from the user's own machine once a working QGIS install let the full (not just `-pure`) suite run for the first time. First, a genuine hang: `WmtsWmsSourceSelectWidget` never had the same test-injection seam for `authcfg_provisioner` that `fetch` already had, so a test switching to IGN's apikey-gated private connection fell through to the real QGIS auth manager, which blocks waiting for a master password a headless test session can never supply. Fixed by threading `authcfg_provisioner` all the way through, with a safe fake now the test file's own default. Also added the widget-level zoom-level-choice dialog for GeoTIFF export (a level's own resolution/pixel-dimensions/size shown per entry, only appearing when a layer's tilematrixset actually declares more than one real level), catching two real bugs (a missing `Optional` import, two merged test bodies) via lint before either reached the user.

## 0.0.35

A second real `make test` failure from the user's machine: `download_items` only ever created the top-level `central_repo` directory, never the nested `subdirectory` a `DownloadItem` could carry - once real callers exercised the source/category/layer nesting added earlier, `dest_path.parent` could sit several levels below what actually existed, and any real writer failed with `FileNotFoundError`. Fixed at the shared orchestrator: `dest_path.parent` is now created immediately before every `download_fn` call, for every item, regardless of nesting depth.

## 0.0.36

The first full `make test` run against real QGIS after the previous two blocking issues were cleared - 6 failures, 363 passed. All six were test-file bugs (stale fakes never confirmed against a live QGIS/GDAL before), not production regressions: a fake `add_layer` recording everything instead of filtering by `guess_layer_kind` like the real callers do; two fakes missing a `log=None` parameter the real functions have always had; a query-builder test not isolating its own caching check from the dialog's own "always re-run on accept" behavior; and a WKT-casing/URL-encoding assumption ("POLYGON" vs QGIS's real "Polygon", plain `unquote` vs `unquote_plus`) that had simply never been checked against real output before now.

## 0.0.37

Fixed a real `QgsField` constructor deprecation warning (QGIS 3.38+ moved off `QVariant` onto `QMetaType`), confirmed against QGIS's own deprecated-API list. Added a `_string_field` helper that tries the new constructor first and falls back to the old one on any exception, since this plugin's declared minimum (`qgisMinimumVersion=3.10`) predates the new overload's existence - deliberately not branching on a version string.

## 0.0.38

Bulk Listing tab generalized to browse STAC catalogs (Switzerland) alongside Atom feeds (France) from the same connection combo. New `gateways/stac.py` implements the same functional contract already established for Atom (`fetch_page`/`fetch_all_pages`/`resolve_downloadable_files`/`Entry`/`Link`/`LargeListing`), handling STAC's real divergences directly rather than papering over them: link-based pagination instead of page numbers, and an inline sha256 multihash checksum instead of Atom's external `.md5` sidecar link. `swisstopo_ch` gained four STAC gateway instances (swissALTI3D, swissTLM3D, swissSURFACE3D, SWISSIMAGE). Norway's flat, facet-based catalog was deliberately scoped out of this generalization - it doesn't fit this tab's tree-browsing model at all, noted as a distinct future integration point rather than forced in.

## 0.0.39

Documentation and manual test scenarios brought up to date for the Atom+STAC generalization - `user_guide.md`, `spec.md`, and `test_scenarios.md` had all drifted (still describing the tab as Atom-only, or citing a stale entry count) once the previous version's code landed.

## 0.0.40

Swiss STAC coverage expanded to outdoor-activity route networks (ski touring, snowshoe, hiking, winter hiking, cycling/MTB/skating) - 8 new confirmed-real instances on `swisstopo_ch`. Avalanche hazard data and SAC mountain hut data were both explicitly investigated and confirmed genuinely out of reach for this integration: avalanche data (SLF's own live bulletin API) is a wholly separate protocol, not on the STAC catalog; hut locations are confirmed, directly from swisstopo's own documentation, as never entered or managed by swisstopo at all - not just unresearched.

## 0.0.41

A direct correction to the previous version's "genuinely out of reach" conclusion on avalanche data: a real avalanche hazard *zone* dataset (distinct from the live bulletin) was found on a third Swiss platform, `geodienste.ch` (the intercantonal geodata portal). New source `GEODIENSTE_CH` with all three gateway types, confidence stated honestly per gateway (WMTS well-confirmed with real, legally-binding hazard-zone layers; WFS confirmed live but with an unverified exact schema; STAC's URL shape inferred from one real example, not independently fetched).

## 0.0.42

Plugin icon replaced with a user-supplied 64×64 RGBA PNG.

## 0.0.43

Norway's Geonorge two-level (Theme → Dataset) hierarchy completed and verified - substantial work was already on disk from earlier in the session; this pass closed real remaining gaps (a missing type in the Bulk tab's own entry-type union, no end-to-end widget test, and four docs still stating flatly that Norway's catalog "doesn't fit this tab's design," no longer true of what had actually been built on top of it).

## 0.0.44

Fixed a real, confirmed bug: the Norway connection's catalog came back empty with no error. The real API returns Norwegian CSV column headers (`Tema`, `Tittel`), not the English ones (`Topic`, `Title`) the code and its own tests had both assumed - confirmed by fetching a real response directly. Also fixed a related risk found proactively: the real response carries a leading UTF-8 BOM, which would have silently corrupted the first column's key.

## 0.0.45

Fixed a real, reproduced crash: selecting an individual file inside a Geonorge dataset's Atom feed for download raised `AttributeError: 'Entry' object has no attribute 'checksum'`. Browsing that deep in a Geonorge connection delegates to real `gateways.atom.Entry` objects, which have no `.checksum` field at all (unlike this module's own `Entry` class) - `resolve_downloadable_files` now checks which kind of entry it actually received before assuming its own shape.

## 0.0.46

Two threads from one question ("for Switzerland, do we only have German data?"). First, wired up a real, already-documented-but-dormant mechanism: geo.admin.ch's WMS/WMTS genuinely support `?lang=de/fr/it/en`, and the plugin's own `supports_locale`/`get_locale()` scaffolding already existed but was never connected to a request or a UI field - now it is, with a locale picker added to Settings. Second, built the actually-requested feature: an LLM-assisted translation memory (`sigate/translation.py`) for sources with no native language option - discover untranslated strings, export as JSON, translate with an LLM of the user's own choosing outside the plugin, load the result back in. Also fixed a real GDAL export failure: the real GDAL error was captured but never shown (only a generic status line reached the user), and no check ever confirmed GDAL was actually new enough for `gdal raster clip`.

## 0.0.47

Fixed a second real WFS 400 from the same mistake class as an earlier fix: a user-typed `st_intersect(...)` filter (a PostGIS-style name CQL doesn't recognize) was going to the server unchanged. The earlier fix only ever helped at the point of typing (autocomplete suggestions); this one actively rewrites recognized `ST_`-prefixed names to CQL's own bare form, composed directly into the shared token-substitution function so every call site gets it automatically.

## 0.0.48

A third real WFS filter bug, resolved by elimination together with the user: a spatial filter against an EPSG:4326 feature type returned zero results with both `WITHIN` and `INTERSECTS`, ruling out a predicate-semantics mistake and pointing at axis order. EPSG:4326 is authority-defined with lat,lon coordinate order, but `QgsGeometry.asWkt()` always emits x,y - every source confirmed working until now was a projected CRS with no such ambiguity. Fixed by swapping coordinates when the target CRS's own `hasAxisInverted()` flag says to.

## 0.0.49

Fixed a real reported bug: "the vrt however is not built properly and a preexisting one was included." An existing mosaic file's mere presence was being treated as sufficient reason to reuse it, with no check that its referenced tiles actually matched what's currently on disk. New `mosaic_covers_tiles` compares an existing VRT's real source files against the current tile set and triggers a silent rebuild on any mismatch - also fixing two things needed to make that rebuild actually work (`gdalbuildvrt -overwrite`, and clearing a stale `.ovr` sidecar before rebuilding).

## 0.0.50

A second reported mosaic bug: "the vrt file is always named the same, its location doesn't follow any subdirectory logic." The mosaic for a batch of plain-file downloads was landing at a flat, generic location regardless of source or layer. Fixed by deriving the mosaic's destination from where its own tiles actually landed. Investigating this surfaced a related, deeper gap: copying a file to a separate Destination folder was flattening it, discarding the same nested structure - fixed to preserve it.

## 0.0.51

Fixed a real, simply-confirmed bug: adding a WMS/WMTS layer named it after the connection ("IGN (France)") instead of the actual layer - every layer from one connection was indistinguishable once added. Fixed to use the layer's own real title, falling back to its identifier when no title is declared.

## 0.0.52

Fixed a real bug reported directly from the resulting connection string: adding IGN's private WMTS layer produced the invalid CRS `EPSG:LAMB93`. IGN declares this TileMatrixSet's CRS as the bare name `"LAMB93"`, not a proper URN or EPSG code - the existing fallback blindly assumed any non-EPSG-prefixed value was a bare numeric code. Fixed with a small, explicitly-scoped alias for this one confirmed case, plus a validation-and-fallback check that had existed for the export path but not the "Add to map" path.

## 0.0.53

A correction to the previous version's own fix, found by the user cross-checking against QGIS's own native connection dialog on the same layer: the real CRS is `IGNF:LAMB93` (IGN's own registered authority), not `EPSG:2154`. The underlying function was rewritten to actually parse a URN's declared authority rather than assume every URN is EPSG-scoped.

## 0.0.54

Fixed a genuine duplicate-fixture staleness bug from the first full `make test` run since the repo scaffolding was restored: `test_bulk_listing_widget.py` had its own separate, never-updated copy of the Geonorge CSV header fixture from an earlier fix, still on the old wrong English header.

## 0.0.55

Fixed a real crash (`IndexError`) when double-clicking an empty or stale results tree - a defensive pattern already used elsewhere in the same file (`_selected_entries`) had never been applied to this handler.

## 0.0.56

The first full `make test` run against real QGIS after the repo scaffolding was restored - 470 passed, 6 failed. All six were genuine test-file bugs, not production issues: a stale pagination-URL assumption, two fakes missing a `log=None` parameter predating a real signature change, a query-builder test not isolating its own cache-check scenario, a WKT precision-formatting assumption, and a fake missing a `lang` parameter added earlier in the session. No production code needed changing for any of them.

## 0.0.57

A follow-up WFS 400 made clear the axis-order fix alone wasn't the whole story, and that there was no way to actually diagnose it: the real HTTP error body (typically a specific, human-readable OWS ExceptionReport for a real WFS server) was being captured but never surfaced - only a generic "HTTP Error 400: Bad Request" ever reached the user. Fixed the shared fetch function to catch `HTTPError` specifically and surface its real response body.

## 0.0.58

The real cause of the WFS 400, once the previous version's fix actually worked: `Illegal property name: geom for feature type BDTOPO_V3:itineraire_autre`. The `$geom` token was substituting the guessed default `"geom"`, which isn't a real field on this feature type - IGN's BDTOPO_V3 family uses `"geometrie"` instead, confirmed via a real working IGN query and added to the guess candidates.

## 0.0.59

A sharp follow-up question ("is the query using the correct crs?") surfaced a real, previously-unexamined gap: no WFS request this plugin makes had ever included an `SRSNAME` parameter, leaving a request's actual CRS entirely to server defaults. Fixed by sending it explicitly, using the same CRS already used to build the filter geometry itself.

## 0.0.60

The real cause of "Download of capabilities failed: Unauthorized" on IGN's private WMTS, resolved by the user directly comparing a working QGIS-native auth config against SIGate's own provisioned one: the config key for the header's own name is `"headerkey"`, not `"headername"` as this module had always written.

## 0.0.61

A second, more fundamental authcfg bug found from QGIS's own documentation and source: authcfg ids must be exactly 7 lowercase alphanumeric characters, and this module's own scheme (`"sigate_" + source_key + "_" + gateway_role`) never came close. Fixed with a stable SHA-256 hash truncated to 7 hex characters - deterministic (preserving the module's reuse-across-reloads requirement) and automatically valid.

## 0.0.62

A correction to the `"headerkey"` fix, reported directly from the actual HTTP request: QGIS's real APIHeader method has no indirection at all - it treats each stored config key as a literal header name to send. The fix now stores the real header name as the config key directly, not a generic key pointing at it.

## 0.0.63

The real, final cause of the "Illegal property name: geom" error, after two rounds of what looked like fixes: switching feature types cleared the active filter text but never the underlying `query_fieldnames` state the geometry-field guess actually reads - a stale field list from a previously-queried, unrelated feature type could be guessed from with full silent confidence. Fixed by clearing every piece of last-query state on a feature-type switch, not just the filter text. First real use of the newly added `make increment` Makefile target.

## 0.0.64

The real fix, after three rounds of patching a guessing heuristic, prompted by a direct challenge ("there should be a standard wfs way... how do wfs consumers do?"): built real `DescribeFeatureType` support - the actual OGC-standard, schema-authoritative way to know a feature type's geometry field, used by real WFS clients including QGIS's own provider. The geometry-field lookup now tries this first, cached per feature type, falling back to the old guessing heuristic only when the authoritative lookup itself has no answer.

## 0.0.65

A second, adjacent "guessing from CSV columns" bug, surfaced immediately after the previous fix let a real request further than before: `Illegal property name: BDTOPO_V3:FID`. GeoServer's CSV output automatically carries a synthetic `FID` pseudo-column regardless of a feature type's real schema; IGN's `cleabs` is the real, confirmed cross-product identifier. Fixed by reordering the id-field guess candidates to prefer it.

## 0.0.66

A third real bug in the same selection-based "Add to map" flow, found from a sharp question about where an unexplained BBOX clause in the actual request was coming from: QGIS's own native WFS provider automatically scopes every fetch to the current map canvas, and the "Add to map (N selected)" path never disabled it for an id-list-based selection - which is already a complete, canvas-independent selector. Wrong on two counts: a selected feature scrolled out of view since selection would be silently dropped, and the extra clause added unnecessary length to an already-long id-list request. Fixed by disabling canvas restriction specifically for that path.

## 0.0.67

IGN retired its LiDAR HD `:dalle` WFS layers in favour of `IGNF_LIDAR-HD_METADONNEE:metadata`, whose features each carry four download links (`url_mnt`, `url_mns`, `url_mnh`, `url_npl`) instead of one `url` - so "Download selected" stayed disabled and "Add to map" gave only footprints. The WFS tab now detects download-link columns by value (a single `http(s)` URI, not by column name), asks which products to fetch when a row has several (rasters pre-ticked, point cloud not), names files from a WMS GetMap link's `FILENAME=`, keeps rasters and point clouds in separate folders, never mosaics MNT/MNS/MNH together (each downloaded item now carries its `product`), and adds a downloaded `.copc.laz` as a point-cloud layer. Also fixes QGIS 4 / Qt 6 compatibility (the three tab widgets passed `widget_mode=0` as a bare int, which Qt 6 rejects) and a stale test. Verified on QGIS 3.44.14 and 4.0.3 (511 tests each) and the 299-test pure-Python suite; not yet exercised by hand in a live QGIS session.

## 0.0.68

Layers SIGate adds to the project now go into groups of the QGIS layer tree instead of the project's top level (36 flat layers from a 9-tile LiDAR HD download was unusable). Default hierarchy, mirroring the on-disk download folders: **source > layer > product** for downloaded files (e.g. `IGN (France) > IGNF_LIDAR-HD_METADONNEE_metadata > MNT`), **source > archive name** for extracted archives, and **source** for WM(T)S layers, GeoTIFF exports and WFS "Add to map". Existing groups are reused, so repeated downloads fill one group; a built mosaic is added instead of its tiles. Implemented in one place, the new `ui/layer_groups.py` (layers are moved after QGIS's own add signal, which has no group parameter), so a future user-configurable organisation module only has to replace the group-path decision. 518 tests pass on QGIS 3.44.14 and 4.0.3, 299 pure-Python; not yet exercised by hand in a live QGIS session.

## 0.0.69

Fixes WFS "Add to map" silently ignoring spatial filters such as `WITHIN($geom, @map_extent)`: QGIS's native WFS provider needs a QGIS expression, not the CQL text the server query uses, and quietly dropped the CQL one (loading every feature with no error). "Add to map" now translates the filter (`$geometry`, `geom_from_wkt('...')`, the extent in the feature type's CRS in QGIS's own x,y order) and passes the CRS in the request. The query path was verified correct for geographic CRSes (lat,lon for the server). Confirmed live on IGN's EPSG:4326 LiDAR HD metadata layer: 102 features WITHIN / 151 INTERSECTS for one extent, matching the server. 522 tests pass on QGIS 3.44.14 and 4.0.3, 299 pure-Python; not yet exercised by hand in a live QGIS session.

## 0.0.70

New **SIGate ArcGIS REST** tab (fourth Data Source Manager category), for sources that publish vector data through Esri's REST API and not OGC WFS. Browse a MapServer/FeatureServer's layers (group layers shown as a path, only queryable layers listed), filter with a SQL `where` clause and/or the current map extent (reprojected into the layer's own CRS), page through the matching features, and Add to map - the selected rows if any (as an `objectid IN (...)` filter), otherwise everything matching the filter - through QGIS's own `arcgisfeatureserver` provider, grouped under the source name in the layer tree. New pure gateway `gateways/arcgis_rest.py`; first seeded source Arpa Piemonte SIVA avalanches (`piemonte_arpa`: 21 layers, 14 queryable, 4,091 documented avalanches). Live check through the real widget: layer listing, query (1-200 of 4091), Add to map producing a valid layer with 4091 features. 546 tests pass on QGIS 3.44.14 and 4.0.3; not yet exercised by hand in a live QGIS GUI session.

## 0.0.71

Avalanche sources go in, and the WM(T)S tab learns plain WMS. **Seeded** (all live-checked 2026-10-06): INRAE avalanches (`inrae_avalanches`, WFS, 38 feature types incl. CLPA, the stand-in while IGN's CLPA layers are missing), Friuli Venezia Giulia (`fvg_it`, WFS 14 types + WMS), NVE Skredhendelser (`nve_no`, **ArcGIS REST** - the service has no WFS but an open REST endpoint - + WMS), Valle d'Aosta avalanche cadastre (`vda_it`, WMS), two Swiss Naturereigniskataster WFS connections under `geodienste_ch` (recorded avalanches, 28,066 in the base dataset) plus its WMS, and Piemonte's WMS. **WM(T)S tab:** new WMS 1.3.0/1.1.1 capabilities parser (`wms_get_capabilities`): named layers, inherited CRS and styles, PNG preferred, group titles kept; a layer is requested in EPSG:3857, else 4326, else the first CRS QGIS knows; connections marked `service=wms` skip the WMTS attempt, unmarked ones fall back to WMS when WMTS finds nothing. This makes the previously empty geodienste.ch Gefahrenkarten "WM(T)S" entry list its 51 layers. Export as GeoTIFF stays WMTS-only and now says so for a WMS layer. **Fixed:** the WFS and ArcGIS REST tabs read the *first* gateway of a source's type, not the selected combo entry - invisible until a source had several WFS connections (geodienste.ch now has three). **Known limitation:** the geodienste.ch servers are MapServer and silently ignore `CQL_FILTER`, so the WFS Filter panel does not narrow the query grid on them (Add to map is unaffected). 553 tests pass on QGIS 3.44.14; not yet exercised by hand in a live QGIS GUI session.
