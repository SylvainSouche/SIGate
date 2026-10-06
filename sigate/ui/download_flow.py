"""
ui.download_flow - shared orchestration for turning a resolved list of
download items into files on disk and layers on the map.

Extracted from the Bulk Listing tab so the WFS tab's file-index use
pattern (a WFS feature carrying a url attribute pointing to a real file -
confirmed real for IGN's LiDAR HD ":dalle" layer) can reuse exactly the
same target validation, size/free-space checks, download, integrity
check, and archive-vs-simple-file branching, rather than duplicating it.

The actual download step is backed by a real QgsTask
(ui.download_task/ui.download_progress_dialog) rather than running
synchronously on the calling (main UI) thread, so a batch download
doesn't freeze QGIS's UI for its entire duration. progress_runner is the
injection point: production code leaves it as the default (a real modal
progress dialog, ui.download_progress_dialog.run_modal), while tests
supply a synchronous stand-in that calls download.pipeline.download_items()
directly with no real dialog - the same injected-collaborator pattern
already used throughout this project for fetch/download_fn, needed here
specifically because a real QDialog.exec() would otherwise hang forever
in a headless test with nothing to click Cancel.

Every external command this module's archive/mosaic dependencies shell
out to (7-Zip listing/extraction, gdalbuildvrt, gdaladdo) is logged via
QgsMessageLog before it runs. QgsMessageLog was chosen over a plain
print() specifically because it shows up in QGIS's Log Messages panel
regardless of whether the Python Console happens to be open, tagged
"SIGate" so it's easy to find among any other plugin's log output.
log_fn is the injection point (same pattern as progress_runner/
download_fn); unlike progress_runner this one is safe to leave at its
real default even in tests, since QgsMessageLog.logMessage doesn't block
or need user interaction the way a real QDialog does.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Sequence

from qgis.PyQt.QtWidgets import QMessageBox, QWidget

from sigate.download import pipeline
from sigate.download.archive import archive_base_name
from sigate.download.extraction_structure import extract_product_key
from sigate.download.mosaic import (
    build_overviews,
    build_vrt_mosaic,
    find_existing_mosaic,
    has_overviews,
    looks_like_tileable_raster_set,
    mosaic_covers_tiles,
    overview_path,
    raster_tile_paths,
)
from sigate.download.targets import DownloadTargets

from . import settings as sigate_settings
from .layer_groups import group_scope

if TYPE_CHECKING:
    from .target_picker import TargetPicker

_RASTER_EXTENSIONS = {".tif", ".tiff", ".asc", ".jp2", ".img", ".vrt"}
_VECTOR_EXTENSIONS = {".shp", ".gpkg", ".geojson", ".json"}


def _default_progress_runner(
    parent: QWidget,
    items: List[pipeline.DownloadItem],
    central_repo: Path,
    download_fn: Optional[Callable],
):
    from .download_progress_dialog import run_modal

    return run_modal(parent, items, central_repo, download_fn=download_fn)


def _qgis_log(message: str) -> None:
    from qgis.core import Qgis, QgsMessageLog

    QgsMessageLog.logMessage(message, "SIGate", Qgis.MessageLevel.Info)


def guess_layer_kind(path: Path) -> Optional[str]:
    suffix = path.suffix.lower()
    if suffix in _RASTER_EXTENSIONS:
        return "raster"
    if suffix in _VECTOR_EXTENSIONS:
        return "vector"
    if path.name.lower().endswith(".copc.laz"):
        return "point_cloud"
    return None


def run_download_and_add_layers(
    parent: QWidget,
    items: List[pipeline.DownloadItem],
    target_picker: "TargetPicker",
    sevenzip_exe: Optional[str],
    set_status: Callable[[str], None],
    add_layer: Callable[[Path], None],
    download_fn: Optional[Callable] = None,
    progress_runner: Optional[Callable] = None,
    log_fn: Optional[Callable[[str], None]] = None,
    group_base: Sequence[str] = (),
) -> None:
    """Runs the full download-through-add-layer flow for `items` against
    `target_picker`'s current target selection. `set_status` and
    `add_layer` are callbacks supplied by the calling tab, since adding a
    layer means emitting that tab's own `addRasterLayer`/`addVectorLayer`
    signal, not something this shared module can do on its own.

    progress_runner(parent, items, central_repo, download_fn) -> (outcomes,
    was_cancelled) is the injection point for the actual download step -
    see this module's own docstring for why it exists and what the real
    default does. log_fn(message) is the injection point for the
    external-command visibility described in the same docstring."""
    if not items:
        return
    progress_runner = progress_runner or _default_progress_runner
    log_fn = log_fn or _qgis_log

    requires_destination = any(
        pipeline.is_archive_file(item.filename) for item in items
    )
    target_picker.set_require_destination(requires_destination)

    error = target_picker.validation_error()
    if error:
        QMessageBox.warning(parent, parent.tr("Invalid target"), error)
        return
    targets = target_picker.get_targets()

    already_present = pipeline.check_already_downloaded(items, targets.central_repo)
    if any(already_present.values()):
        proceed = QMessageBox.question(
            parent,
            parent.tr("Some files already exist"),
            parent.tr(
                "{} of {} file(s) already exist in the central repository. Use the existing files?"
            ).format(sum(already_present.values()), len(items)),
        )
        if proceed != QMessageBox.StandardButton.Yes:
            for item in items:
                if already_present.get(item.filename):
                    (targets.central_repo / item.filename).unlink()

    total_size = pipeline.combined_declared_size(items)
    if not pipeline.check_free_space_for_download(targets.central_repo, total_size):
        QMessageBox.critical(
            parent,
            parent.tr("Not enough free space"),
            parent.tr("There is not enough free space to download this batch."),
        )
        return
    if pipeline.exceeds_size_warning_threshold(
        total_size, sigate_settings.get_size_warning_threshold_bytes()
    ):
        size_gb = total_size / 1024**3
        proceed = QMessageBox.question(
            parent,
            parent.tr("Large download"),
            parent.tr("This batch totals approximately {:.1f} GB. Continue?").format(
                size_gb
            ),
        )
        if proceed != QMessageBox.StandardButton.Yes:
            return

    set_status(parent.tr("Downloading..."))
    outcomes, was_cancelled = progress_runner(
        parent, items, targets.central_repo, download_fn
    )

    failures = [o for o in outcomes if not o.succeeded and o.path is not None]
    for outcome in failures:
        pipeline.delete_failed_outcome(outcome)
    errored = [o for o in outcomes if o.error and o.error != "Cancelled"]
    if failures or errored:
        QMessageBox.warning(
            parent,
            parent.tr("Some downloads failed"),
            parent.tr(
                "{} download(s) failed an integrity check or errored out and were not kept."
            ).format(len(failures) + len(errored)),
        )

    succeeded = [o for o in outcomes if o.succeeded]
    archive_outcomes = [o for o in succeeded if pipeline.is_archive_file(o.path.name)]
    simple_outcomes = [
        o for o in succeeded if not pipeline.is_archive_file(o.path.name)
    ]

    # Layer-tree placement (ui.layer_groups): group_base (the calling tab's
    # source name) > archive name, or > layer folder (> product) for plain
    # files - mirroring the on-disk source/category/layer folders. Opened
    # here, around the post-download steps, because that is when layers
    # are actually emitted, whichever way progress_runner ran the download.
    for outcome in archive_outcomes:
        with group_scope(*group_base, archive_base_name(outcome.path.name)):
            _process_archive(
                parent, outcome.path, targets, sevenzip_exe, add_layer, log_fn
            )

    if simple_outcomes:
        # Mosaic/pyramid-building is checked here across the whole batch
        # of plain-file downloads, not just within one archive's
        # extracted contents - a WFS file-index download (e.g. LiDAR
        # HD's ":dalle" layer) delivers plain .tif files directly, each
        # its own DownloadItem, never wrapped in an archive at all, so
        # this check has to run independently of the archive path below.
        placed_paths = [
            pipeline.place_simple_file(o.path, targets) for o in simple_outcomes
        ]
        groups = group_outcomes_by_product(simple_outcomes, placed_paths)
        for product, group_paths in groups.items():
            with group_scope(*group_base, group_paths[0].parent.name, product):
                _process_simple_files(
                    parent, group_paths, add_layer, log_fn, product=product
                )

    if was_cancelled:
        set_status(
            parent.tr("Cancelled - {} of {} file(s) completed before stopping.").format(
                len(succeeded), len(items)
            )
        )
        return

    set_status(
        parent.tr("Done: {} of {} file(s) downloaded successfully.").format(
            len(succeeded), len(items)
        )
    )


def group_outcomes_by_product(
    outcomes, placed_paths: List[Path]
) -> Dict[Optional[str], List[Path]]:
    """Splits a batch of downloaded plain files into one group per
    DownloadItem.product (None for items with no product split - the
    common case, one group). IGN's LiDAR HD metadata layer delivers a
    tile's MNT, MNS and MNH in one batch - same format and resolution,
    so they would otherwise look like a single tileable set and be
    mosaicked together."""
    groups: Dict[Optional[str], List[Path]] = {}
    for outcome, path in zip(outcomes, placed_paths):
        groups.setdefault(outcome.item.product, []).append(path)
    return groups


def _process_simple_files(
    parent: QWidget,
    placed_paths: List[Path],
    add_layer: Callable[[Path], None],
    log_fn: Callable[[str], None],
    product: Optional[str] = None,
) -> None:
    """Offers a mosaic (and its pyramid) for a batch of plain,
    non-archive downloaded files that look like tiles of the same area -
    the counterpart to _process_archive's own mosaic offer, for the case
    where the tiles arrived as independent downloads (confirmed real:
    WFS file-index downloads, e.g. LiDAR HD's ":dalle" layer) rather
    than packaged inside one archive.

    The mosaic is written alongside the tiles it's actually built from -
    placed_paths[0].parent, not targets.effective_destination directly -
    fixing a real reported bug ("the vrt file is always named the same,
    its location doesn't follow any subdirectory logic"). Each tile was
    already placed at central_repo/{source}/{category}/{layer}/filename
    (pipeline.build_download_subdirectory, via each DownloadItem's own
    .subdirectory) before this function ever runs; ignoring that and
    writing the mosaic straight into the flat destination root meant
    every source/layer that ever got mosaicked shared the exact same
    mosaic path and generic product_key-only filename, with no
    source/layer isolation at all - a real collision risk, not just an
    organizational inconsistency, unlike _process_archive's own
    destination (already isolated per archive via archive_base_name)."""
    destination = Path(placed_paths[0]).parent
    product_key = extract_product_key(Path(placed_paths[0]).stem)
    if product:
        # one mosaic per product, never one shared name (see
        # group_outcomes_by_product)
        product_key = f"{product_key}_{product}"

    existing_mosaic = find_existing_mosaic(destination, product_key)
    if existing_mosaic:
        mosaic_path = _reuse_or_rebuild_mosaic(
            parent, existing_mosaic, placed_paths, destination, product_key, log_fn
        )
        add_layer(mosaic_path)
        return

    if looks_like_tileable_raster_set(placed_paths):
        build_mosaic = QMessageBox.question(
            parent,
            parent.tr("Build mosaic"),
            parent.tr(
                "{} downloaded file(s) look like tiles of the same area. "
                "Build a single mosaic instead of adding each one separately?"
            ).format(len(placed_paths)),
        )
        if build_mosaic == QMessageBox.StandardButton.Yes:
            mosaic_path = build_vrt_mosaic(
                raster_tile_paths(placed_paths), destination, product_key, log=log_fn
            )
            _build_overviews_best_effort(parent, mosaic_path, log_fn)
            add_layer(mosaic_path)
            return

    for path in placed_paths:
        add_layer(path)


def _process_archive(
    parent: QWidget,
    archive_path: Path,
    targets: DownloadTargets,
    sevenzip_exe: Optional[str],
    add_layer: Callable[[Path], None],
    log_fn: Callable[[str], None],
) -> None:
    try:
        classification = pipeline.list_and_classify_archive(
            archive_path,
            data_dir=targets.effective_destination,
            sevenzip_exe=sevenzip_exe,
            log=log_fn,
        )
    except Exception as e:
        log_fn(f"Could not read archive {archive_path}: {e!r}")
        QMessageBox.warning(parent, parent.tr("Could not read archive"), str(e))
        return

    all_members = [m for members in classification.values() for m in members]
    if not all_members:
        QMessageBox.information(
            parent,
            parent.tr("Nothing to extract"),
            parent.tr("No files were found in this archive."),
        )
        return

    proceed = QMessageBox.question(
        parent,
        parent.tr("Extract archive"),
        parent.tr(
            "Extract {} file(s) from {} into a folder named after the archive?"
        ).format(len(all_members), archive_path.name),
    )
    if proceed != QMessageBox.StandardButton.Yes:
        return

    # Extracted into a subfolder named after the archive itself, not
    # directly into the destination root - so extracting more than one
    # archive into the same destination can't mix their contents
    # together, and every file the archive contained is kept (not just
    # the ones classified as "data"), each still traceable back to
    # which archive it came from by the folder name alone.
    destination = targets.effective_destination / archive_base_name(archive_path.name)
    if not pipeline.check_free_space_for_download(destination, None):
        QMessageBox.critical(
            parent,
            parent.tr("Not enough free space"),
            parent.tr("Not enough free space to extract."),
        )
        return

    pipeline.extract_selected_members(
        archive_path, destination, all_members, sevenzip_exe=sevenzip_exe, log=log_fn
    )
    # Extraction preserves each member's full relative path inside the
    # archive - the resulting file is not flattened to the destination
    # root, so the path must be reconstructed the same way.
    extracted_paths = [destination / Path(member) for member in all_members]
    # Only the actual raster tiles are candidates for a mosaic - the
    # extracted batch here can also include metadata/supplement files
    # (XML, HTML, a tile-index shapefile, ...), which gdalbuildvrt can't
    # meaningfully include.
    tile_candidates = raster_tile_paths(extracted_paths)

    product_key = archive_path.stem.split("_")[0]
    existing_mosaic = find_existing_mosaic(destination, product_key)
    if existing_mosaic:
        # Reused as-is only if it's confirmed to still cover exactly
        # tile_candidates - a mosaic from before overview-building
        # existed, one an earlier build_overviews attempt failed on, or
        # one that's genuinely stale (fewer/different tiles than what's
        # actually on disk now) are all handled the same way here:
        # whatever's actually correct gets returned, with pyramids.
        mosaic_path = _reuse_or_rebuild_mosaic(
            parent, existing_mosaic, tile_candidates, destination, product_key, log_fn
        )
        add_layer(mosaic_path)
        return

    if looks_like_tileable_raster_set(tile_candidates):
        build_mosaic = QMessageBox.question(
            parent,
            parent.tr("Build mosaic"),
            parent.tr(
                "{} tiles were extracted. Build a single mosaic instead of adding each tile separately?"
            ).format(len(tile_candidates)),
        )
        if build_mosaic == QMessageBox.StandardButton.Yes:
            mosaic_path = build_vrt_mosaic(
                tile_candidates, destination, product_key, log=log_fn
            )
            _build_overviews_best_effort(parent, mosaic_path, log_fn)
            add_layer(mosaic_path)
            return

    for extracted_path in extracted_paths:
        add_layer(extracted_path)


def _reuse_or_rebuild_mosaic(
    parent: QWidget,
    existing_mosaic: Path,
    tile_paths: List[Path],
    destination: Path,
    product_key: str,
    log_fn: Callable[[str], None],
) -> Path:
    """Reuses existing_mosaic as-is if it's confirmed to still cover
    exactly the given tile_paths (mosaic_covers_tiles), or rebuilds it
    fresh in place - silently, without re-asking the user, since they
    already opted into having a mosaic at this destination when it was
    first built; keeping it correct doesn't need a second confirmation
    - if it's stale. Fixes the actual reported failure ("the vrt
    however is not built properly and a preexisting one was included"):
    an existing mosaic's mere presence was previously the only thing
    ever checked, with no verification it actually reflected what's
    currently on disk.

    Either way, ensures overviews exist for whatever mosaic is actually
    returned. A rebuilt mosaic's old .ovr sidecar, if any, is deleted
    first - left in place, it would silently keep describing the
    previous, now-wrong VRT content (wrong extent/resolution/tile
    layout) rather than the corrected one, and has_overviews would
    report it as already-built when it no longer corresponds to
    anything real."""
    if mosaic_covers_tiles(existing_mosaic, tile_paths):
        if not has_overviews(existing_mosaic):
            _build_overviews_best_effort(parent, existing_mosaic, log_fn)
        return existing_mosaic
    mosaic_path = build_vrt_mosaic(tile_paths, destination, product_key, log=log_fn)
    stale_overview = overview_path(mosaic_path)
    if stale_overview.is_file():
        stale_overview.unlink()
    _build_overviews_best_effort(parent, mosaic_path, log_fn)
    return mosaic_path


def _build_overviews_best_effort(
    parent: QWidget, vrt_path: Path, log_fn: Callable[[str], None]
) -> None:
    """Builds pyramids for a mosaic VRT - the other half of the real,
    confirmed artifact shape a mosaic is meant to produce (docs/spec.md's
    documented "dem73.vrt" + "dem73.vrt.ovr" pair). A gdaladdo failure
    (missing executable, a genuinely unwritable destination, etc.) is
    reported but doesn't block adding the mosaic itself - the VRT is
    still fully usable without its pyramid, just slower to render at
    reduced zoom, so a failure here shouldn't cost the user the mosaic
    they came for."""
    try:
        build_overviews(vrt_path, log=log_fn)
    except Exception as e:
        log_fn(f"Could not build overviews for {vrt_path}: {e!r}")
        QMessageBox.warning(
            parent,
            parent.tr("Could not build pyramids"),
            parent.tr(
                "The mosaic was created, but building overviews (pyramids) "
                "for it failed: {}\n\nThe mosaic is still fully usable, just "
                "slower to render at reduced zoom without them."
            ).format(e),
        )
