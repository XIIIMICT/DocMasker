"""Flat, safe ZIP entry names for sync and job result archives (P1.1)."""

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from src.masking.archives import (
    mark_cache_hit,
    mask_archive,
    restore_archive,
    safe_stem,
    write_archive_to_path,
)


def test_safe_stem_discards_paths_and_controls() -> None:
    assert safe_stem("..\\..\\private.docx") == "private"
    assert safe_stem("C:\\Users\\me\\private.docx") == "private"
    assert safe_stem("/var/tmp/private.docx") == "private"  # noqa: S108 - test input
    assert safe_stem("..") == "document"
    assert safe_stem("CON.docx") == "document"
    assert safe_stem("a\x00b.docx") == "ab"
    assert safe_stem("Отчёт 2026.docx") == "Отчёт 2026"
    assert len(safe_stem("x" * 500 + ".docx")) == 120


def test_mask_and_restore_archives_are_flat_for_both_paths() -> None:
    manifest = {"schema_version": 1}
    for base in ("private", safe_stem("..\\..\\private.docx")):
        for payload in (
            mask_archive(b"docx", "keys", base, manifest),
            restore_archive(b"docx", base, manifest),
        ):
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names = archive.namelist()
            assert all("/" not in name and "\\" not in name for name in names)
            assert any(name.endswith(".docx") for name in names)
            assert "manifest.json" in names
            assert all("private" in name or name == "manifest.json" for name in names)


def _manifest(payload: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return json.loads(archive.read("manifest.json"))


def test_write_archive_to_path_streams_file_entries(tmp_path: Path) -> None:
    document = tmp_path / "masked.docx"
    document.write_bytes(b"docx" * 100)
    target = tmp_path / "result.zip"
    write_archive_to_path(
        target,
        {
            "masked.docx": document,
            "manifest.json": b"{}",
        },
    )
    with zipfile.ZipFile(target) as archive:
        assert archive.read("masked.docx") == b"docx" * 100
        assert archive.read("manifest.json") == b"{}"


def test_mark_cache_hit_rewrites_only_the_new_archive() -> None:
    original = mask_archive(
        b"docx",
        "keys",
        "doc",
        {"schema_version": 1, "cache": {"hit": False, "namespace_version": 1}},
    )
    marked = mark_cache_hit(original)
    assert marked != original
    assert _manifest(original)["cache"]["hit"] is False
    assert _manifest(marked)["cache"]["hit"] is True
