"""
download.pipeline - orchestrates the download-to-layer sequence described
in the SIGate design: a target check, a size/free-space check, the
download itself, an integrity check, and then a format-dependent branch
(archive extraction with role classification and an optional mosaic
build, or direct placement for a plain file).

Exposed as a set of individually-callable stage functions rather than one
combined function, since several stages need a decision from the calling
application in between (which target to use, whether to proceed past a
size warning, which archive members to extract) - this module reports
what it finds at each stage; it does not make those decisions itself.

Pure Python, no Qt dependency. The `download_fn` parameter accepted by
download_items lets a caller inject its own transport, matching the same
pattern used throughout sigate.gateways.
"""

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .archive import (
    detect_archive_kind,
    extract_archive,
    is_non_data_artifact,
    list_archive,
)
from .extraction_structure import classify_archive_contents, extract_product_key
from .integrity import verify_byte_count, verify_hash, verify_md5
from .targets import DownloadTargets, has_enough_free_space

DEFAULT_SIZE_WARNING_THRESHOLD_BYTES = 1024**3  # 1 GB


@dataclass(frozen=True)
class DownloadItem:
    """One file to download.

    subdirectory, when given, is a "/"-joined relative path (works
    correctly as a multi-component path on both POSIX and Windows via
    plain Path joining) placed between central_repo and the file itself
    - the source/category/layer structure built by
    build_download_subdirectory, so a batch of downloads doesn't all
    land flatly in one directory regardless of where they came from or
    what they are (a real reported problem: "now we d/l everything in
    the same directory"). None means no subdirectory - the file lands
    directly in central_repo, unchanged from this project's original
    behavior."""

    url: str
    filename: str
    declared_size: Optional[int] = None
    expected_md5: Optional[str] = None
    # (algorithm, hex_digest) - the generic counterpart to expected_md5
    # above, added for gateways.stac's inline sha256 multihash checksum
    # rather than repurposing expected_md5 for a non-MD5 algorithm.
    # None means "no generic hash to check"; expected_md5 keeps working
    # exactly as before for every existing atom/WFS call site - the two
    # aren't mutually exclusive, but no current caller sets both.
    expected_hash: Optional[Tuple[str, str]] = None
    subdirectory: Optional[str] = None
    # Which product of a multi-product feature this file is (e.g. "MNT",
    # "NPL" for IGN's LiDAR HD metadata layer, one feature = four links).
    # Downloads of different products must never be mosaicked together
    # even when they share a format and resolution; None = no such split.
    product: Optional[str] = None


@dataclass
class DownloadOutcome:
    item: DownloadItem
    path: Optional[Path]
    already_existed: bool
    byte_count_ok: Optional[bool] = None
    md5_ok: Optional[bool] = None
    # Result of verifying item.expected_hash (generic algorithm), kept
    # distinct from md5_ok above rather than overloading it - a caller
    # checking outcome.md5_ok for an atom/WFS item is unaffected; a
    # caller checking outcome.hash_ok is what a STAC item's result
    # lands in instead.
    hash_ok: Optional[bool] = None
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        if self.error is not None:
            return False
        if self.byte_count_ok is False or self.md5_ok is False or self.hash_ok is False:
            return False
        return self.path is not None


# --------------------------------------------------------------------------- download directory structure

_RASTER_EXTENSIONS = {".tif", ".tiff", ".asc", ".jp2", ".img", ".vrt"}
_VECTOR_EXTENSIONS = {".shp", ".gpkg", ".geojson", ".json"}
_POINT_CLOUD_EXTENSIONS = {".laz", ".las", ".copc"}

_UNSAFE_PATH_CHARS = re.compile(r'[\\/:*?"<>|]')


def guess_data_category(filename: str) -> str:
    """A broad data-type category for a filename - raster / vector /
    point_cloud / other - used to build the source/category/layer
    download directory structure (build_download_subdirectory). A
    distinct concept from ui.download_flow's own guess_layer_kind, which
    only ever distinguishes raster/vector (all that QGIS's own
    addRasterLayer/addVectorLayer signals need) and returns None rather
    than a fallback category - point_cloud is a real, meaningful
    category for directory placement even though this project has no
    add-point-cloud-layer signal yet. Falls back to "other" rather than
    None, since every file needs *some* folder to land in."""
    suffix = Path(filename.lower()).suffix
    if suffix in _POINT_CLOUD_EXTENSIONS:
        return "point_cloud"
    if suffix in _RASTER_EXTENSIONS:
        return "raster"
    if suffix in _VECTOR_EXTENSIONS:
        return "vector"
    return "other"


def sanitize_path_component(name: str) -> str:
    """Makes a string safe to use as a single directory/file name
    component across Windows/macOS/Linux - replaces characters illegal
    on at least one of them with an underscore. Deliberately leaves
    already-safe characters alone (spaces, parentheses, accented
    letters) - a source display name like "IGN (France)" is meant to
    stay readable as a folder name, not get mangled into something only
    a machine would recognize."""
    cleaned = _UNSAFE_PATH_CHARS.sub("_", name).strip()
    return cleaned or "unnamed"


