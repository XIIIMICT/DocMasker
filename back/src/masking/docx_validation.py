"""
DOCX package validation for the file endpoints.

The endpoints must not trust the extension or the client: a malformed archive
reached `python-docx` and became an unhandled exception (HTTP 500), and a ZIP
bomb could exhaust memory. `validate_docx_package` checks the actual OPC/ZIP
structure and enforces resource limits, so the router can answer 413/415 with
a clear Russian `detail` instead of crashing.
"""

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from src.config import get_settings

#: Parts every valid DOCX (WordprocessingML package) must contain.
_REQUIRED_PARTS = ("[Content_Types].xml", "word/document.xml")

#: Entries above this count are rejected before any content is read.
_ENTRY_COUNT_LIMIT = 10_000

#: Compression ratio above which an entry is treated as a zip-bomb attempt.
#: Legitimate OOXML parts compress 2-10x; a ratio far beyond that with a large
#: uncompressed size has no business in a document.
_MAX_COMPRESSION_RATIO = 1_000

#: The "encrypted" general-purpose bit in a ZIP entry header.
_ZIP_FLAG_ENCRYPTED = 0x1

_EMPTY_FILE = "Файл пуст."
_OVERSIZE_UPLOAD = "Файл больше допустимого ({limit_mb} МБ)."
_TOO_MANY_ENTRIES = "Файл содержит слишком много внутренних частей."
_MISSING_PARTS = "Файл не является корректным DOCX (отсутствуют части пакета Word)."
_BAD_ZIP = "Файл не является корректным DOCX (повреждённый архив)."
_ENCRYPTED = "Зашифрованные DOCX не поддерживаются."
_ZIP_BOMB = "Файл выглядит как архив-бомба и отклонён."
_OVERSIZE_UNCOMPRESSED = "Файл распаковывается в слишком большой объём данных."


class DocxValidationError(ValueError):
    """The uploaded DOCX is malformed, unsupported or exceeds the limits."""

    def __init__(self, message: str, *, http_status: int = 415) -> None:
        super().__init__(message)
        self.http_status = http_status


def _too_large(message: str) -> DocxValidationError:
    """Build the resource-limit variant of the error (the router answers 413)."""
    return DocxValidationError(message, http_status=413)


@dataclass(frozen=True, slots=True)
class DocxLimits:
    """Resource limits applied to an uploaded DOCX package."""

    max_upload_bytes: int
    max_uncompressed_bytes: int
    max_entry_count: int = _ENTRY_COUNT_LIMIT


_BAD_ENTRY_NAME = "Архив содержит небезопасные имена внутренних частей."
_DUPLICATE_ENTRY_NAME = "Архив содержит повторяющиеся имена внутренних частей."


def _validate_entry_names(names: list[str]) -> None:
    """Reject hostile ZIP entry names before any processing (P1.1)."""
    seen: set[str] = set()
    for name in names:
        if (
            not name
            or name.startswith(("/", "\\"))
            or re.match(r"^[A-Za-z]:", name)
            or "\\" in name
            or ".." in name.split("/")
            or "\x00" in name
            or any(ord(char) < 32 for char in name)
            or any(part.startswith(".") and part != ".rels" for part in name.split("/"))
            or name in seen
        ):
            raise DocxValidationError(_DUPLICATE_ENTRY_NAME if name in seen else _BAD_ENTRY_NAME)
        seen.add(name)
    for required in _REQUIRED_PARTS:
        if names.count(required) != 1:
            raise DocxValidationError(_BAD_ENTRY_NAME)


def _validate_zip(archive: zipfile.ZipFile, limits: DocxLimits) -> None:
    """Shared OPC/ZIP checks over an already-opened ZipFile (no full read)."""
    infos = archive.infolist()
    if len(infos) > limits.max_entry_count:
        raise _too_large(_TOO_MANY_ENTRIES)

    names = [info.filename for info in infos]
    if any(part not in names for part in _REQUIRED_PARTS):
        raise DocxValidationError(_MISSING_PARTS)
    _validate_entry_names(names)

    total_uncompressed = 0
    for info in infos:
        if info.flag_bits & _ZIP_FLAG_ENCRYPTED:
            raise DocxValidationError(_ENCRYPTED)
        total_uncompressed += info.file_size
        if info.compress_size > 0:
            ratio = info.file_size / info.compress_size
            # A huge ratio only matters when the entry is actually large.
            if ratio > _MAX_COMPRESSION_RATIO and info.file_size > limits.max_uncompressed_bytes / 100:
                raise _too_large(_ZIP_BOMB)
    if total_uncompressed > limits.max_uncompressed_bytes:
        raise _too_large(_OVERSIZE_UNCOMPRESSED)


def _check_size(size: int, limits: DocxLimits) -> None:
    if size == 0:
        raise DocxValidationError(_EMPTY_FILE)
    if size > limits.max_upload_bytes:
        raise _too_large(_OVERSIZE_UPLOAD.format(limit_mb=limits.max_upload_bytes // (1024 * 1024)))


def validate_docx_package(data: bytes, limits: DocxLimits) -> None:
    """
    Validate an uploaded DOCX as an OPC/ZIP package against `limits`.

    Raises `DocxValidationError` with a Russian message; the router turns that
    into 413 (resource limits) or 415 (malformed/unsupported) instead of letting
    the exception reach the transformer and surface as a 500.
    """
    _check_size(len(data), limits)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise DocxValidationError(_BAD_ZIP) from error
    with archive:
        _validate_zip(archive, limits)


def validate_docx_package_path(path: Path, limits: DocxLimits) -> None:
    """
    Validate a staged upload from its filesystem path (Phase 6 §8.5/§10).

    `zipfile.ZipFile` opens the file lazily and reads only the central
    directory — the full document is never loaded into a single `bytes`
    object here. Use this for the job-submit upload path; sync endpoints keep
    the in-memory variant because the OPC transformer needs the whole package.
    """
    try:
        size = path.stat().st_size
    except OSError as error:
        raise DocxValidationError(_BAD_ZIP) from error
    _check_size(size, limits)
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as error:
        raise DocxValidationError(_BAD_ZIP) from error
    with archive:
        _validate_zip(archive, limits)


def upload_limits() -> DocxLimits:
    """DOCX package limits derived from the settings (single source: root .env)."""
    settings = get_settings()
    return DocxLimits(
        max_upload_bytes=settings.max_upload_mb * 1024 * 1024,
        max_uncompressed_bytes=settings.max_upload_mb * 10 * 1024 * 1024,
    )
