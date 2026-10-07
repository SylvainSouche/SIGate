# SIGate — Installation Guide

SIGate is not yet published on the official QGIS Plugin Repository. Until it is, install it manually using one of the two methods below.

## Requirements

- **QGIS 3.40 or later**, including QGIS 4 (the plugin declares Qt6 support). Developed and tested on QGIS 3.44 and 4.0; 3.40 is the declared minimum (the long-term-release line that has the scoped enums, point-cloud layers and COPC support SIGate uses) and has not been run directly.
- **Python 3.9 or later** — bundled with QGIS; no separate install needed.
- **7-Zip** (optional) — only required if you want to download sources that distribute data as `.7z` archives (confirmed real for at least one configured source). Not required for `.zip` or `.tar`-family archives, which SIGate handles without any external tool.
  - **Windows**: download the installer from [7-zip.org](https://www.7-zip.org/). SIGate looks for it at the standard install locations (`C:\Program Files\7-Zip\7z.exe` or the `(x86)` equivalent) even if the installer didn't add it to your system PATH.
  - **macOS**: `brew install p7zip`
  - **Linux**: `sudo apt install p7zip-full` (Debian/Ubuntu) or your distribution's equivalent package.
  - If SIGate can't find a 7-Zip executable automatically, you can point it to one manually in **Settings** (see the User Guide) — either browse to its location or let SIGate re-run auto-detection.

## Method A — Install from ZIP (recommended)

1. Download or build the SIGate plugin as a `.zip` file. The zip's top-level folder must be named `sigate` (matching the plugin's package name) and contain `metadata.txt`, `__init__.py`, and the rest of the plugin's files directly inside it — not nested in an extra subfolder.
2. In QGIS, go to **Plugins → Manage and Install Plugins…**
3. Click **Install from ZIP** in the left-hand list.
4. Browse to the `.zip` file and click **Install Plugin**.
5. Once installed, go to the **Installed** tab in the same dialog and make sure the checkbox next to **SIGate** is ticked (enabled).

## Method B — Manual folder copy

1. Locate your QGIS profile's plugins folder:
   - **Windows**: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
   - **macOS**: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
   - **Linux**: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`

   (Replace `default` with your actual profile name if you use a different QGIS profile — check via **Settings → User Profiles** in QGIS if unsure.)
2. Copy the entire `sigate` folder into that plugins directory, so the final path looks like `.../python/plugins/sigate/metadata.txt`, `.../python/plugins/sigate/__init__.py`, etc.
3. Restart QGIS, or use **Plugins → Manage and Install Plugins… → Installed** and tick the checkbox next to **SIGate** if it isn't already enabled.

## Verifying the install

1. Open **Layer → Data Source Manager…** (or the equivalent toolbar button).
2. You should see three new categories in the left-hand list, alongside QGIS's built-in ones (Vector, Raster, WMS/WMTS, etc.):
   - **SIGate WMS/WMTS**
   - **SIGate Bulk Download**
   - **SIGate WFS**

If none of these appear, check the **Python console** (**Plugins → Python Console**) for an error message from SIGate's `initGui()`, and confirm the plugin is actually enabled in the Plugin Manager's Installed tab.

## Uninstalling

**Plugins → Manage and Install Plugins… → Installed**, select **SIGate**, click **Uninstall Plugin**. The three Data Source Manager categories disappear immediately without needing to restart QGIS.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Categories don't appear in Data Source Manager | Plugin not enabled, or an error during `initGui()` — check the Python Console for a traceback. |
| ".7z archives can't be extracted" | No 7-Zip executable found. Install one per the Requirements section above, or set a manual path in Settings. |
| A download fails immediately with a network error | Check your internet connection and any proxy configuration; SIGate's download mechanism does not yet route through QGIS's own proxy-aware network manager (see the developer documentation's open items) — a restrictive proxy may block it even if QGIS's own network settings would normally allow it. |
| A behavior you'd expect to be fixed still seems to be happening after updating the plugin | **Restart QGIS completely**, not just reinstall/toggle the plugin. Python caches imported modules in memory — replacing the plugin's files on disk, or even disabling and re-enabling it in the Plugin Manager, does not reliably force QGIS to re-import every one of the plugin's already-loaded submodules. A full restart guarantees you're actually running the new code, not a stale cached copy of the old code. |
