"""Flat, safe ZIP entry names for sync and job result archives (P1.1)."""

import io
import zipfile

from src.masking.archives import mask_archive, restore_archive, safe_stem


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