def build_download_subdirectory(
    source_display_name: str, filename: str, layer_name: Optional[str] = None
) -> str:
    """Builds the source/category/layer subdirectory a download should
    land in beneath central_repo ("gateway" here means the
    source/institution, e.g. "IGN (France)"; "data type" means a broad
    raster/vector/point-cloud category, not a per-product key). category
    is derived from the file's own extension (guess_data_category);
    layer_name is supplied by the calling tab (a WFS typename, a WMTS
    layer identifier, or the closest analogous "which specific resource
    this came from" concept for a source without a literal "layer") -
    omitted from the path entirely when not given, rather than inserting
    an empty or placeholder segment.

    Returned as a "/"-joined string rather than a Path, since
    DownloadItem.subdirectory is stored this way and joins correctly via
    plain Path "/" operator on both POSIX and Windows regardless."""
    parts = [
        sanitize_path_component(source_display_name),
        guess_data_category(filename),
    ]
    if layer_name:
        parts.append(sanitize_path_component(layer_name))
    return "/".join(parts)


def _item_dest_path(central_repo: Path, item: DownloadItem) -> Path:
    """The single source of truth for where one item's file actually
    lands beneath central_repo - shared by check_already_downloaded and
    download_items so the "already downloaded" check can never disagree
    with where a download actually gets placed."""
    if item.subdirectory:
        return central_repo / item.subdirectory / item.filename
    return central_repo / item.filename


# --------------------------------------------------------------------------- already-downloaded check


def check_already_downloaded(
    items: List[DownloadItem], central_repo: Path
) -> Dict[str, bool]:
    """Returns {filename: already_present} for each item, checked against
    the central-repo target. A partial set for a multi-file item (e.g. one
    part of a split archive present, another missing) is the caller's
    responsibility to detect by checking every filename belonging to that
    item - this function reports presence per file, not per logical item."""
    central_repo = Path(central_repo)
    return {
        item.filename: _item_dest_path(central_repo, item).exists() for item in items
    }


# --------------------------------------------------------------------------- size / free space


def combined_declared_size(items: List[DownloadItem]) -> Optional[int]:
    """Sums declared sizes across items. Returns None if any item's size
    is unknown, since a partial total would understate the real
    requirement."""
    sizes = [item.declared_size for item in items]
    if any(size is None for size in sizes):
        return None
    return sum(sizes)


def exceeds_size_warning_threshold(
    total_bytes: Optional[int],
    threshold_bytes: int = DEFAULT_SIZE_WARNING_THRESHOLD_BYTES,
) -> bool:
    if total_bytes is None:
        return False
    return total_bytes > threshold_bytes


def check_free_space_for_download(
    central_repo: Path, total_bytes: Optional[int]
) -> bool:
    """Returns True if there is enough free space, or if the required
    size is unknown (nothing to check against - the download itself may
    still fail partway through if space genuinely runs out)."""
    if total_bytes is None:
        return True
    return has_enough_free_space(central_repo, total_bytes)


# --------------------------------------------------------------------------- download + integrity


def _default_download(
    url: str,
    dest_dir: Path,
    filename: str,
    progress_callback: Optional[Callable[[int, Optional[int]], None]] = None,
    should_continue: Optional[Callable[[], bool]] = None,
) -> Path:
    from ..gateways.atom import download as atom_download

    return Path(
        atom_download(
            url,
            str(dest_dir),
            filename,
            progress_callback=progress_callback,
            should_continue=should_continue,
        )
    )


