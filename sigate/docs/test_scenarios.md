# SIGate — Human Test Scenario Collection

This is the manual test plan for everything that could not be verified without a real QGIS installation, real network access, and (for the export feature) a real GDAL ≥ 3.11 install — the gap every automated test in this project explicitly can't close, since the sandbox these run in has no route to any real geodata server and no GDAL CLI tools at all. Automated tests cover the underlying logic against realistic simulated data; these scenarios confirm the real thing actually works the same way, end to end, with a real server on the other end.

**Prerequisites before starting**: SIGate installed and enabled (see the Install Guide), a real internet connection able to reach `data.geopf.fr`, a working 7-Zip installation (for the archive scenarios), and GDAL ≥ 3.11 with `gdal`/`gdal_translate` on PATH (for the WM(T)S export scenarios specifically — check with `gdal --version` in a terminal before starting those).

For each scenario: follow the steps, compare against "Expected result," and note anything that diverges — including things that technically "work" but feel wrong, confusing, or slow. Where a scenario says to check the **Log Messages panel**, that's **View → Panels → Log Messages Panel** in QGIS, then the **SIGate** tab within it (it only appears once something has actually been logged — if it's missing entirely where one is expected, that's itself a real finding, not just "nothing to see").

This document is organized by tab, then by feature within each tab, finishing with a condition-matrix appendix that cross-references scenarios against combinations (central repo alone vs. repo+destination, file already present or not, network/permission failures) that don't each get their own numbered scenario but must still be exercised.

---

## TC1 — Installation and activation
1. Install SIGate per the Install Guide.
2. Open **Layer → Data Source Manager…**

**Expected**: three new categories appear — SIGate WMS/WMTS, SIGate Bulk Download, SIGate WFS — alongside QGIS's built-in ones.

---

## TC1b — Connection manager: CRUD
1. Open any of the three SIGate tabs. Click **New...** next to the connection dropdown.
2. Fill in a key, display name, country, and a base URL (any placeholder values are fine — you don't need a real working endpoint). Click OK.
3. Confirm the new connection appears in the dropdown and is selected.
4. Click **Edit...**, change the display name, click OK. Confirm the change is reflected in the dropdown.
5. Click **Save...**, save it to a `.json` file. Click **Delete**, confirm the deletion prompt, and confirm it's removed from the dropdown.
6. Click **Load...**, select the `.json` file saved in step 5. Confirm the connection reappears.
7. Select the built-in IGN connection. Confirm **Edit** and **Delete** are both disabled/grayed out for it.

**Expected**: all of the above work as described, and — importantly — closing and reopening Data Source Manager afterward should still show whatever connections you added (not just IGN), confirming they're actually being saved rather than only held in memory for the current tab instance.

---

## TC1c — Connection manager: one entry per gateway instance, not per source
1. Open the **SIGate WMS/WMTS** tab specifically. Open the connection dropdown.

**Expected**: two distinct IGN entries, not one — labelled something like **"IGN (France) — public"** and **"IGN (France) — private"**. If you only see a single "IGN (France)" entry with no role suffix, that's a real regression (this exact bug — one merged entry silently combining both gateways' layers — was reported and fixed once already).

2. Select the **public** entry, confirm the layer list loads (should be a large, real list — orthophoto, etc., not a single placeholder entry).
3. Select the **private** entry.

**Expected**: this one requires the apikey-gated endpoint to actually work — see TC2b below for what "working" means here specifically. At minimum, selecting it should attempt to load a genuinely different layer list than the public one (e.g. SCAN25 topographic layers), not silently show the same list or an empty one.

4. Open the **SIGate WFS** and **SIGate Bulk Download** tabs and check their own connection dropdowns.

