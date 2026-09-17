"""
Tests for download.integrity's generic verify_hash dispatcher - added
alongside the pre-existing MD5-specific functions when gateways.stac's
sha256 checksum needed a second algorithm, without disturbing any
existing MD5 caller. The pipeline-level expected_hash/hash_ok wiring
itself is covered in test_pipeline.py; this file is just the dispatcher
in isolation.
"""

import hashlib

import pytest

from sigate.download.integrity import compute_sha256, verify_hash


def test_verify_hash_sha256_true_for_matching_digest(tmp_path):
    path = tmp_path / "f.bin"
    content = b"some file content"
    path.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()
    assert verify_hash(path, "sha256", expected) is True


def test_verify_hash_sha256_false_for_mismatched_digest(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"some file content")
    assert verify_hash(path, "sha256", "0" * 64) is False


def test_verify_hash_is_case_insensitive_on_both_algorithm_name_and_digest(tmp_path):
    path = tmp_path / "f.bin"
    content = b"case insensitivity check"
    path.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest().upper()
    assert verify_hash(path, "SHA256", expected) is True


def test_verify_hash_still_supports_md5_through_the_same_dispatcher(tmp_path):
    path = tmp_path / "f.bin"
    content = b"md5 via the generic dispatcher"
    path.write_bytes(content)
    expected = hashlib.md5(content).hexdigest()
    assert verify_hash(path, "md5", expected) is True


def test_verify_hash_rejects_unknown_algorithm_rather_than_silently_skipping(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"content")
    with pytest.raises(ValueError):
        verify_hash(path, "sha1", "0" * 40)


def test_compute_sha256_matches_hashlib_directly(tmp_path):
    path = tmp_path / "f.bin"
    content = b"a" * 5_000_000  # spans multiple 1MB read chunks
    path.write_bytes(content)
    assert compute_sha256(path) == hashlib.sha256(content).hexdigest()