def download_items(
    items: List[DownloadItem],
    central_repo: Path,
    download_fn: Optional[Callable[..., Path]] = None,
    on_file_start: Optional[Callable[[int, int, DownloadItem], None]] = None,
    on_file_progress: Optional[Callable[[int, Optional[int]], None]] = None,
    should_continue: Optional[Callable[[], bool]] = None,
) -> List[DownloadOutcome]:
    """Downloads every item to central_repo, skipping any already present,
    and checks byte count (and MD5, when an expected hash was supplied)
    for each one actually downloaded.

    on_file_start(index, total, item), when given, is called right before
    each file's own download begins (0-based index) - drives a "file N of
    M" progress display. on_file_progress(read, total) is forwarded
    straight through to download_fn's own progress_callback for the file
    currently in flight; total is None if unknown, same convention as
    gateways.atom.download.

    should_continue(), when given, is checked before starting each file
    (stopping early, without attempting any remaining ones, if it starts
    returning False) and forwarded to download_fn as its own
    should_continue - download_fn is expected to check it mid-transfer
    too (gateways.atom.download does), so cancellation can take effect
    within a single file, not just between files. A file interrupted
    mid-transfer gets its own outcome with error="Cancelled" rather than
    being silently omitted, so a caller can report how far it got.

    Every download_fn (the default, or one injected by a caller) must
    accept progress_callback and should_continue as keyword arguments,
    even if it ignores them - this contract supports a background,
    cancellable progress dialog.
    """
    download_fn = download_fn or _default_download
    from ..gateways.base import DownloadCancelled

    central_repo = Path(central_repo)
    central_repo.mkdir(parents=True, exist_ok=True)
    outcomes = []
    total_items = len(items)
    for index, item in enumerate(items):
        if should_continue is not None and not should_continue():
            break
        if on_file_start is not None:
            on_file_start(index, total_items, item)
        dest_path = _item_dest_path(central_repo, item)
        if dest_path.exists():
            outcomes.append(
                DownloadOutcome(item=item, path=dest_path, already_existed=True)
            )
            continue
        # central_repo.mkdir above only ever created the top-level repo
        # directory - once items can carry a nested item.subdirectory
        # (source/category/layer), dest_path.parent may be several levels
        # deeper than that and won't exist yet. Without this, download_fn
        # is handed a dest_dir that doesn't exist, and any real writer
        # (the default atom_download, or a test's fake_download) fails
        # trying to open a file inside a missing directory - silently
        # recorded as a per-item error below rather than raised, so this
        # was easy to miss until a downstream `.exists()` assertion failed
        # with no obvious cause.
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path = download_fn(
                item.url,
                dest_path.parent,
                item.filename,
                progress_callback=on_file_progress,
                should_continue=should_continue,
            )
        except DownloadCancelled:
            outcomes.append(
                DownloadOutcome(
                    item=item, path=None, already_existed=False, error="Cancelled"
                )
            )
            break
        except Exception as e:
            outcomes.append(
                DownloadOutcome(
                    item=item, path=None, already_existed=False, error=str(e)
                )
            )
            continue
        byte_count_ok = verify_byte_count(path, item.declared_size)
        md5_ok = verify_md5(path, item.expected_md5) if item.expected_md5 else None
        hash_ok = verify_hash(path, *item.expected_hash) if item.expected_hash else None
        outcomes.append(
            DownloadOutcome(
                item=item,
                path=path,
                already_existed=False,
                byte_count_ok=byte_count_ok,
                md5_ok=md5_ok,
                hash_ok=hash_ok,
            )
        )
    return outcomes


def delete_failed_outcome(outcome: DownloadOutcome) -> None:
    """Removes the downloaded file for an outcome that failed an
    integrity check, so a corrupt file is never left on disk looking
    valid."""
    if outcome.path is not None and outcome.path.exists():
        outcome.path.unlink()


# --------------------------------------------------------------------------- archive vs. simple file branch


def is_archive_file(filename: str) -> bool:
    return detect_archive_kind(filename) is not None


def list_and_classify_archive(
    path: Path,
    product_key: Optional[str] = None,
    data_dir: Optional[Path] = None,
    sevenzip_exe: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
) -> Dict[str, List[str]]:
    """Lists an archive's contents and classifies each member by role
    (data / metadata / supplements / unclassified)."""
    members = list_archive(path, sevenzip_exe=sevenzip_exe, log=log)
    paths = [m.name for m in members if not m.is_dir]
    key = product_key or extract_product_key(path.stem)
    return classify_archive_contents(paths, product_key=key, data_dir=data_dir)


def extract_selected_members(
    archive_path: Path,
    dest_dir: Path,
    selected_members: List[str],
    sevenzip_exe: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    dest_dir = Path(dest_dir)
    filtered = [m for m in selected_members if not is_non_data_artifact(m)]
    extract_archive(
        archive_path, dest_dir, members=filtered, sevenzip_exe=sevenzip_exe, log=log
    )


# --------------------------------------------------------------------------- simple file placement


def place_simple_file(source_path: Path, targets: DownloadTargets) -> Path:
    """Places a downloaded plain (non-archive) file at its effective
    destination: if a destination target was given and differs from the
    central-repo location, the file is copied there, preserving
    whatever subdirectory structure it already had beneath central_repo
    (source/category/layer, from build_download_subdirectory) rather
    than flattening it into the destination root. Fixed alongside a
    related, actual reported bug (a mosaic built from these files
    landing at a generic, unnested location - see
    ui.download_flow._process_simple_files) - a flattened copy here
    would have undermined that fix too, in exactly the case where a
    separate Destination folder is chosen, since the mosaic is placed
    alongside wherever its own source tiles actually ended up.
    Otherwise (no separate destination, or one equal to central_repo)
    the file is left exactly where it was downloaded."""
    destination_dir = targets.effective_destination
    if destination_dir == targets.central_repo:
        return source_path
    destination_dir = Path(destination_dir)
    try:
        relative = source_path.relative_to(targets.central_repo)
    except ValueError:
        # Not actually beneath central_repo - shouldn't normally
        # happen (download_items always places under central_repo
        # first), but falls back to a flat copy rather than raising,
        # matching this function's own prior behavior for that edge
        # case rather than turning an unexpected path shape into a
        # hard failure mid-download.
        relative = Path(source_path.name)
    dest_path = destination_dir / relative
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path != source_path:
        shutil.copy2(source_path, dest_path)
    return dest_path
