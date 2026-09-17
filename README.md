# SIGate — Spatialized Infrastructure Gateway

A QGIS plugin that adds three new categories to QGIS's native **Data Source Manager** for browsing, searching, and downloading geospatial data directly from national mapping agencies — no separate browser tab, no manual download-then-import step.

- **WFS** — browse feature types, filter with a real query builder, add to the map or download linked files.
- **WM(T)S** — browse and add raster layers via QGIS's own native WMS/WMTS provider, or export a clipped GeoTIFF of the current map view directly from the source.
- **Bulk Download** — browse Atom-style bulk-download catalogs, with automatic archive extraction (zip/tar/7z, including split multi-part 7z archives), mosaic building, and MD5 verification where a source provides a checksum.

Currently configured: **France (IGN)**, **Switzerland (swisstopo)**, **Germany (BKG TopPlusOpen)**, the **Netherlands (PDOK)**, and **Austria (basemap.at)**. See [`docs/candidate_sources.md`](sigate/docs/candidate_sources.md) for further sources already researched but not yet added, and why.

## Status

Implemented and in active use — not a released, publicly-submitted QGIS plugin yet (`experimental=True` in `metadata.txt`). See [`docs/version.md`](sigate/docs/version.md) for what's shipped in each version, and [`docs/spec.md`](sigate/docs/spec.md) for the current, real architecture.

## Installation

See [`docs/install_guide.md`](sigate/docs/install_guide.md).

## Documentation

Everything lives under [`sigate/docs/`](sigate/docs/):

| File | What it's for |
|---|---|
| `spec.md` | The current, real architecture — what's implemented and how, checked against the actual code. |
| `dev_workflow.md` | Coding rules, plus a detailed, append-only history of every fix and feature, in the order they actually happened. |
| `version.md` | One entry per delivered version — "what shipped in version X." |
| `candidate_sources.md` | Research on further national data sources: what's integrated, what's deferred, and why. |
| `install_guide.md` | End-user install instructions and troubleshooting. |
| `user_guide.md` | End-user feature walkthrough. |
| `test_scenarios.md` | The manual test plan for anything needing a real QGIS install, real network access, or real GDAL — a condition matrix covering error cases and interactive-state combinations the automated suite can't reach. |

## Development

```sh
make test      # full automated test suite (pure-Python + QGIS-dependent, if a QGIS install is available)
make lint       # ruff check
make format     # ruff format
make distrib    # build sigate-<version>.zip
make increment  # bump metadata.txt's version (X.Y.Z -> X.Y.Z+1)
make verify     # full sweep: lint, format check, tests, then distrib and re-extract to confirm the zip itself is correct
make clean      # remove __pycache__/.ruff_cache/.pytest_cache and any built zip
```

See `make help` (or just `make` with no target) for the full list, and `docs/dev_workflow.md`'s "Step 0 — Coding rules" for the standards new code is expected to meet, including the versioning convention (`metadata.txt`'s `version` bumps once per packaged delivery, tracked in `version.md`).

## Project layout

```
.
├── LICENSE
├── README.md              # this file
├── Makefile
├── pyproject.toml         # ruff configuration
├── .gitignore
└── sigate/                # the actual plugin - this directory is what gets zipped and installed into QGIS
    ├── metadata.txt
    ├── gateways/          # pure-Python fetch/parse logic, no Qt dependency
    ├── download/          # the download/extraction/integrity pipeline, gateway-agnostic
    ├── sources/           # bundled + user-configured source definitions
    ├── ui/                # Qt widgets - the only QGIS-dependent layer
    ├── docs/               # everything listed in the table above
    ├── i18n/
    └── tests/
```

See `sigate/docs/spec.md` §8 for the full, current module-by-module breakdown.

## License

BSD-3-Clause — see [`LICENSE`](LICENSE).

QGIS's official plugin repository requires a submitted plugin's license
to be ["compatible with the GPLv2 or later"](https://plugins.qgis.org/docs/publish).
QGIS's own blog post stating that requirement frames the actual
underlying concern directly: *"all code included in any plugin should
be made clearly and easily available in source form"* — the policy
exists to stop closed-source or obfuscated plugins, not to require
every plugin's license text to literally say "GPL." BSD-3-Clause is a
fully open, source-available license that satisfies that concern
already.

On the license-compatibility question itself: BSD-3-Clause is on the
FSF's own list of licenses classified as GPL-compatible (code under it
can be incorporated into a GPL-licensed work), and GPL-licensed code is
routinely distributed as part of QGIS itself — the direction this
project's own code would need to combine with QGIS in is exactly the
direction BSD-3-Clause is compatible in. Real precedent for permissive
licenses specifically at the plugin level, not just the general
compatibility rule: MIT- and BSD-licensed plugins are already hosted in
the official repository today.

On that basis, BSD-3-Clause should already satisfy the repository's
stated requirement. If a real submission were ever rejected
specifically on this ground despite that, dual-licensing (adding a
GPLv2-or-later option alongside BSD-3-Clause) is the fallback.

## A note on `metadata.txt`

`author`/`email`/`homepage`/`tracker`/`repository` are filled in only as far as this project currently has real values for them — `homepage`/`tracker`/`repository` are still empty pending an actual hosted repository to point at. Fill these in before submitting to the official QGIS plugin repository; until then, the plugin works fine installed locally without them.
