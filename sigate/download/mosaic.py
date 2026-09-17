"""
download.mosaic - detects when a set of extracted files looks like
individual tiles of one larger raster, builds a single VRT mosaic from
them via gdalbuildvrt rather than requiring every tile to be added as
its own layer, and builds external overviews (pyramids) for that VRT via
gdaladdo, so opening it renders efficiently at every zoom level.

A predictable, plugin-generated naming convention is used for mosaic
output files, so a later visit to the same destination can recognize a
previously-built mosaic (see find_existing_mosaic) instead of either
proposing to rebuild it or presenting it as unclassified raw data. That
existing mosaic's mere presence is not, on its own, trusted as still
correct, though - mosaic_covers_tiles compares its own referenced source
files against what's actually on disk right now and triggers a rebuild
on any mismatch (fixed after a real reported failure: a stale VRT from
an earlier, incomplete state was being silently reused rather than
rebuilt to reflect the currently downloaded tiles). The overview step
follows the exact real, confirmed artifact shape observed in an actual
user's own pre-plugin workflow (docs/spec.md: a "dem73.vrt" +
"dem73.vrt.ovr" pair) - build_vrt_mosaic alone only ever produced the
VRT half of that; build_overviews closes the other half.
"""

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, List, Optional

_RASTER_TILE_EXTENSIONS = {".tif", ".tiff", ".jp2", ".asc", ".img"}

_MOSAIC_FILENAME_PREFIX = "sigate_mosaic_"


def _log_command(log: Optional[Callable[[str], None]], cmd: List[str]) -> None:
    if log is not None:
        log("+ " + " ".join(cmd))


def raster_tile_paths(paths: List[Path]) -> List[Path]:
    """Filters paths down to just the ones with a known raster-tile
    extension - the same set looks_like_tileable_raster_set checks
    against. Needed by callers that extract a mixed batch of files (data
    tiles alongside metadata/supplement files) and must build a mosaic
    from only the actual raster tiles, not the whole batch - passing a
    non-raster file straight to gdalbuildvrt would either fail outright
    or silently produce a broken mosaic."""
    return [p for p in paths if p.suffix.lower() in _RASTER_TILE_EXTENSIONS]


def looks_like_tileable_raster_set(paths: List[Path]) -> bool:
    """A simple heuristic: more than one file sharing a common
    known-raster extension suggests individual tiles of a larger area
    rather than a set of unrelated files. Not a guarantee - the actual
    decision to build a mosaic is always offered to the user, never
    applied silently."""
    matching = [p for p in paths if p.suffix.lower() in _RASTER_TILE_EXTENSIONS]
    if len(matching) < 2:
        return False
    extensions = {p.suffix.lower() for p in matching}
    return len(extensions) == 1


def mosaic_filename(product_key: str) -> str:
    return f"{_MOSAIC_FILENAME_PREFIX}{product_key.lower()}.vrt"


def find_existing_mosaic(dest_dir: Path, product_key: str) -> Optional[Path]:
    """Looks for a previously-built mosaic for the given product in
    dest_dir, using this module's own naming convention. Returns its path
    if found, or None. Its mere presence is not, on its own, a reason to
    trust it as up to date - see mosaic_covers_tiles."""
    candidate = Path(dest_dir) / mosaic_filename(product_key)
    return candidate if candidate.is_file() else None


def _referenced_source_filenames(vrt_path: Path) -> set:
    """The set of source filenames (basenames only - gdalbuildvrt
    commonly stores these relative to the VRT's own directory when the
    sources live alongside it, which this module's own callers always
    do, but comparing by basename is robust regardless of whether a
    given VRT happens to store relative or absolute paths) a VRT mosaic
    actually references, parsed directly from its own XML. Returns an
    empty set for anything unparseable rather than raising - a
    corrupted or unexpected file is more safely treated as "doesn't
    cover the current tiles" (triggering a rebuild) than as a hard
    failure blocking the whole download."""
    try:
        tree = ET.parse(vrt_path)
    except ET.ParseError:
        return set()
    return {
        Path(el.text.strip()).name
        for el in tree.getroot().iter("SourceFilename")
        if el.text and el.text.strip()
    }


