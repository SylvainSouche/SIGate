"""
download.extraction_structure - classifies archive contents into a data
role (actual data, metadata, or supplementary files) so a user can be
shown a checklist of what to extract without needing to inspect the
archive layout by hand.

Fallback order:
  1. A general packaging convention: numbered role folders anywhere in
     the archive's directory tree, named `{N}_{ROLE}_LIVRAISON_{id}`
     (N = 1/2/3, ROLE = DONNEES/METADONNEES/SUPPLEMENTS). This pattern
     has been found at varying depths within different archives from the
     same producer, so the whole tree is scanned rather than assuming a
     fixed depth.
  2. A per-product override, keyed by the stable product-name prefix of
     the archive (the part of the name before any version/date/tile
     suffix), for cases that don't follow the numbered convention.
  3. A simple keyword heuristic on folder names (folders containing
     "DATA"/"DONNEES" are treated as data; folders containing
     "DOC"/"METADATA"/"METADONNEES" are excluded), used only when neither
     of the above applies.

Non-data filesystem artifacts (see download.archive.is_non_data_artifact)
are filtered out of the result regardless of which rule classified their
containing folder.
"""

import json
import re
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional

from .archive import is_non_data_artifact

ROLE_DATA = "DONNEES"
ROLE_METADATA = "METADONNEES"
ROLE_SUPPLEMENTS = "SUPPLEMENTS"
ROLE_UNCLASSIFIED = "UNCLASSIFIED"

_NUMBERED_ROLE_FOLDER_PATTERN = re.compile(
    r"^\d+_(DONNEES|METADONNEES|SUPPLEMENTS)_LIVRAISON_.+$"
)
_PRODUCT_KEY_PATTERN = re.compile(r"^([A-Z][A-Z0-9]*)")

_HEURISTIC_DATA_KEYWORDS = ("DATA", "DONNEES")
_HEURISTIC_EXCLUDED_KEYWORDS = ("DOC", "METADATA", "METADONNEES")

OVERRIDES_FILENAME = "sigate_extraction_overrides.json"


def extract_product_key(archive_name: str) -> str:
    """Returns the stable product-name prefix of an archive or file name
    - the leading run of uppercase letters and digits, which stays
    constant across different editions of the same product (differing
    department, resolution, date, or tile) - or the full name if no such
    prefix is found."""
    match = _PRODUCT_KEY_PATTERN.match(archive_name)
    return match.group(1) if match else archive_name


def _classify_by_numbered_folders(paths: List[str]) -> Optional[Dict[str, List[str]]]:
    result: Dict[str, List[str]] = {
        ROLE_DATA: [],
        ROLE_METADATA: [],
        ROLE_SUPPLEMENTS: [],
        ROLE_UNCLASSIFIED: [],
    }
    found_any = False
    for path in paths:
        role = ROLE_UNCLASSIFIED
        for part in PurePosixPath(path).parts:
            match = _NUMBERED_ROLE_FOLDER_PATTERN.match(part)
            if match:
                role = match.group(1)
                found_any = True
                break
        result[role].append(path)
    return result if found_any else None


def _classify_by_heuristic(paths: List[str]) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {
        ROLE_DATA: [],
        ROLE_METADATA: [],
        ROLE_SUPPLEMENTS: [],
        ROLE_UNCLASSIFIED: [],
    }
    for path in paths:
        parts_upper = [part.upper() for part in PurePosixPath(path).parts]
        if any(
            any(keyword in part for keyword in _HEURISTIC_EXCLUDED_KEYWORDS)
            for part in parts_upper
        ):
            result[ROLE_METADATA].append(path)
        elif any(
            any(keyword in part for keyword in _HEURISTIC_DATA_KEYWORDS)
            for part in parts_upper
        ):
            result[ROLE_DATA].append(path)
        else:
            result[ROLE_UNCLASSIFIED].append(path)
    return result


def load_product_overrides(data_dir: Path) -> Dict[str, Dict[str, List[str]]]:
    """Reads user-saved per-product classification overrides, keyed by
    product key. Returns an empty dict if none have been saved yet."""
    path = Path(data_dir) / OVERRIDES_FILENAME
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_product_override(
    data_dir: Path, product_key: str, classification: Dict[str, List[str]]
) -> None:
    """Saves a classification result as the override for a given product
    key, so future archives from the same product classify the same way
    without needing the numbered-folder convention or the heuristic to
    apply."""
    path = Path(data_dir)
    path.mkdir(parents=True, exist_ok=True)
    overrides = load_product_overrides(path)
    overrides[product_key] = classification
    with open(path / OVERRIDES_FILENAME, "w", encoding="utf-8") as f:
        json.dump(overrides, f, indent=2, ensure_ascii=False)


def classify_archive_contents(
    paths: List[str], product_key: Optional[str] = None, data_dir: Optional[Path] = None
) -> Dict[str, List[str]]:
    """Classifies archive member paths into DONNEES/METADONNEES/
    SUPPLEMENTS/UNCLASSIFIED, applying the fallback order described in
    this module's docstring. Non-data filesystem artifacts are removed
    from the result entirely, not merely left unclassified.

    A result found via the numbered-folder convention is used as-is - it
    does not consult or get overridden by a saved per-product entry,
    since the general convention is trusted directly when it applies.
    """
    filtered = [p for p in paths if not is_non_data_artifact(p)]

    by_numbered_folders = _classify_by_numbered_folders(filtered)
    if by_numbered_folders is not None:
        return by_numbered_folders

    if product_key and data_dir is not None:
        overrides = load_product_overrides(data_dir)
        if product_key in overrides:
            saved = overrides[product_key]
            # A saved override records which paths were data/metadata/etc
            # for a previous archive of this product; re-apply the same
            # *pattern* (folder name membership) to the current path list
            # rather than replaying the exact saved paths, since the
            # specific tile/file names will differ between editions.
            saved_data_folder_names = {
                PurePosixPath(p).parts[0] for p in saved.get(ROLE_DATA, []) if p
            }
            if saved_data_folder_names:
                result: Dict[str, List[str]] = {
                    ROLE_DATA: [],
                    ROLE_METADATA: [],
                    ROLE_SUPPLEMENTS: [],
                    ROLE_UNCLASSIFIED: [],
                }
                for path in filtered:
                    top = (
                        PurePosixPath(path).parts[0]
                        if PurePosixPath(path).parts
                        else ""
                    )
                    role = (
                        ROLE_DATA
                        if top in saved_data_folder_names
                        else ROLE_UNCLASSIFIED
                    )
                    result[role].append(path)
                return result

    return _classify_by_heuristic(filtered)
