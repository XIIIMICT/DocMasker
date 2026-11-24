"""Phase 6 §8.5/§10: bounded streaming upload, path validation, XML heuristics."""

import io
import zipfile
from pathlib import Path

import pytest
from fastapi import UploadFile

from src import uploads
from src.jobs.routing import count_docx_paragraphs
from src.masking.docx_validation import DocxLimits, DocxValidationError, validate_docx_package_path
from src.uploads import UploadTooLargeError, cleanup, read_staged, stage_upload


def _docx_bytes(*, paragraph_count: int = 3, prefix: str = "w", payload: bytes = b"") -> bytes:
    """Minimal OPC package; `prefix` can be any namespace alias."""
    paragraphs = "".join(
        f"<{prefix}:p><{prefix}:r><{prefix}:t>text {i}</{prefix}:t></{prefix}:r></{prefix}:p>"
        for i in range(paragraph_count)
    )
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    document = (
        f'<?xml version="1.0"?><{prefix}:document xmlns:{prefix}="{ns}">'
        f"{paragraphs}{payload.decode('latin-1')}</{prefix}:document>"
    ).encode("latin-1")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def _upload(data: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename="doc.docx")


@pytest.fixture(autouse=True)
def _staging_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    class _Settings:
        jobs_dir = str(tmp_path)

    monkeypatch.setattr(uploads, "get_settings", _Settings)
    return tmp_path


async def test_stage_upload_counts_real_bytes_not_content_length() -> None:
    data = _docx_bytes()
    # No Content-Length is supplied anywhere; the limiter counts streamed bytes.
    staged = await stage_upload(
        _upload(data), limits=DocxLimits(max_upload_bytes=len(data), max_uncompressed_bytes=4096)
    )
    try:
        assert staged.size == len(data)
        assert staged.sha256
        assert read_staged(staged) == data
        assert staged.path.is_file()
    finally:
        cleanup(staged)
    assert not staged.path.exists()


def _leftover_uploads(root: Path) -> list[str]:
    """Sync helper: avoid a blocking Path method inside an async test."""
    return [entry.name for entry in root.iterdir() if entry.name.startswith(".upload-")]


@pytest.mark.usefixtures("_staging_dir")
async def test_stage_upload_rejects_oversized_stream_and_cleans_up(tmp_path: Path) -> None:
    data = b"x" * 1000
    with pytest.raises(UploadTooLargeError):
        await stage_upload(_upload(data), limits=DocxLimits(max_upload_bytes=100, max_uncompressed_bytes=4096))
    # Cancellation/exception-safe cleanup: no unreferenced staging artifact left.
    assert _leftover_uploads(tmp_path) == []


async def test_validate_from_staged_path() -> None:
    data = _docx_bytes()
    limits = DocxLimits(max_upload_bytes=len(data), max_uncompressed_bytes=4096)
    staged = await stage_upload(_upload(data), limits=limits)
    try:
        validate_docx_package_path(staged.path, limits)
    finally:
        cleanup(staged)

    garbage = staged.path.parent / "garbage.docx"
    garbage.write_bytes(b"not a zip" * 20)
    with pytest.raises(DocxValidationError):
        validate_docx_package_path(garbage, DocxLimits(max_upload_bytes=10_000, max_uncompressed_bytes=4096))


@pytest.mark.parametrize("prefix", ["w", "x", "word"])
def test_paragraph_count_is_namespace_independent(prefix: str) -> None:
    assert count_docx_paragraphs(_docx_bytes(paragraph_count=7, prefix=prefix)) == 7


def test_paragraph_count_is_bounded_on_large_xml() -> None:
    # A huge XML part (past the old 64 KiB prefix scan) still counts correctly.
    payload = b"<!-- padding -->" * 10_000
    assert count_docx_paragraphs(_docx_bytes(paragraph_count=3, payload=payload)) == 3


def test_paragraph_count_of_garbage_returns_zero() -> None:
    assert count_docx_paragraphs(b"not a zip" * 20) == 0