def mosaic_covers_tiles(vrt_path: Path, tile_paths: List[Path]) -> bool:
    """True only if vrt_path's own referenced source files exactly
    match tile_paths, by filename - not just "at least covers them",
    since a VRT referencing tiles that no longer exist (e.g. left over
    from an interrupted earlier build, or a different delivery that
    happened to resolve to the same product_key) is just as stale as
    one missing newly-added tiles. Either kind of mismatch means the
    existing mosaic doesn't reflect the currently downloaded tile set
    and must be rebuilt, not silently reused - the actual reported
    failure this function exists to fix ("the vrt however is not built
    properly and a preexisting one was included") was exactly this: an
    existing mosaic's mere presence, previously the only thing checked,
    is not the same as it being correct for what's on disk right now."""
    return _referenced_source_filenames(vrt_path) == {p.name for p in tile_paths}


def build_vrt_mosaic(
    tile_paths: List[Path],
    dest_dir: Path,
    product_key: str,
    gdalbuildvrt_exe: str = "gdalbuildvrt",
    log: Optional[Callable[[str], None]] = None,
) -> Path:
    """Builds a VRT mosaic from the given tile files, writing it to
    dest_dir under this module's predictable naming convention. Returns
    the path to the created VRT.

    -overwrite is passed explicitly rather than relying on
    gdalbuildvrt's own default behavior when the destination already
    exists - this function is now also the rebuild path for a stale
    existing mosaic (see mosaic_covers_tiles), not only the fresh-build
    path, so it must succeed unconditionally regardless of whether
    dest_path is already present."""
    dest_path = Path(dest_dir) / mosaic_filename(product_key)
    cmd = [
        gdalbuildvrt_exe,
        "-overwrite",
        str(dest_path),
        *[str(p) for p in tile_paths],
    ]
    _log_command(log, cmd)
    subprocess.run(
        cmd,
        check=True,
        capture_output=True,
    )
    return dest_path


def overview_path(vrt_path: Path) -> Path:
    """The external-overview sidecar path GDAL itself uses for a given
    raster - matches the real, confirmed shape seen in an actual user's
    own workflow before this plugin existed (docs/spec.md's documented
    "dem73.vrt" + "dem73.vrt.ovr" pair), which this module's own naming
    convention (mosaic_filename) was designed to be recognizable
    alongside."""
    return Path(str(vrt_path) + ".ovr")


def has_overviews(vrt_path: Path) -> bool:
    return overview_path(vrt_path).is_file()


def build_overviews(
    vrt_path: Path,
    levels: str = "2 4 8 16",
    resampling: str = "average",
    gdaladdo_exe: str = "gdaladdo",
    log: Optional[Callable[[str], None]] = None,
) -> Path:
    """Builds external overviews (a .ovr sidecar) for a VRT mosaic via
    gdaladdo, so opening it in QGIS renders efficiently at every zoom
    level instead of QGIS computing pyramids on the fly (or not at all)
    each time.

    Deliberately does not force a lossy compression choice (e.g. JPEG,
    appropriate for photographic imagery) by default - a mosaic built
    through this module could just as easily be elevation/DEM data, and
    JPEG's lossiness has no business being introduced into elevation
    values. "average" resampling with GDAL's own default overview
    encoding is a safe, format-agnostic choice here; a caller that
    specifically knows its input is photographic imagery and wants
    JPEG-compressed overviews can call gdaladdo directly instead of
    going through this function."""
    cmd = [gdaladdo_exe, "-ro", "-r", resampling, str(vrt_path), *levels.split()]
    _log_command(log, cmd)
    subprocess.run(
        cmd,
        check=True,
        capture_output=True,
    )
    return overview_path(vrt_path)
