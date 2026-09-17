"""
download.targets - the target-location model used by the download
pipeline, and persistence for user-defined named bookmarks.

A download always has a central-repo target (where a file is downloaded
to). A destination target is required when the downloaded file is an
archive (extraction is mandatory, so a destination is needed to extract
into) and optional when it is a plain file (the file is already usable
as downloaded, so the central-repo location can double as the
destination with no extra copy step). See download.pipeline for how
these targets are actually used.
"""

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

BOOKMARKS_FILENAME = "sigate_target_bookmarks.json"

# Default safety margin applied on top of a raw byte requirement before
# treating a free-space check as passing: whichever is larger of a
# percentage of the requirement, or a flat minimum. Filling a filesystem
# to its last byte causes problems well before it is actually full.
FREE_SPACE_MARGIN_FRACTION = 0.05
FREE_SPACE_MARGIN_MINIMUM_BYTES = 100 * 1024 * 1024  # 100 MB


@dataclass(frozen=True)
class DownloadTargets:
    """The two target locations for one download operation."""

    central_repo: Path
    destination: Optional[Path] = None

    @property
    def effective_destination(self) -> Path:
        """The destination to actually use: the explicit destination if
        one was given, otherwise the central-repo location itself."""
        return self.destination if self.destination is not None else self.central_repo


@dataclass(frozen=True)
class TargetBookmark:
    """A user-defined named location, reusable across downloads."""

    name: str
    path: str


def _bookmarks_path(data_dir: Path) -> Path:
    return Path(data_dir) / BOOKMARKS_FILENAME


def load_bookmarks(data_dir: Path) -> List[TargetBookmark]:
    """Reads the user's saved target bookmarks. Returns an empty list if
    none have been saved yet."""
    path = _bookmarks_path(data_dir)
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [TargetBookmark(**entry) for entry in raw]


def save_bookmarks(data_dir: Path, bookmarks: List[TargetBookmark]) -> None:
    path = Path(data_dir)
    path.mkdir(parents=True, exist_ok=True)
    with open(_bookmarks_path(path), "w", encoding="utf-8") as f:
        json.dump([asdict(b) for b in bookmarks], f, indent=2, ensure_ascii=False)


def add_or_replace_bookmark(
    data_dir: Path, bookmark: TargetBookmark
) -> List[TargetBookmark]:
    """Adds a new bookmark, or replaces an existing one with the same
    name. Returns the updated full list, already saved to disk."""
    existing = load_bookmarks(data_dir)
    updated = [b for b in existing if b.name != bookmark.name]
    updated.append(bookmark)
    save_bookmarks(data_dir, updated)
    return updated


def required_bytes_with_margin(byte_count: int) -> int:
    """Applies the standard safety margin on top of a raw byte
    requirement."""
    margin = max(
        int(byte_count * FREE_SPACE_MARGIN_FRACTION), FREE_SPACE_MARGIN_MINIMUM_BYTES
    )
    return byte_count + margin


def has_enough_free_space(path: Path, required_byte_count: int) -> bool:
    """Checks whether the filesystem containing `path` has enough free
    space for `required_byte_count` bytes, including the standard safety
    margin. `path` does not need to exist yet - its parent is used if it
    doesn't."""
    check_path = Path(path)
    while not check_path.exists():
        check_path = check_path.parent
    free_bytes = shutil.disk_usage(check_path).free
    return free_bytes >= required_bytes_with_margin(required_byte_count)
