"""
Artifact spool readiness gate (worker startup + job admission).

The check is deliberately narrow: one free-space comparison and one small
write probe in the private spool. It never reads user data, never leaves a
partial artifact and maps every failure to one stable client-visible code.
"""

import os
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path

ARTIFACT_STORAGE_CODE = "artifact_storage_unavailable"

_INSPECT_DETAIL = "Хранилище артефактов недоступно."
_FULL_DETAIL = "Недостаточно свободного места для сохранения результатов задания."
_UNWRITABLE_DETAIL = "Хранилище артефактов недоступно для записи."


class ArtifactStorageError(RuntimeError):
    """Typed storage-precondition failure; `code`/`detail` are safe for the API envelope."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def check_artifact_storage(root: str | Path, min_free_bytes: int) -> None:
    """Fail closed when the artifact volume is unreadable, unwritable or too full."""
    path = Path(root)
    try:
        path.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(path)
    except OSError as error:
        raise ArtifactStorageError(ARTIFACT_STORAGE_CODE, _INSPECT_DETAIL) from error
    if usage.free < min_free_bytes:
        raise ArtifactStorageError(ARTIFACT_STORAGE_CODE, _FULL_DETAIL)
    _probe_write(path)


def _probe_write(path: Path) -> None:
    """Create and remove one probe file; a failure means results cannot be published."""
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".dm-readiness-", dir=str(path))
        try:
            os.write(descriptor, b"ok")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ArtifactStorageError(ARTIFACT_STORAGE_CODE, _UNWRITABLE_DETAIL) from error
    finally:
        if temporary is not None:
            with suppress(OSError):
                Path(temporary).unlink()
