"""Tests for DOCX package validation (structure, encryption, zip-bomb and size limits)."""

import io
import warnings
import zipfile

import pytest

from src.masking.docx_validation import DocxLimits, DocxValidationError, validate_docx_package


def _limits(max_upload_bytes: int = 25 * 1024 * 1024, max_uncompressed_bytes: int = 250 * 1024 * 1024) -> DocxLimits:
    return DocxLimits(max_upload_bytes=max_upload_bytes, max_uncompressed_bytes=max_uncompressed_bytes)


def _minimal_docx_bytes() -> bytes:
    """Build a structurally valid OPC package with the required Word parts."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    return buffer.getvalue()


def test_valid_package_passes() -> None:
    validate_docx_package(_minimal_docx_bytes(), _limits())


def test_garbage_bytes_are_rejected_as_malformed() -> None:
    with pytest.raises(DocxValidationError, match="повреждённый архив") as error:
        validate_docx_package(b"definitely not a zip archive" * 10, _limits())
    assert error.value.http_status == 415


def test_zip_without_word_parts_is_rejected() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "not a document")
    with pytest.raises(DocxValidationError, match="отсутствуют части пакета"):
        validate_docx_package(buffer.getvalue(), _limits())


def test_oversized_upload_is_rejected_with_413() -> None:
    with pytest.raises(DocxValidationError, match="больше допустимого") as error:
        validate_docx_package(b"x" * 2048, _limits(max_upload_bytes=1024))
    assert error.value.http_status == 413


def test_too_many_entries_are_rejected_with_413() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
        for index in range(50):
            archive.writestr(f"word/media/image{index}.png", b"0")
    limits = DocxLimits(max_upload_bytes=25 * 1024 * 1024, max_uncompressed_bytes=250 * 1024 * 1024, max_entry_count=10)
    with pytest.raises(DocxValidationError, match="слишком много") as error:
        validate_docx_package(buffer.getvalue(), limits)
    assert error.value.http_status == 413


def test_zip_bomb_ratio_is_rejected_with_413() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
        # Highly compressible payload with a big uncompressed size: extreme ratio.
        archive.writestr("word/huge.xml", b"0" * (10 * 1024 * 1024))
    with pytest.raises(DocxValidationError, match="архив-бомба") as error:
        validate_docx_package(buffer.getvalue(), _limits(max_uncompressed_bytes=1024 * 1024))
    assert error.value.http_status == 413


def test_oversized_uncompressed_content_is_rejected_with_413() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
        archive.writestr("word/blob.xml", b"0" * (2 * 1024 * 1024))
    with pytest.raises(DocxValidationError, match="слишком большой объём") as error:
        validate_docx_package(buffer.getvalue(), _limits(max_uncompressed_bytes=1024 * 1024))
    assert error.value.http_status == 413


def _package_with_entry(name: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
        archive.writestr(name, "payload")
    return buffer.getvalue()


@pytest.mark.parametrize(
    "hostile",
    [
        "../private.txt",
        "word/../../private.txt",
        "..\\..\\private.txt",
        "C:\\private.txt",
        "/etc/passwd",
        "word/ok\x01.xml",
    ],
)
def test_hostile_entry_names_are_rejected(hostile: str) -> None:
    with pytest.raises(DocxValidationError, match="небезопасные имена"):
        validate_docx_package(_package_with_entry(hostile), _limits())


def test_duplicate_entry_names_are_rejected() -> None:
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<document/>")
            archive.writestr("word/document.xml", "duplicate")
    with pytest.raises(DocxValidationError, match="повторяющиеся имена"):
        validate_docx_package(buffer.getvalue(), _limits())


def test_encrypted_entries_are_rejected() -> None:
    # zipfile clears the encryption flag on write, so flip the byte in the raw
    # local file header (general-purpose bit 0 lives at offset 6 of "PK\x03\x04").
    data = bytearray(_minimal_docx_bytes())
    # zipfile reads flags from the central-directory header (PK\x01\x02);
    # general-purpose bit 0 (encrypted) sits at offset +8 of the signature.
    central_offset = bytes(data).find(b"PK\x01\x02")
    data[central_offset + 8] |= 0x1
    with pytest.raises(DocxValidationError, match="Зашифрованные DOCX"):
        validate_docx_package(bytes(data), _limits())
