"""
download.archive - archive format detection and handling: plain zip/tar
via the standard library, and 7z via a detected external executable
(never bundled, to avoid adding a plugin-side Python dependency in an
environment where the available Python packages can vary).

Split 7z archives (a single logical archive delivered as multiple parts,
named `name.7z.001`, `name.7z.002`, and so on) are grouped and always
operated on via their first part - the 7-Zip command-line tool finds the
remaining parts itself, provided they are all present in the same
directory as the first one.

The 7z-invoking functions accept an optional `log(message)` callback,
called with the exact command about to run. Kept as a plain injected
callable rather than importing a logging mechanism directly, so this
module stays Qt/QGIS-independent; the real plugin supplies a
QgsMessageLog-backed logger (see ui.download_flow), tests can supply a
list-capturing fake, and the default (None) means silent.
"""

import re
import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

ARCHIVE_KIND_ZIP = "zip"
ARCHIVE_KIND_TAR = "tar"
ARCHIVE_KIND_7Z = "7z"

_SPLIT_7Z_PART_PATTERN = re.compile(
    r"^(?P<base>.+\.7z)\.(?P<part>\d{3,})$", re.IGNORECASE
)

_TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")

# Files that are never real archive content, regardless of source -
# filesystem/metadata artifacts left behind by how an archive happened to
# be created or transferred.
_NON_DATA_ARTIFACT_NAMES = {".DS_Store"}
_NON_DATA_ARTIFACT_PREFIX = "._"

# Well-known 7-Zip executable names to search for on PATH.
_SEVENZIP_EXECUTABLE_NAMES = ("7z", "7za", "7zr")

# Default installation paths on Windows, where the 7-Zip installer does
# not always add itself to PATH.
_SEVENZIP_WINDOWS_DEFAULT_PATHS = (
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
)


@dataclass(frozen=True)
class ArchiveMember:
    name: str
    size: Optional[int]
    is_dir: bool


def is_non_data_artifact(member_path: str) -> bool:
    """True for filesystem/metadata artifacts that should never be
    presented as selectable archive content (e.g. macOS AppleDouble
    sidecar files, `.DS_Store`)."""
    basename = member_path.replace("\\", "/").rsplit("/", 1)[-1]
    return basename in _NON_DATA_ARTIFACT_NAMES or basename.startswith(
        _NON_DATA_ARTIFACT_PREFIX
    )


def is_split_7z_part(filename: str) -> bool:
    return _SPLIT_7Z_PART_PATTERN.match(filename) is not None


def group_split_7z_parts(filenames: List[str]) -> Dict[str, List[str]]:
    """Groups filenames that are parts of a split 7z archive by their
    shared base name, sorted so the first part comes first. Filenames
    that are not part of a split archive are not included in the result."""
    groups: Dict[str, List[str]] = {}
    for name in filenames:
        match = _SPLIT_7Z_PART_PATTERN.match(name)
        if match:
            groups.setdefault(match.group("base"), []).append(name)
    for parts in groups.values():
        parts.sort()
    return groups


def detect_archive_kind(filename: str) -> Optional[str]:
    """Returns one of ARCHIVE_KIND_ZIP/TAR/7Z, or None if the filename
    does not look like a recognized archive format. Split 7z parts are
    recognized as ARCHIVE_KIND_7Z regardless of which part is given."""
    lower = filename.lower()
    if lower.endswith(".zip"):
        return ARCHIVE_KIND_ZIP
    if lower.endswith(_TAR_SUFFIXES):
        return ARCHIVE_KIND_TAR
    if lower.endswith(".7z") or is_split_7z_part(filename):
        return ARCHIVE_KIND_7Z
    return None


def archive_base_name(filename: str) -> str:
    """The archive's own name with its format-specific extension(s)
    stripped - correctly handling a split 7z part's own two-suffix
    naming (e.g. "RGEALTI_D073.7z.001" -> "RGEALTI_D073", not
    "RGEALTI_D073.7z") and a compound tar suffix (e.g.
    "foo.tar.gz" -> "foo", not "foo.tar"). Used to name a per-archive
    extraction subfolder, so extracting more than one archive into the
    same destination can't mix their contents together."""
    split_match = _SPLIT_7Z_PART_PATTERN.match(filename)
    if split_match:
        return split_match.group("base")[: -len(".7z")]
    lower = filename.lower()
    for suffix in _TAR_SUFFIXES:
        if lower.endswith(suffix):
            return filename[: -len(suffix)]
    for suffix in (".7z", ".zip"):
        if lower.endswith(suffix):
            return filename[: -len(suffix)]
    return Path(filename).stem


def first_7z_part(path: Path) -> Path:
    """Given any part of a split 7z archive (or a plain, unsplit .7z
    file), returns the path 7-Zip operations should actually be pointed
    at - the first part for a split archive, or the file itself if it is
    not split."""
    match = _SPLIT_7Z_PART_PATTERN.match(path.name)
    if not match:
        return path
    sibling_parts = sorted(
        p.name
        for p in path.parent.glob(f"{match.group('base')}.*")
        if is_split_7z_part(p.name)
    )
    if not sibling_parts:
        return path
    return path.parent / sibling_parts[0]


