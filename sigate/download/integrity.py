"""
download.integrity - post-download integrity checks: byte count against a
declared size, and MD5 verification when a source provides a checksum
file.
"""

import hashlib
import re
from pathlib import Path
from typing import Optional

_MD5_HEX_PATTERN = re.compile(r"\b[a-fA-F0-9]{32}\b")


def verify_byte_count(path: Path, expected_size: Optional[int]) -> bool:
    """Compares a downloaded file's actual size against a declared size.
    Returns True if expected_size is None (nothing to check against)."""
    if expected_size is None:
        return True
    return Path(path).stat().st_size == expected_size


def compute_md5(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def compute_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


_HASH_COMPUTERS = {"md5": compute_md5, "sha256": compute_sha256}


def verify_hash(path: Path, algorithm: str, expected_hex: str) -> bool:
    """Generic dispatcher covering both this module's original MD5-only
    verify_md5 (atom's external .md5-sidecar convention) and sha256
    (STAC's inline "file:checksum" multihash, decoded to a bare hex
    digest by gateways.stac before it ever reaches here - this module
    has no STAC/multihash-format knowledge of its own, matching the
    existing gateways/download boundary: gateway modules resolve their
    own source-specific checksum representation into a plain
    (algorithm, hex_digest) pair; download/ only ever compares bytes
    against an already-decoded hex string). Raises ValueError for an
    unrecognized algorithm name rather than silently skipping the
    check, the same failure mode an unimplemented verification should
    have."""
    computer = _HASH_COMPUTERS.get(algorithm.lower())
    if computer is None:
        raise ValueError(f"Unsupported hash algorithm: {algorithm!r}")
    return computer(path).lower() == expected_hex.lower()


def parse_md5_checksum_file(content: str) -> Optional[str]:
    """Extracts an MD5 hash from checksum file content. Handles either a
    bare hex hash on its own, or the common md5sum-style format
    ("<hash>  <filename>"). Returns None if no 32-character hex string is
    found."""
    match = _MD5_HEX_PATTERN.search(content)
    return match.group(0).lower() if match else None


def verify_md5(path: Path, expected_md5: str) -> bool:
    return compute_md5(path).lower() == expected_md5.lower()
