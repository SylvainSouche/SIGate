# SIGate — User Guide

SIGate adds four new categories to QGIS's **Data Source Manager** (**Layer → Data Source Manager…**), each for a different way of getting geospatial data from configured national mapping agencies into your QGIS project. Currently configured: **France (IGN)**, **Switzerland (swisstopo)**, **Italian Alpine regions (Piemonte, Valle d'Aosta, Friuli Venezia Giulia)**, **Norway (NVE avalanche events)**, **INRAE avalanche data (France)**, **Germany (BKG TopPlusOpen)**, the **Netherlands (PDOK)**, and **Austria (basemap.at)** — see "Current coverage" at the end of this guide for exactly what that means in practice for each.

## Managing connections

All four tabs share the same **Connection** row at the top, matching the same New/Edit/Delete/Load/Save pattern QGIS itself uses for WMS, WFS, PostGIS, and other server-based data sources. Three drop-downs narrow the list from left to right: **country**, then **organisation** (the body that publishes the data, such as IGN or Regione Piemonte), then the **connection** itself. Each of the first two offers "All ..." or one value, and only what has something for the current tab. Once an organisation is picked, its connections are listed without the organisation's name in front. Your choices are remembered between tabs and sessions. When you add a connection with **New...**, the optional *Organisation* field says where it belongs; left empty, it is taken from the start of the display name (up to the first " - ").

- The dropdown switches between whichever connections are configured for that tab's kind of source.
- **New…** adds your own connection (a base URL, optional authentication, and any other details a specific source needs).
- **Edit…** modifies a connection you've added yourself. Built-in connections (like IGN's) can't be edited this way — Edit is disabled for those; add your own with **New…** instead if you need something different.
- **Delete** removes a connection you've added — again, only available for your own, not built-in ones.
- **Load…** / **Save…** import or export a single connection as a `.json` file, for backing one up or sharing it with someone else.

Connections you add are stored in your QGIS profile and persist across sessions.

---

## SIGate WMS/WMTS

The simplest of the three tabs. It shows a short list of pre-configured, known-working WMS/WMTS layers. Select one, click **Add to map**, and QGIS's own native WMS/WMTS handling takes over from there — panning, zooming, and rendering all work exactly as they would for any layer you'd added through QGIS's built-in "Add WMS/WMTS Layer" dialog. SIGate's role here is purely to save you from having to already know the right layer name, tile matrix set, and style — details that are easy to get wrong by hand (an incorrect combination produces a "missing TileMatrixSetLink and/or Style" error from QGIS itself, not a helpful message).

There's no download step here — the imagery streams live from the server each time you view it, same as any WMS/WMTS layer. If you need an actual standalone file instead (e.g. to share, or use offline), **Export clipped area as GeoTIFF** materializes one via GDAL's own command-line tools — this needs GDAL 3.11 or newer on your system PATH specifically for this one feature (`gdal raster clip` is a fairly new subcommand); SIGate checks this up front and tells you clearly if your GDAL is too old, rather than failing partway through with a cryptic error.

**Currently available**: IGN's high-resolution (10cm) Lambert-93 orthophoto layer. IGN's other WMTS endpoint (SCAN25 topographic maps) requires an API key and isn't yet offered through this tab — see "Current limitations" below. Also available as a connection here: **Gefahrenkarten (geodienste.ch, Switzerland)** — Swiss avalanche/flood/landslide/rockfall hazard-zone layers, added directly in response to a question about avalanche zone data specifically (see the Bulk Download section below for the fuller picture on what is and isn't available for avalanches).

---

## SIGate Bulk Download

The most involved of the three tabs — for downloading actual files (elevation models, department-level imagery archives, individual DEM/orthophoto tiles, and similar), not streaming them. The **Connection** dropdown lists both kinds of source this tab understands side by side — an Atom-style listing (IGN) and STAC catalog collections (swisstopo — topo/DEM basics like swissALTI3D and SWISSIMAGE, plus outdoor-activity route networks like ski touring, hiking, and cycling — see "Current coverage" below for the full list) — browsing, filtering, and downloading all work identically regardless of which one you pick; the tab doesn't ask you to know or care which kind of source you're looking at.