def find_7z_executable(extra_search_paths: Optional[List[str]] = None) -> Optional[str]:
    """Searches PATH for a 7-Zip executable, then a small set of known
    Windows default install locations that may not be on PATH, then any
    caller-supplied extra paths. Returns the first match found, or None
    if no 7-Zip executable could be located."""
    for name in _SEVENZIP_EXECUTABLE_NAMES:
        found = shutil.which(name)
        if found:
            return found
    for candidate in _SEVENZIP_WINDOWS_DEFAULT_PATHS:
        if Path(candidate).is_file():
            return candidate
    for candidate in extra_search_paths or []:
        if Path(candidate).is_file():
            return candidate
    return None


def _list_zip(path: Path) -> List[ArchiveMember]:
    with zipfile.ZipFile(path) as zf:
        return [
            ArchiveMember(name=info.filename, size=info.file_size, is_dir=info.is_dir())
            for info in zf.infolist()
        ]


def _list_tar(path: Path) -> List[ArchiveMember]:
    with tarfile.open(path) as tf:
        return [
            ArchiveMember(name=member.name, size=member.size, is_dir=member.isdir())
            for member in tf.getmembers()
        ]


def _parse_7z_slt_listing(output: str) -> List[ArchiveMember]:
    """Parses `7z l -slt` output. The output starts with an archive-level
    header block (itself containing a `Path = ` line for the archive
    file, which would otherwise be mistaken for a member) followed by a
    line of dashes, then one blank-line-separated block per actual
    member. Only content after that separator line is parsed as members."""
    lines = output.splitlines()
    try:
        separator_index = next(
            i for i, line in enumerate(lines) if line.strip().startswith("----------")
        )
    except StopIteration:
        return []
    member_lines = lines[separator_index + 1 :]

    members = []
    current: Dict[str, str] = {}

    def _flush():
        if "Path" not in current:
            return
        size_str = current.get("Size", "")
        attributes = current.get("Attributes", "")
        members.append(
            ArchiveMember(
                name=current["Path"],
                size=int(size_str) if size_str.isdigit() else None,
                is_dir="D" in attributes,
            )
        )

    for line in member_lines:
        line = line.rstrip()
        if not line:
            _flush()
            current = {}
            continue
        if " = " in line:
            key, _, value = line.partition(" = ")
            current[key] = value
    _flush()
    return members


def _log_command(log: Optional[Callable[[str], None]], cmd: List[str]) -> None:
    if log is not None:
        log("+ " + " ".join(cmd))


def _list_7z(
    path: Path, sevenzip_exe: str, log: Optional[Callable[[str], None]] = None
) -> List[ArchiveMember]:
    target = first_7z_part(path)
    cmd = [sevenzip_exe, "l", "-slt", str(target)]
    _log_command(log, cmd)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
    )
    return _parse_7z_slt_listing(result.stdout)


def list_archive(
    path: Path,
    sevenzip_exe: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
) -> List[ArchiveMember]:
    """Lists the contents of an archive, dispatching by detected format.
    For a 7z archive, `path` may be any part of a split archive - the
    first part is located and used automatically."""
    kind = detect_archive_kind(path.name)
    if kind == ARCHIVE_KIND_ZIP:
        return _list_zip(path)
    if kind == ARCHIVE_KIND_TAR:
        return _list_tar(path)
    if kind == ARCHIVE_KIND_7Z:
        if not sevenzip_exe:
            raise ValueError(
                "listing a 7z archive requires a detected 7-Zip executable"
            )
        return _list_7z(path, sevenzip_exe, log=log)
    raise ValueError(f"unrecognized archive format: {path.name}")


def _extract_zip(path: Path, dest_dir: Path, members: Optional[List[str]]) -> None:
    with zipfile.ZipFile(path) as zf:
        zf.extractall(dest_dir, members=members)


def _extract_tar(path: Path, dest_dir: Path, members: Optional[List[str]]) -> None:
    with tarfile.open(path) as tf:
        if members is None:
            tf.extractall(dest_dir, filter="data")
        else:
            wanted = set(members)
            selected = [m for m in tf.getmembers() if m.name in wanted]
            tf.extractall(dest_dir, members=selected, filter="data")


def _extract_7z(
    path: Path,
    dest_dir: Path,
    members: Optional[List[str]],
    sevenzip_exe: str,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    target = first_7z_part(path)
    cmd = [sevenzip_exe, "x", str(target), f"-o{dest_dir}", "-y"]
    if members:
        cmd.extend(members)
    _log_command(log, cmd)
    subprocess.run(cmd, check=True, capture_output=True)


def extract_archive(
    path: Path,
    dest_dir: Path,
    members: Optional[List[str]] = None,
    sevenzip_exe: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    """Extracts an archive to dest_dir. If `members` is given, only those
    entries are extracted; otherwise everything is. For a 7z archive,
    `path` may be any part of a split archive."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    kind = detect_archive_kind(path.name)
    if kind == ARCHIVE_KIND_ZIP:
        _extract_zip(path, dest_dir, members)
    elif kind == ARCHIVE_KIND_TAR:
        _extract_tar(path, dest_dir, members)
    elif kind == ARCHIVE_KIND_7Z:
        if not sevenzip_exe:
            raise ValueError(
                "extracting a 7z archive requires a detected 7-Zip executable"
            )
        _extract_7z(path, dest_dir, members, sevenzip_exe, log=log)
    else:
        raise ValueError(f"unrecognized archive format: {path.name}")
