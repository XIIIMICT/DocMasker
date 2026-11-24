"""
DOCX package validation for the file endpoints.

The endpoints must not trust the extension or the client: a malformed archive
reached `python-docx` and became an unhandled exception (HTTP 500), and a ZIP
bomb could exhaust memory. `validate_docx_package` checks the actual OPC/ZIP
structure and enforces resource limits, so the router can answer 413/415 with
a clear Russian `detail` instead of crashing.
"""

import io
import zipfile
from dataclasses import dataclass

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


def validate_docx_package(data: bytes, limits: DocxLimits) -> None:
    """
    Validate an uploaded DOCX as an OPC/ZIP package against `limits`.

    Raises `DocxValidationError` with a Russian message; the router turns that
    into 413 (resource limits) or 415 (malformed/unsupported) instead of letting
    the exception reach `python-docx` and surface as a 500.
    """
    size = len(data)
    if size == 0:
        raise DocxValidationError(_EMPTY_FILE)
    if size > limits.max_upload_bytes:
        raise _too_large(_OVERSIZE_UPLOAD.format(limit_mb=limits.max_upload_bytes // (1024 * 1024)))

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise DocxValidationError(_BAD_ZIP) from error

    with archive:
        infos = archive.infolist()
        if len(infos) > limits.max_entry_count:
            raise _too_large(_TOO_MANY_ENTRIES)

        names = {info.filename for info in infos}
        if any(part not in names for part in _REQUIRED_PARTS):
            raise DocxValidationError(_MISSING_PARTS)

        total_uncompressed = 0
        for info in infos:
            if info.flag_bits & _ZIP_FLAG_ENCRYPTED:
                raise DocxValidationError(_ENCRYPTED)
            total_uncompressed += info.file_size
            if info.compress_size > 0:
                ratio = info.file_size / info.compress_size
                # A huge ratio only matters when the entry is actually large
                # (every tiny XML part compresses hundreds-fold).
                if ratio > _MAX_COMPRESSION_RATIO and info.file_size > limits.max_uncompressed_bytes / 100:
                    raise _too_large(_ZIP_BOMB)
        if total_uncompressed > limits.max_uncompressed_bytes:
            raise _too_large(_OVERSIZE_UNCOMPRESSED)


def upload_limits() -> DocxLimits:
    """DOCX package limits derived from the settings (single source: root .env)."""
    settings = get_settings()
    return DocxLimits(
        max_upload_bytes=settings.max_upload_mb * 1024 * 1024,
        max_uncompressed_bytes=settings.max_upload_mb * 10 * 1024 * 1024,
    )