### Browsing

The tab loads a hierarchical listing — folders you can double-click into, and files you can select. Use **< Back** to go up, or type a URL directly into the address bar if you know exactly where you want to go. **Next page >** / **< Prev page** move through a folder's contents if it has more entries than fit on one page. (For a STAC connection, each "folder" is one tile/scene and its contents are the actual downloadable file(s) for that tile — the underlying pagination mechanism differs from Atom's, but you won't notice a difference in how the tab behaves, apart from the total entry count not always being shown up front the way it is for Atom.)

### Finding a specific entry

Type into the **Filter** field to instantly narrow down what's shown on the current page — no extra download, just filtering what's already loaded. If what you're looking for might be on a different page, click **Filter (all pages)** instead: this fetches every page of the current listing first (you'll be asked to confirm if it's a large listing), then shows only the matches — useful for finding one specific item in a folder with hundreds or thousands of entries. Click **Clear** to go back to normal page-by-page browsing.

### Downloading

Select one or more entries (Ctrl/Shift-click for multiple), then set:

- **Central repository**: where the raw downloaded file(s) are kept. Think of this as your permanent local archive — a good practice is to point it at one folder you reuse across projects, rather than a fresh folder each time, so SIGate can recognize files you've already downloaded and skip re-fetching them. Whatever you set here (and for Destination, below) is remembered automatically the next time you open this tab, even in a new QGIS session — you shouldn't need to re-enter it every time.
- **Destination**: where the *usable* result ends up. This is **optional for simple files** (a single GeoTIFF, for instance — it's already usable exactly as downloaded, so it just stays in the central repository unless you specify otherwise) but **required for archives** (a `.zip`/`.tar`/`.7z` file needs somewhere to be extracted to).

Click **Download selected**. What happens next depends on what you selected:

- **A plain file** downloads, gets checked for size correctness, and is added to your map directly.
- **An archive** downloads, gets checked (size, and a checksum if the source provides one), and you're asked which of its contents to extract — SIGate makes a good first guess at what's genuinely data versus documentation/metadata (using each source's own packaging convention where it's known), but always shows you the actual choice rather than deciding silently. If the extracted result looks like a set of individual map tiles rather than one file, you'll also be offered the choice to combine them into a single mosaic instead of adding dozens of small layers separately.

### A few things worth knowing

- **Already downloaded?** If a file's already sitting in your central repository, SIGate will ask whether to reuse it instead of downloading again.
- **Large downloads** trigger a warning (configurable in Settings) showing you the approximate total size before anything starts — this is about giving you a heads-up on time/bandwidth, not a hard limit.
- **Not enough disk space** is a hard stop, not just a warning — SIGate won't let a download or extraction proceed if it would fill your disk.
- **Multi-part archives** (some large deliveries are split into `.7z.001`, `.7z.002`, etc.) are handled transparently — you don't need to do anything differently for these.

---

## SIGate WFS

For adding vector data (feature layers — boundaries, points, polygons) rather than downloading files — usually. A small number of WFS layers work differently: their features are really an index of downloadable files rather than data meant to be displayed directly. SIGate handles both cases.

### The common case: adding a vector layer

1. The tab loads the list of available feature types from the configured WFS source. Use the filter box to narrow a long list down by name.
2. Optionally, add a simple attribute filter (`field = value`) to load only matching features rather than the whole layer — useful for very large layers where you only want a specific subset.
3. Click **Add to map**. This hands off directly to QGIS's own native WFS support — SIGate doesn't fetch or render the features itself.

### The other case: downloading linked files

Some layers (IGN's LiDAR HD tile index, `IGNF_LIDAR-HD_METADONNEE:metadata`, is a real example) don't represent map features at all — each "feature" is really a record pointing at downloadable files, with its geometry just showing the tile it covers. For these:

1. Click **Query features** instead of Add to map. This actually fetches the feature data and shows it in a table.
2. If any of the returned rows have a download link, the **Download selected** button becomes available. A column counts as a download link when its *values* are web addresses (`http://` or `https://`), whatever the column is called. Select one or more rows and click it.
3. If the layer has **several** link columns (the LiDAR HD layer has four: `url_mnt` terrain model, `url_mns` surface model, `url_mnh` canopy height model, `url_npl` point cloud), a dialog asks which products to fetch for all the selected rows. The three rasters are pre-ticked; the point cloud (hundreds of MB per tile) is not.
4. From here, the flow is identical to the Bulk Download tab: set a central repository (and destination, if a linked file turns out to be an archive), and the files download, get checked, and are added to your map — rasters as raster layers, a `.copc.laz` point cloud as a point-cloud layer. Rasters and point clouds are saved in separate `raster/` and `point_cloud/` subfolders. When you download several tiles of one product you are offered a mosaic per product; the products are never mixed into one mosaic.

**Add to map** on such a layer only adds the vector footprints; the data files come through the download buttons.

**Where layers appear in the project**: SIGate never leaves layers loose at the top level. Downloaded files go into groups that mirror the folders on disk — *source › layer › product* (for example `IGN (France) › IGNF_LIDAR-HD_METADONNEE_metadata › MNT`); files extracted from an archive go under *source › archive name*; WM(T)S layers, GeoTIFF exports and WFS "Add to map" go into a group named after the source. Downloading again reuses the same groups. (A way to customise this organisation is planned.)

If **Download selected** stays disabled after querying, the layer you picked is a normal vector layer without downloadable files attached — use **Add to map** instead.

### A note on the attribute filter: it's built to work the same way for simple `field = value` searches whether the underlying source speaks a stricter query dialect or a more permissive one — this covers the common case well, but hasn't been confirmed to behave identically for every possible kind of filter expression (complex boolean logic, wildcards, spatial queries). If a filter doesn't behave as expected, try simplifying it to a single plain equality check first.


**Plain WMS** layers (as opposed to tiled WMTS) are listed in the WM(T)S tab the same way - for example the Valle d'Aosta avalanche cadastre or the Swiss hazard maps. A WMS layer is requested in web-mercator when the server offers it, otherwise lon/lat or the first coordinate system QGIS recognises. *Export as GeoTIFF* only works on tiled (WMTS) layers.

**On some servers the WFS Filter panel does not narrow the query results.** The Swiss geodienste.ch services ignore the filter text sent with the query, so the table shows unfiltered rows; "Add to map" still applies your filter (QGIS handles it itself).

---

## SIGate ArcGIS REST

For sources that publish vector data through Esri's ArcGIS REST API instead of WFS (for example Arpa Piemonte's avalanche service, which has a WMS but no WFS). It works like the WFS tab.

1. Pick a connection. The list shows the service's queryable layers; group layers appear as a path (`GROUP › Layer`). Use the filter box to narrow it.
2. Select a layer: its geometry type, CRS and field count appear under the list.
3. Optionally narrow the result: **Filter...** takes a SQL where clause (for example `comune = 'Alagna'`), and **Limit to current map extent** restricts to what the map canvas shows. **Query features** shows the matching rows (200 per page by default; the server may cap it lower), with the total count.
4. **Add to map** adds the layer through QGIS's own ArcGIS support. If you selected rows, only those are added; otherwise everything matching the filter is. The layer lands in a group named after the source.

Dates are shown as ISO dates. The tab does not download files: it adds live layers, like the WFS tab's common case.

---

## Settings

Open SIGate's settings dialog to configure:

- **Size warning threshold** — the download size (in GB) that triggers the "this is a big download" confirmation prompt. Default: 1 GB.
- **7-Zip executable** — normally auto-detected; only needed if you want to download `.7z`-format archives and SIGate can't find an installed 7-Zip on its own. Use **Browse** to point at one manually, or **Auto-detect** to re-run the automatic search.
- **Preferred language** — English, Deutsch, Français, or Italiano. Two things use this, differently: for sources that support asking the server directly for a language (swisstopo's WMTS/WMS genuinely honour this — real, human-translated layer names, not a guess), SIGate now asks for it directly. For sources that have no such option at all (Atom feed or catalog entry titles, for example), this is also the language the data-translation feature below targets.
- **Data translation** — for the sources that have no native language option of their own. As you browse normally, SIGate quietly remembers every title it displays that it doesn't already have a translation for. **Discover & Save untranslated strings...** exports that list as a plain JSON file (`{"original text": ""}` for each one) — hand that file to an LLM of your choice (outside SIGate; no API key or network call happens here) and ask it to fill in the translations for your preferred language. **Load translations...** reads the completed file back in and remembers it from then on — matching titles are shown translated everywhere in SIGate, while the original text is still what's actually used for filenames and folder structure, so downloads stay consistent regardless of display language. This is separate from SIGate's own interface text (buttons, labels), which follows QGIS's own language setting instead.

Changes here persist across QGIS restarts.

---

## Current coverage and limitations

Worth knowing plainly, rather than discovering by trial and error:

- **France (IGN), Switzerland (swisstopo), the intercantonal Swiss platform geodienste.ch, Norway (Geonorge), Germany (BKG TopPlusOpen), the Netherlands (PDOK), and Austria (basemap.at) are configured so far.** The underlying design supports adding others; see `docs/candidate_sources.md` for what's been researched but not yet added, and why.
- **The Bulk Download tab works with Atom-style sources (IGN), STAC catalog collections (swisstopo, geodienste.ch), and a two-level Theme/Dataset view built on top of Norway's otherwise-flat Geonorge catalog** — Germany, the Netherlands, and Austria don't currently expose anything this tab can use. Norway is worth explaining specifically: `kartkatalog.geonorge.no/api/search` is a flat search-and-facets API with no tree of its own, but every dataset that carries a real Atom feed (398 of the catalog's ~8,637 records, as of this session) can be grouped by its Topic/Theme into a real, browsable two-level hierarchy — Theme, then Dataset — and once you descend into a dataset, you're looking at its own genuine INSPIRE Atom feed, browsed exactly the same way IGN's feeds already are. This only surfaces the ~5% of the catalog that has an Atom feed at all — the rest (WMS/WFS-only services, REST APIs) simply doesn't appear in Bulk Download, which is a real, stated limitation, not a bug. For Switzerland specifically, thirteen datasets are wired in across two distinct platforms: swisstopo's federal `data.geo.admin.ch` (the topo/DEM basics — swissALTI3D, swissTLM3D, swissSURFACE3D, SWISSIMAGE — plus eight outdoor-activity route networks: ski touring, two separate snowshoe-route datasets, hiking/Wanderland, winter hiking, cycling/Veloland, mountain biking, skating), and `geodienste.ch` (Gefahrenkarten — avalanche, flood, landslide, and rockfall hazard zones, also available as a WM(T)S layer and a WFS connection, not just Bulk Download).
- **On avalanche hazard specifically, since this came up directly**: hazard *zones* (where avalanches can occur, at what intensity, for what return period — including the legally-binding "Gefahrengebiet" zone, not just an indicative hint) are real and wired in via `geodienste.ch` above, with near-national coverage (98% of the area needing mapping). What's still **not** available: SLF's live daily avalanche danger bulletin (a separate API, different protocol entirely) and a confirmed historical-events/past-avalanches dataset (SLF maintains avalanche accident records, but a live queryable geoservice for them wasn't found). SAC mountain hut data also isn't available — swisstopo's own documentation states this partner-organisation data is "neither entered nor managed by swisstopo," with no independent downloadable dataset found for it at all.
- **IGN's private, apikey-gated WMTS layer (SCAN25 topographic)** is now available through the WM(T)S tab alongside the public orthophoto layer — shown as a separate connection entry ("IGN (France) — private").
- **The interface language is English only** at the moment — the groundwork for other languages exists internally, but no translations have been written yet.
- **Downloads don't yet route through QGIS's own network/proxy settings** — if you're on a restrictive network that requires QGIS's proxy configuration to reach the internet, SIGate's downloads may fail even though QGIS itself can browse fine.

None of these are permanent design limits — they reflect what's been built so far, not what the plugin is designed to be limited to.
