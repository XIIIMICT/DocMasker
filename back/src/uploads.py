"""
Bounded streaming upload staging (Phase 6, §8.5).

Uploads are copied chunk-by-chunk into a private temporary file with a real
byte counter (never trusting Content-Length), so a large multipart body never
has to be materialised as one `bytes` object in memory.
"""

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from src.config import get_settings
from src.masking.docx_validation import DocxLimits, upload_limits
from src.metrics import observe_upload

_CHUNK = 64 * 1024


@dataclass(frozen=True, slots=True)
class StagedUpload:
    path: Path
    size: int
    sha256: str


class UploadTooLargeError(ValueError):
    """The streamed upload exceeded the configured byte limit."""


def _too_large() -> None:
    msg = "Файл больше допустимого."
    raise UploadTooLargeError(msg)


def _unlink(path: Path) -> None:
    """Sync helper so async staging never calls a blocking Path method directly."""
    path.unlink(missing_ok=True)


async def stage_upload(file: UploadFile, *, limits: DocxLimits | None = None) -> StagedUpload:
    """
    Stream `file` into a private temp file, enforcing the real byte count.

    Raises `UploadTooLargeError` when the streamed size exceeds the limit —
    this works even when Content-Length is absent or forged. The caller must
    remove the staged path when done (see `cleanup`).
    """
    active_limits = limits or upload_limits()
    max_bytes = active_limits.max_upload_bytes
    root = Path(get_settings().jobs_dir)
    root.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 (one-shot, tiny dir)
    fd, name = tempfile.mkstemp(prefix=".upload-", dir=str(root))
    path = Path(name)
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(fd, "wb") as stream:
            while True:
                chunk = await file.read(_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    _too_large()
                digest.update(chunk)
                stream.write(chunk)
    except Exception:
        _unlink(path)
        observe_upload(outcome="error")
        raise
    observe_upload(outcome="ok")
    return StagedUpload(path=path, size=total, sha256=digest.hexdigest())


def cleanup(staged: StagedUpload) -> None:
    _unlink(staged.path)


def read_staged(staged: StagedUpload) -> bytes:
    """Read a staged file only when it is needed in memory (small/text paths)."""
    return staged.path.read_bytes()