**Expected**: **WFS** shows **one plain "IGN (France)"** entry (no role suffix - this source has only one WFS gateway instance, so the "one entry per instance" behavior is unaffected here) **plus one "Gefahrenkarten (geodienste.ch, Switzerland)"** entry (a second, distinct WFS source, added for avalanche/flood/landslide/rockfall hazard-zone data) - two entries total, not one, now that a second WFS source exists. **Bulk Download** shows **fifteen** entries total: one plain **"IGN (France)"** (its single atom instance) plus twelve swisstopo entries disambiguated by role — four topo/DEM basics (**"— swissALTI3D"**, **"— swissTLM3D"**, **"— swissSURFACE3D"**, **"— SWISSIMAGE"**) and eight outdoor-activity route networks (**"— ski touring routes"**, **"— snowshoe routes"**, **"— hiking network (Wanderland)"**, **"— winter hiking trails"**, **"— snowshoe trails (ASTRA)"**, **"— cycling network (Veloland)"**, **"— mountain bike network"**, **"— skating network"**) — plus one **"Gefahrenkarten (geodienste.ch, Switzerland) — Gefahrenkarten (all hazard types)"** entry (a second Swiss source's own single stac instance) — plus one plain **"Geonorge (Norway)"** entry (its single `geonorge_catalog` instance - unlike the others, this one opens onto a curated Theme list rather than tiles or files directly; see TC3d). If Bulk Download shows only the single IGN entry, the atom/stac/geonorge_catalog generalization silently isn't wired in - a real regression, not expected behavior.

---

## TC1d — Avalanche hazard connections (geodienste.ch), across three tabs
Added directly in response to a question about avalanche hazard zone data specifically. Confidence genuinely differs per gateway here (see `docs/candidate_sources.md`'s own note on this source) - this scenario is worth running fully rather than assuming all three behave the same.

1. Open **SIGate WMS/WMTS**, switch to the **"Gefahrenkarten (geodienste.ch, Switzerland)"** connection.

**Expected**: the best-confirmed of the three - a real, substantial layer list should load, including at minimum `gefahrengebiet_lawine` (the legally-binding avalanche hazard zone), `gefahrenhinweisgebiet_lawine` (the indicative one), and several `intensitaet_lawine_*` layers (one per return-period scenario). Add `gefahrengebiet_lawine` to the map and confirm it actually renders (a real polygon layer covering Swiss terrain, not an empty/error response).

2. Open **SIGate WFS**, switch to the same connection.

**Expected**: this one has a known, flagged uncertainty - the endpoint itself is confirmed live (real XML response), but its exact FeatureType names weren't independently verified before this was wired in. Confirm the feature-type list populates with *something* real (not empty, not an error) - if it does, this uncertainty is resolved and worth removing from `candidate_sources.md`; if it doesn't (empty list, or an error), that's a real, useful bug report - check the Log Messages panel for the actual GetCapabilities response received.

3. Open **SIGate Bulk Download**, switch to the same connection's stac entry ("Gefahrenkarten (geodienste.ch, Switzerland) — Gefahrenkarten (all hazard types)").

**Expected**: this is the weakest-confirmed of the three - the Items URL shape was inferred, not fetched live, before this was wired in. Confirm it loads a real listing (even one entry is enough to confirm the shape is right) rather than an error - if it errors, check whether the actual STAC API root differs from `https://www.geodienste.ch/stac` (e.g. a version path segment, or a different root entirely) and report back with what the real shape turns out to be.

---

## TC2 — WM(T)S tab: add a layer (public, no auth)
1. Open Data Source Manager → **SIGate WMS/WMTS**. Confirm the **public** IGN connection is selected (see TC1c).
2. Confirm the layer list populates with a real, substantial number of layers — IGN's public WMTS genuinely offers many, not just one. Type into the filter box to narrow the list, e.g. `orthophoto`.
3. Select the "HR.ORTHOIMAGERY.ORTHOPHOTOS.L93" layer specifically (search for it if the list is long), click **Add to map**.

**Expected**: the layer is added to your map and renders correctly — French high-resolution orthophoto imagery. Pan/zoom to somewhere in France to confirm imagery actually loads.

**Specifically check**: the layer's CRS. Right-click the layer → **Properties → Information**, confirm it shows **EPSG:2154** (Lambert-93), not EPSG:3857 (Web Mercator) — this is derived live from the server's own declared tile-matrix-set definitions.

4. Try adding a *different* layer from the list using Web Mercator instead of Lambert-93.

**Expected**: that layer's CRS should correctly show **EPSG:3857** — confirming CRS is genuinely derived per-layer, not defaulting to whatever was tested first.

---

## TC2b — WM(T)S tab: private/apikey-gated layer (authcfg)
1. Select the **private** IGN connection (TC1c). Select a layer (e.g. a SCAN25 topographic layer). Click **Add to map**.

**Expected**: the layer is added and its tiles actually render — not just an empty/gray layer. This is the one open item in this project flagged as "implemented, not yet fully live-verified" — a real bug in QGIS's own tracker shows at least one other provider silently dropping this exact auth method, so this is genuinely worth checking carefully, not just glancing at.

2. If tiles don't render: open **Settings → Authentication → Authentication Configurations** in QGIS itself. Confirm a SIGate-provisioned entry exists there (method "API Header").

**Expected**: if tiles fail to render but a real authcfg entry does exist with what looks like the right header/value, that specifically confirms the open item above — report it precisely (what you saw fail, and confirmation the authcfg entry itself was created correctly), since that pinpoints where in the chain it broke.

---

## TC2c — WM(T)S tab: Export clipped area as GeoTIFF — basic export
1. Pan/zoom the map to a modest-sized real area in France (a few km across — small enough to export quickly).
2. Select a layer in the WM(T)S tab (public IGN orthophoto is a good first test). Click **Export clipped area as GeoTIFF...**.
3. If a zoom-level picker appears (see TC2f below for when it should/shouldn't), pick any level and confirm. Choose a save location and filename. Wait for the export dialog to finish.

**Expected**: a progress dialog appears (single indeterminate bar, a status label naming whichever GDAL command is currently running) and does not freeze QGIS's UI — you should be able to move other windows, etc., while it runs. On completion, a real `.tif` file exists at the chosen path, and it's automatically added as a layer, showing genuinely clipped imagery matching your canvas view (not the whole country, not an unrelated area).

4. Open the **Log Messages panel → SIGate tab**.

**Expected**: the real `gdal raster clip`/`gdal_translate` commands that were actually run are shown, in full, with real paths and arguments — not a summary, the literal command line.

5. Check where the exported file's supporting files landed: look in the **same folder as the output file itself** for a `wmts_def_*.xml` file.

**Expected**: the definition file is sitting right next to your chosen output — not in a hidden QGIS profile settings folder somewhere else. (This was a real, reported bug once — if it's back, that's a regression worth flagging clearly.)

---

## TC2d — WM(T)S tab: Export clipped area — CRS correctness
1. Set your QGIS project's CRS to something *different* from the layer you're about to export (e.g. project in EPSG:4326, exporting a Lambert-93 IGN layer).
2. Repeat TC2c's export with this mismatched project CRS active.

**Expected**: the exported file still covers the correct real-world area you were looking at on screen — not an area shifted or distorted by the CRS mismatch. This confirms the canvas extent is being correctly reprojected into the layer's own CRS before clipping, not naively assumed to already match.

---

## TC2e — WM(T)S tab: Export clipped area — size warning
1. Zoom *out* significantly (a large region, tens of km across or more) before exporting, at a layer's finest available resolution.
2. Click **Export clipped area as GeoTIFF...**.

**Expected**: before any file dialog or GDAL command runs, a warning appears stating the estimated pixel dimensions and an approximate uncompressed size, asking to continue. Cancel it — confirm nothing was exported and no output file was created. Repeat and click Continue this time — confirm the export then proceeds normally.

3. Zoom back in to a small area and export again.

**Expected**: no warning at all for a small, ordinary export — it should proceed straight to the zoom-level picker (if applicable) or file dialog with no extra prompt.

---

## TC2f — WM(T)S tab: Export clipped area — zoom-level picker
1. Find a layer whose tilematrixset has more than one real zoom level available (most real WMTS layers do). Export it (TC2c).

**Expected**: a picker appears before the file dialog, listing each available level with its own resolution and an estimated size *for your current view specifically* (not a generic table) — coarsest at the top, finest at the bottom, finest pre-selected by default. Pick a **coarser** level deliberately (not the default) and complete the export.

**Specifically check**: the resulting file's actual pixel dimensions/resolution should reflect the level you picked, not the finest one — open it in QGIS and check its properties, or compare file size against what exporting the same area at the finest level produces. This confirms the choice genuinely constrains what GDAL fetches, not just a size estimate followed by silently downsampling a full-resolution fetch.

2. Cancel out of the picker itself (not the whole export, just decline the dialog).

**Expected**: the export stops cleanly — no file created, no lingering progress dialog.

---

## TC2g — WM(T)S tab: Export clipped area — cancellation and error cases
1. Start an export of a reasonably large area (large enough that it takes a few seconds, not instant). Click **Cancel** on the progress dialog shortly after it starts.

**Expected**: the dialog reports that cancellation was requested but a command already in progress will still run to completion (this is a documented, real limitation — GDAL's CLI tools offer no clean way to interrupt a command mid-flight). The dialog eventually closes; check whether a partial/complete file was left behind either way and note which happened.

2. Pick an output path in a location you know is **not writable** (e.g. a system-protected folder, if your OS setup allows testing this safely) and attempt an export.

**Expected**: a clear failure message naming the real error, not a silent failure or a generic crash.

3. If you can arrange for `gdal`/`gdal_translate` to be temporarily unavailable on PATH (e.g. rename the binary, or test on a machine without GDAL installed) and attempt an export:

**Expected**: a real, if not especially friendly, OS-level "command not found" style error surfaces through the failure dialog — this is a known, documented gap (no proactive "GDAL not found" check exists yet, unlike the 7-Zip detection the other tabs have). Confirm it at least fails visibly rather than hanging or crashing QGIS.

---

## TC2h — WM(T)S tab: Export clipped area — revisiting the same output
1. Export the same area/layer/zoom-level combination to the exact same output path a second time.

**Expected**: either a clean overwrite prompt, or a silent overwrite — confirm which, and that the resulting file is genuinely fresh (correct content), not a stale leftover from the first export.

---

## TC3 — Bulk Listing tab: browse
1. Open Data Source Manager → **SIGate Bulk Download**.
2. The tab should load IGN's Atom feed automatically and show a list of entries (mostly directories — product categories).
3. Double-click into a few directories (e.g. navigate toward `RGEALTI`) to go deeper.
4. Click **< Back** a couple of times.
5. Navigate into a category likely to have more than 50 entries (IGN's page size), and use **Next page >** / **< Prev page** to move through the listing.

**Expected**: navigation works smoothly, entries show correctly as DIR or FILE with sizes where available, and Back correctly returns to the previous listing. The page label shows the current page, total pages, and total entry count; Next/Prev correctly enable and disable at the first/last page. Navigating into a new folder resets back to page 1.

---

## TC3b — Bulk Listing tab: filtering
1. Navigate to a category with a modest number of entries. Type part of an entry's name into the Filter field.

**Expected**: the list narrows instantly, with no delay (filters only the currently-loaded page, no new network request).

2. Navigate to a category likely to have more entries than fit on one page. Type a name you know exists but that isn't visible on the current page. Click **Filter (all pages)**.

**Expected**: if the listing is large, you're asked to confirm before it fetches everything; once confirmed (or immediately, for a smaller listing), the matching entry is found and shown even though it wasn't on the page you started on. Page navigation buttons become disabled while this filtered view is active. Click **Clear** to return to normal single-page browsing.

---

## TC3c — Bulk Listing tab: browse and download via a STAC connection (Switzerland)
1. Open Data Source Manager → **SIGate Bulk Download**. Switch the connection dropdown to **swisstopo (Switzerland) — swissALTI3D**.
2. The tab should load a listing of tiles (each entry a DIR — one STAC Item per tile, not a file itself).
3. Double-click into one tile entry.

**Expected**: descending into a tile shows its actual downloadable Asset(s) as FILE entries underneath, sizes shown where the server reports them (`file:size`) - this is the STAC equivalent of TC3's directory-into-files navigation, just one level of Item→Asset instead of Atom's arbitrary-depth resource tree. Note the page label may not show a total entry count the way IGN's Atom listing does (STAC doesn't always report one up front) - that's expected, not a bug.

4. Select a tile Asset, set a **Central repository**, click **Download selected**.

**Expected**: same download pipeline as any other Bulk Listing download (progress dialog, nested `swisstopo (Switzerland)/.../` subfolder structure, added to the map if it's a recognizable format) - see TC4 for the general shape. The checksum step specifically should succeed silently on a match (see TC7b, updated for this connection specifically).

5. Switch the connection dropdown between swissALTI3D and a few of the other swisstopo entries — at least one other topo/DEM one (swissTLM3D, swissSURFACE3D, or SWISSIMAGE) and at least one outdoor-activity one (e.g. "ski touring routes" or "hiking network (Wanderland)") — and confirm each one loads its own genuinely different listing, not a stale/cached one left over from whichever was selected previously. The route-network entries are vector line data rather than raster tiles, so their Item/Asset structure may look different in practice (e.g. one Item covering a larger area, fewer Assets per Item) - that's expected, not a bug, and worth noting if it looks surprising.

---

## TC3d — Bulk Listing tab: browse and download via the Norway (Geonorge) connection
Directly answers a follow-up question about whether a two-level hierarchy could be built from Norway's otherwise-flat catalog. Worth understanding what you're looking at here specifically, since it's shaped differently from every other Bulk Listing connection: Level 1 is a curated Theme list (not discovered from the live catalog - see `gateways/geonorge_catalog.py`'s own docstring), Level 2 is real datasets carrying an Atom feed, and everything past that is a completely ordinary Atom feed, parsed by the exact same code IGN's connection uses.

1. Open Data Source Manager → **SIGate Bulk Download**. Switch the connection dropdown to **"Geonorge (Norway)"**.

**Expected**: the tab loads a listing of Theme entries (DIR) - e.g. "Høydedata", "Natur", "Samfunnssikkerhet". This list comes from a fixed, curated set built into the plugin, not a live query - it should load instantly (no real network delay), unlike every other level in this tab.

2. Double-click into **"Høydedata"** (or any other theme).

**Expected**: now a real, live network fetch happens - the tab is pulling the *entire* Geonorge catalog (confirmed ~8,637 records as of this session) as one CSV export and filtering it down to just this theme's datasets that carry a real Atom feed. This may take a few seconds longer than any other connection's first page load, precisely because it's fetching everything rather than one page - if it seems to hang far longer than a few seconds, or errors, that's worth reporting (the catalog may have grown enough to need a different approach, or the CSV export URL/shape may have changed).

3. Double-click into one of the resulting dataset entries.

**Expected**: from this point on, you're looking at a completely ordinary Atom feed - browsing, pagination, and file/checksum behavior should look and behave identically to TC3's IGN walkthrough, because it's the exact same code handling it. If a dataset's feed turns out to have zero entries or an unexpected shape, that's a real per-dataset data-quality issue on Geonorge's end, not something this plugin can fix - but worth noting which dataset it was.

4. Select a file, set a **Central repository**, click **Download selected**.

**Expected**: same download pipeline as any other Bulk Listing download - see TC4 for the general shape, TC7b for checksum behavior (Norway's feeds may or may not carry an `.md5` sidecar per file; either is normal).

---

## TC4 — Bulk Listing: download a simple (non-archive) file — central repo only
1. Navigate to a product that delivers a single non-archive file, or use any single small file entry.
2. Select it. Set a **Central repository** folder (any empty test folder). **Leave Destination blank.**
3. Click **Download selected**.

**Expected**: a progress dialog appears (two bars — overall "File 1 of 1", and this file's own byte progress) and does not freeze the UI. The file lands **inside a subfolder structure**, not directly in the repo root — check the actual path: it should be `<repo>/IGN (France)/<category>/<layer or resource name>/<filename>`, not flat. If it's a recognizable raster or vector format, it's added to the map automatically with no extraction step.

---

## TC4b — Bulk Listing: download a simple file — repo *and* destination both set
1. Repeat TC4, but this time set **both** Central repository and Destination to two different empty folders.

**Expected**: the file downloads into the repo (in the same nested subfolder structure as TC4), then is *copied* into the destination folder directly (flat, no subfolder inside destination — the subdirectory structure only applies to the central-repo cache location, not an explicit destination you picked). The layer that gets added references the destination copy, not the repo copy.

---

## TC5 — Bulk Listing: download and extract an archive — everything is kept
1. Navigate to an RGE ALTI département delivery. Select it. Set both **Central repository** and **Destination**. Click **Download selected**.
2. When prompted "Extract N file(s) from [archive] into a folder named after the archive?", click **Yes**.

**Expected**:
- The archive itself downloads into the central repository (inside the same `source/category/layer` subfolder structure as TC4).
- The confirmation count should now reflect **every** file in the archive, not just the `DONNEES` folder's contents — `METADONNEES` and `SUPPLEMENTS` files (readmes, XML/HTML metadata, tile-index shapefiles) count too now.
- Extraction lands inside **a new subfolder named after the archive itself** within the destination (e.g. `<destination>/RGEALTI_D073_.../1_DONNEES_LIVRAISON_.../RGEALTI_.../*.asc`) — check this subfolder exists and is named sensibly after the real archive filename, not just dumped flat into the destination root.
- Open that subfolder directly and confirm the metadata/readme files are genuinely present on disk (not silently discarded) — but only the actual raster/vector data files got added as map layers, not the readmes (a `.txt`/`.xml` file should not appear in your Layers panel).
- If multiple tiles were extracted, you should be asked whether to build a single mosaic — confirm this offer appears, and that accepting it produces one loadable layer rather than many, and that the mosaic file itself, plus a `.ovr` pyramid sidecar, both land inside that same archive-named subfolder.

---

## TC5b — Bulk Listing: revisiting an already-extracted archive
1. Repeat TC5's exact same download+extract into the exact same destination a second time.

**Expected**: if a mosaic was already built last time, it should be recognized and reused rather than rebuilt/re-offered — check the Log Messages panel to confirm no fresh `gdalbuildvrt` command ran this time (only `gdaladdo`, if the pyramid was somehow missing, which it shouldn't be if TC5 completed cleanly).

---

## TC6 — Bulk Listing: split (multi-part) 7z archive
1. Find a delivery known to ship as multiple `.7z.001`/`.7z.002`/... parts (the 1m-resolution RGE ALTI product for a large département is a good candidate).
2. Select the entry, download it as in TC5.

**Expected**: all parts download (check the central repository subfolder — you should see `.7z.001`, `.7z.002`, etc., co-located, not just one), and extraction succeeds as a single combined archive into one archive-named subfolder (named after the base archive name, not literally including `.001` in the folder name — check this specifically, since a naming mistake here would be a real, previously-caught-in-code-review class of bug).

---

## TC7 — Bulk Listing: already-downloaded detection
1. Repeat TC4 or TC5 for the same entry a second time, with the same central repository folder.

**Expected**: you're asked whether to use the existing file(s) rather than re-downloading — checked against the *actual nested subfolder path* the file really lives at, not a flat guess. Confirm both choices work (using existing skips the download entirely — check the Log Messages panel shows no fresh download command; declining triggers a fresh download that overwrites correctly).

2. For a split-archive delivery (TC6): delete just *one* of the parts from the repo folder manually, then repeat the download.

**Expected**: the plugin correctly detects the set is *incomplete* and re-downloads (at minimum the missing part, likely the whole set) — it must not treat a partial set as "already downloaded."

---

## TC7b — Bulk Listing: checksum verification (md5 sidecar, and inline sha256)
1. Find a delivery you have reason to believe publishes a `.md5` checksum sidecar alongside its real download link (RGE ALTI deliveries are the best-documented case for this) and download it normally.

**Expected**: nothing looks different if the checksum matches — the file is simply kept and used normally. Check the Log Messages panel or Python Console for any sign the checksum file was fetched (there's no dedicated status message for a *successful* match, by design, but you can confirm indirectly: temporarily point at a delivery with a checksum, and compare against one without).

2. If you can find (or construct, e.g. via a test/staging feed) a case where the published checksum genuinely does **not** match the real file content:

**Expected**: the download is rejected — a clear failure message, and the bad file is deleted from disk, not left behind looking valid. This is the one integrity scenario worth deliberately trying to break, since a checksum *mismatch* is much harder to stumble into by accident than a match.

3. Download something from a source/layer that has **no** checksum available at all (most WFS file-index layers, and most Atom entries without a `.md5` sibling).

**Expected**: downloads proceed completely normally, no warning or delay related to checksums at all — the check is silently skipped, not treated as a failure.

4. Repeat step 1's "matches, nothing looks different" case against a **swisstopo (STAC) connection** instead (TC3c) — every swissALTI3D/swissTLM3D/swissSURFACE3D/SWISSIMAGE asset carries an inline checksum, so this should be the common case there, not something you need to specifically hunt for the way the md5-sidecar case sometimes is for Atom.

**Expected**: same outcome as step 1 (kept silently on a match), but verified via a genuinely different code path - no external `.md5` file gets fetched at all for a STAC download (check the Log Messages / Python Console to confirm: you should NOT see a second request to a `.md5`-suffixed URL the way TC7b step 1 might show for an Atom download - the checksum was already present in the same STAC response that listed the file itself).

---

## TC8 — Bulk Listing: size warning
1. Select several large entries at once (or one very large département) so the combined size clearly exceeds 1 GB (the default threshold).
2. Click **Download selected**.

**Expected**: a warning dialog appears showing the approximate total size before downloading anything, with Continue/Cancel options. Cancel it — confirm nothing downloaded. Repeat and Continue — confirm the download proceeds.

---

## TC8b — Bulk Listing: cancelling a download in progress
1. Start downloading something large enough to take at least 10-15 seconds. Click **Cancel** on the progress dialog partway through.

**Expected**: the current file's download stops promptly (not after finishing the whole file) — this pipeline supports cancellation at chunk granularity, not just between files. Confirm no corrupt/partial file is left behind at the final destination path (a partial download should not silently masquerade as a complete one). If multiple files were queued, confirm none of the *later*, not-yet-started files were downloaded either.

---

## TC8c — Bulk Listing: network/permission error cases
1. Start a download, then disconnect your network connection (or otherwise force a failure) partway through.

**Expected**: a clear failure message naming the real error, not a silent hang or an unhandled crash. Check the Log Messages panel for the full diagnostic detail (not just the shorter user-facing message).

2. Set the central repository to a path you know is not writable (e.g. a read-only location, if your OS setup allows testing this).

**Expected**: a clear, specific error about the destination not being writable, surfaced before or immediately upon attempting the download — not a generic crash.

---

## TC9 — WFS tab: browse and filter
1. Open Data Source Manager → **SIGate WFS**.
2. Confirm the feature-type list populates (a real, possibly large list from IGN's live WFS capabilities — expect this to take a moment).
3. Type `lidar` into the filter field.

**Expected**: the list narrows to feature types whose name or title contains "lidar" — confirm `IGNF_LIDAR-HD_METADONNEE:metadata` appears. (The older `IGNF_*-LIDAR-HD:dalle` layers were retired by IGN; they should *not* appear.)

---

## TC10 — WFS tab: add a plain vector layer
1. Select a feature type you'd expect to be manageable in size (avoid the LiDAR HD metadata layer — about 500,000 features, better suited to TC11).
2. Leave any filter blank. Click **Add to map**.

**Expected**: QGIS's own native WFS provider takes over and loads the layer normally — confirm it actually renders features and pans/zooms like any other QGIS vector layer.

---

## TC10b — WFS tab: selection-restricted Add to map
1. Query a feature type (**Query features**, not Add to map). Select 2-3 specific rows in the results grid (not all of them). Click **Add to map**.

**Expected**: the layer that gets added contains **only** the rows you selected, not the whole (possibly filtered) result set — confirm the feature count in the added layer matches your selection count exactly. Right-click the layer → check its filter/query string if you want to confirm it's a `field IN (...)` restriction.

2. Deselect everything (click empty space in the grid) and click **Add to map** again.

**Expected**: falls back to adding the whole current filtered result (or the whole unfiltered layer, if no filter is active) — the previous behavior, unaffected by there being no selection.

---

## TC10c — WFS tab: downloading linked files
1. Select `IGNF_LIDAR-HD_METADONNEE:metadata`, zoom the map to a small area (a few km²), and click **Query features**.

**Expected**: a results table appears with real feature data (columns include `url_mnt`, `url_mns`, `url_mnh`, `url_npl`). **Download selected** becomes enabled, since these columns hold web addresses.

2. Select one result row. Set a central repository (and destination, if testing that combination). Click **Download selected**.

**Expected**: a dialog lists MNT, MNS, MNH and NPL with the three rasters ticked and NPL unticked. Cancelling it downloads nothing. Accept with MNT only: one `.tif` (about 15 MB, named from the `FILENAME=` in the link, e.g. `LHD_FXX_0998_6542_MNT_O_0M50_LAMB93_IGN69.tif`) downloads via the same background-task pipeline as the Bulk Download tab, lands under `source/raster/layer` (here "layer" is the WFS typename, e.g. `IGNF_LIDAR-HD_METADONNEE_metadata`), and is added to the map.

3. Select 2+ rows and tick MNT, MNS and MNH.

**Expected**: all tiles download; if offered a mosaic, it is offered **separately for each product** (three mosaics named per product), never one mosaic mixing MNT/MNS/MNH.

4. Select one row and tick only NPL (point cloud, about 340 MB — check the size warning behaviour: it may not appear because the raster links carry no declared size).

**Expected**: a `.copc.laz` lands under `source/point_cloud/layer` and is added to the map as a **point-cloud layer**.

5. Look at the Layers panel after steps 2–4.

**Expected**: nothing is loose at the top level. Layers sit in `IGN (France) › IGNF_LIDAR-HD_METADONNEE_metadata › MNT` (and `MNS`, `NPL` groups for the other products); downloading more tiles reuses those groups rather than creating duplicates; a built mosaic appears instead of its tiles.

6. Repeat with a plain vector layer instead.

**Expected**: **Download selected** stays disabled after querying, since that layer's rows have no download link. **Add to map** on a WFS layer, and adding a WM(T)S layer, put the layer in a group named after the source (`IGN (France)`).

**Expected**: **Download selected** stays disabled after querying, since that layer's rows have no download link.

---

## TC10d — ArcGIS REST tab: browse, filter, add

1. Open Data Source Manager → **SIGate ArcGIS REST**; connection "Arpa Piemonte - SIVA avalanches (Italy)". **Expected:** 14 queryable layers, group layers shown as `GROUP › Layer`.
2. Select "Valanghe documentate". **Expected:** polygon, EPSG:32632, 14 fields.
3. **Query features.** **Expected:** `1-200 of 4091`; Next page works.
4. **Filter...** → `comune = '<a value seen in the results>'`, Query. **Expected:** a smaller count.
5. Tick **Limit to current map extent** with the map zoomed on a valley; Query. **Expected:** only features in view.
6. **Add to map** with nothing selected. **Expected:** one vector layer, in a group named after the source, with the filtered feature count.
7. Select two rows, **Add to map**. **Expected:** a layer named "… (2 selected)" with 2 features.
8. Enter an invalid where clause. **Expected:** a clear "Query failed" message and an empty grid.

## TC10e — WM(T)S tab: a plain WMS source

1. Open **SIGate WM(T)S**; connection "Valle d'Aosta - avalanche cadastre (Italy)". **Expected:** 34 layers listed (no WMTS involved).
2. Select "Catasto Valanghe", **Add to map**. **Expected:** a raster layer drawn from the WMS, in a group named after the source.
3. Choose the Gefahrenkarten "WM(T)S" connection. **Expected:** about 51 layers, including `gefahrengebiet_lawine`.
4. With a WMS layer selected, **Export clipped area as GeoTIFF**. **Expected:** a message that export needs a tiled (WMTS) layer.

## TC10f — WFS tab: several WFS connections in one source

1. Open **SIGate WFS**; the combo lists three geodienste.ch entries (Gefahrenkarten, Naturereigniskataster, Naturereigniskataster umfassend).
2. Pick each. **Expected:** 40, 13 and 16 feature types respectively - each its own list.
3. On Naturereigniskataster pick `prozessraum_lawine`, **Query features**, then **Add to map**. **Expected:** 28,066 features, EPSG:2056. (A Filter-panel filter does not narrow the query grid on this server; it does on Add to map.)

## TC11 — WFS tab: the Filter panel — attribute expressions
1. Select `IGNF_MNS-LIDAR-HD:dalle` (or another large layer). Click **Filter...** *without* querying first.

**Expected**: the panel opens showing real field names and sample values anyway — it auto-queries a first page internally before showing itself, rather than opening empty.

2. In the SQL editor, type a WHERE-clause expression directly (e.g. `id_chantier = '137'`, using a value you have reason to expect exists). Click **Query features** (using the whole filter, not a row selection).

**Expected**: the results grid empties immediately when the query starts (never shows stale results from before), then repopulates with only matching features — confirm the count is small and specific, not the full dataset. The status bar names that a filter was actually applied.

3. Click **Add to map** with the same filter still active (no row selection).

**Expected**: the added layer is similarly restricted to matching features — confirm via feature count, same as the CQL path.

---

## TC11b — WFS tab: the Filter panel — spatial predicates (`@map_extent`)
The two actions resolve the filter differently on purpose (CQL for the server, a QGIS expression for QGIS's own provider) — test both halves, and on a geographic-CRS layer (e.g. `IGNF_LIDAR-HD_METADONNEE:metadata`, EPSG:4326) as well as a projected one (e.g. a BDTOPO layer, Lambert-93).

1. Pan/zoom the map to a specific area with known features in it. Open **Filter...**. Type `WITHIN($geom, @map_extent)` (note: `WITHIN`, not `ST_Within` — the editor's own autocomplete should suggest the correct bare name; if it still suggests an `ST_`-prefixed name, that's a real regression). Click **Query features**.

**Expected**: this should now genuinely work — results are restricted to features actually within your current map view. Check the Log Messages panel or a network inspector if available to confirm the real CQL sent to the server contains an actual coordinate polygon, not the literal text `@map_extent`.

2. With the same filter still active, click **Add to map** instead (no row selection).

**Expected**: the added layer contains only features within the map view — **not** the whole layer. Compare the feature count with step 1's result for the same view (for the LiDAR HD metadata layer, a view around Chamonix gives about 100 tiles within and about 150 intersecting). Pan away afterwards: the already-added layer should keep exactly those features (the filter, not just the canvas, is restricting it). Right-click the layer → Filter/Properties to see the filter reads `WITHIN($geometry, geom_from_wkt('POLYGON((...))'))` with coordinates in lon,lat (x,y) order for a geographic CRS.

3. Repeat step 2 with `INTERSECTS($geom, @map_extent)` and with `ST_Within($geom, @map_extent)` (the `ST_` alias is rewritten to `WITHIN`).

**Expected**: INTERSECTS returns at least as many features as WITHIN; `ST_Within` behaves exactly like `WITHIN`.

---

## TC11c — WFS tab: expression editor — fields and values
1. Open **Filter...** after having queried a feature type at least once. Look at the **Fields** list.

**Expected**: real field names from the actual query result. Select a field, click **Load some** — sample values appear. Click **Load all** — every distinct value appears (may look the same as "some" for a small result set).

2. Type part of a value into the values filter box.

**Expected**: narrows instantly, no delay.

3. Double-click a field name, then double-click a value.

**Expected**: both insert into the expression editor at the cursor — the value correctly quoted (embedded quotes doubled, if you test one containing a `'`).

---

## TC11d — WFS tab: query pagination
1. Select a layer likely to have more features than one page. Set **Count** small (e.g. `10`).
2. Click **Query features**, then **Next page >** a few times, then **< Prev page** back.

**Expected**: each click fetches a genuinely different page (compare a few row values to confirm). Prev is disabled on the first page; Next disables once a page comes back shorter than Count.

---

## TC11e — WFS tab: filtering query results (client-side, current page only)
1. Query a layer. Type part of a value visible in the results table into **Filter results**.

**Expected**: the table narrows instantly to matching rows — no delay. Clear the field to restore the full page.

---

## TC11f — WFS tab: search ALL pages
1. Query a layer with Count small enough that a specific feature you know about isn't on the first page. Type its identifying value into **Filter results**. Click **Search ALL pages**.

**Expected**: if the layer is large, you're asked to confirm before fetching everything; once done, the feature is found even though it wasn't on the starting page. Page navigation disables while this filtered view is active — **Clear** returns to normal paging.

---

## TC11g — WFS tab: download ALL pages
1. Select `IGNF_LIDAR-HD_METADONNEE:metadata` with a small map extent (or another layer with download links). Click **Download ALL pages**. For a multi-link layer the product dialog appears first.

**Expected**: confirmation shown with the real total feature count if the server provides one. Once confirmed, every page is fetched and every feature with a download link is downloaded (background task, same progress dialog as Bulk Listing) and added as a layer, regardless of any filter currently displayed.

---

## TC11h — WFS tab: query failure and error messaging
1. Open **Filter...**, type a deliberately malformed expression (e.g. unbalanced parentheses, or a made-up function name), and click **Query features**.

**Expected**: a message box (not just a quiet status-bar update) clearly states the query failed and names the real server error, and explicitly mentions a filter was involved. The results grid should be empty (not showing stale results from a previous successful query) — confirm it doesn't look like "the filter did nothing," which would be misleading.

---

## TC12 — Settings dialog
1. Open SIGate's settings. Change the size-warning threshold to a different value (e.g. 5 GB). Click OK. Reopen the dialog.

**Expected**: the new value persists and shows correctly. Try setting it to a **negative** value directly if the field allows typing one.

**Expected**: rejected with a clear error, not silently accepted (a real validation was added for this).

2. Repeat for the 7-Zip path field, using both **Browse** (pick a real executable) and **Auto-detect**.
3. Change **Preferred language** to something other than English (e.g. Français). Click OK, reopen the dialog.

**Expected**: the selection persists. Switch to the WM(T)S tab and reload the swisstopo or Gefahrenkarten connection's layer list - real, human-translated layer titles should now appear (confirmed real on geo.admin.ch's own docs: `?lang=` genuinely changes the GetCapabilities response, not just a cosmetic label somewhere in this plugin).

4. **Restart QGIS entirely**, then reopen the settings dialog.

**Expected**: all three values (threshold, 7-Zip path, language) still persist across a full restart.

---

## TC12a — Data translation memory
For sources with no native language option (Atom/catalog entry titles) - distinct from TC12 step 3 above, which is about sources that *do* support asking the server directly.

1. Browse a Bulk Listing connection whose titles aren't in your preferred language (e.g. IGN's Atom feed, or Geonorge's Norwegian dataset titles) for a minute or two - enough to load a few pages/folders.
2. Open Settings, click **Discover & Save untranslated strings...**, save the file.

**Expected**: a JSON file is written, containing an object with the titles you actually saw as keys, each with an empty string value. If you browsed nothing yet, a message says there's nothing to translate instead.

3. Manually edit the saved file (or feed it to an LLM as intended) to fill in a few of the empty values with real translations. Save it.
4. Back in Settings, click **Load translations...**, select the edited file.

**Expected**: a message reports how many entries were updated (matching however many you filled in - entries you left empty shouldn't count). Close Settings, return to Bulk Listing, and reload the same connection/page.

**Expected**: the titles you translated now show the translated text; anything you didn't translate still shows the original. Select a translated entry and download it - the file that actually lands on disk, and its folder name, should use the **original** (untranslated) text, not the translated display label.

5. Repeat step 2 (Discover & Save) once more.

**Expected**: the strings you already translated in step 3 are no longer in the exported file - only genuinely still-untranslated ones are.

---

## TC12b — WM(T)S: export a clipped area as GeoTIFF - GDAL version check and real error messages
Directly answers a real reported failure ("Could not export the clipped area: Command '[...]' returned non-zero exit status 1.") - both fixes here should be visible now.

1. If you can arrange it, test against a GDAL install **older than 3.11** (or temporarily rename/hide `gdal` from PATH).

**Expected**: the export fails immediately with a clear message naming the actual GDAL version found (or that GDAL couldn't be found at all) and stating the 3.11 requirement - not a generic subprocess error, and not a partial attempt at the clip first.

2. With a real GDAL >= 3.11, attempt an export that you have reason to expect might fail (e.g. a bbox with no real tile coverage for that specific layer/zoom, or a deliberately malformed layer choice).

**Expected**: if it does fail, the warning dialog's message now includes GDAL's own real error text (e.g. a specific "no such TileMatrixSet" or driver-level complaint), not just "returned non-zero exit status 1." Check the Log Messages panel too - the actual `gdal`/`gdal_translate` commands run should be visible there regardless of outcome (unchanged from before this fix).

---

## TC12c — Target paths persist between sessions
1. In the Bulk Listing tab, set a **Central repository** folder (and a **Destination**, if testing an archive workflow).
2. Close Data Source Manager entirely, reopen it, go back to Bulk Listing.

**Expected**: both fields are pre-filled with what you set before.

---

## TC12d — Command visibility (Log Messages panel)
1. Perform any archive extraction (TC5) or WM(T)S export (TC2c). Open **View → Panels → Log Messages Panel**, check the **SIGate** tab.

**Expected**: the real external commands actually run (`7z x ...`, `gdalbuildvrt ...`, `gdaladdo ...`, `gdal raster clip ...`, `gdal_translate ...` as applicable) are logged there, in full — real paths, real arguments — before each one runs, not summarized or paraphrased.

---

## TC13 — Uninstall
1. Disable or uninstall SIGate via the Plugin Manager.
2. Reopen Data Source Manager.

**Expected**: all three SIGate categories disappear immediately, with no leftover error or crash.

---

## Condition matrix — cross-reference before signing off

The scenarios above cover most of this implicitly; use this table to confirm every combination was actually exercised somewhere, not just assumed. Tick each cell as covered once you've genuinely seen that specific combination happen, not just the general feature.

| Condition | Bulk Listing simple file | Bulk Listing archive | WFS file-index download | WM(T)S export |
|---|---|---|---|---|
| Central repo only, no destination | TC4 | n/a (destination required for archives) | TC10c step 2 (repo only) | n/a (own save dialog, not repo/destination) |
| Both repo and destination set | TC4b | TC5 | TC10c (repeat with destination set) | n/a |
| File/output already present | TC7 step 1 | TC7 step 1, TC5b | repeat TC10c a second time | TC2h |
| Split/partial file present | TC7 step 2 | TC7 step 2 | n/a (WFS downloads are always single files) | n/a |
| Network failure mid-transfer | TC8c step 1 | repeat TC8c during an archive download | repeat TC8c during a WFS download | TC2g step 3 (GDAL-missing case; a genuine mid-clip network failure isn't really possible since GDAL fetches tiles itself) |
| Unwritable destination | TC8c step 2 | repeat TC8c step 2 with destination set instead of repo | repeat TC8c step 2 | TC2g step 2 |
| Checksum available and matches | n/a | TC7b step 1 | repeat TC7b step 1 against a WFS row carrying an md5 field, if you can find one | n/a |
| Checksum available and mismatches | n/a | TC7b step 2 | same, if constructible | n/a |
| No checksum available | TC4 (implicitly - most sources) | most archives (implicitly) | TC10c (implicitly, most WFS layers) | n/a |
| Cancelled mid-operation | TC8b | TC8b (during extraction specifically - try cancelling after the archive itself has downloaded but before/during extraction) | repeat TC8b during a WFS download | TC2g step 1 |
| Size warning triggered | TC8 | TC8 | n/a (no size warning currently exists for WFS downloads specifically - note if this feels like a gap) | TC2e |
| Selection vs. no selection | n/a | n/a | TC10b | n/a (zoom-level picker, TC2f, is the closest analog) |

---

## Reporting results

For each test case, note: **Pass**, **Pass with issues** (describe what felt off even if it technically worked), or **Fail** (describe exactly what happened, and if possible, any error text from the Python Console — **Plugins → Python Console** — since that will show the full traceback SIGate logs for any handled or unhandled error, and the Log Messages panel's SIGate tab for the exact external commands that ran up to the point of failure).
