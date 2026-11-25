"""
Private spool files for job inputs/results (ADR-0002).

Files are keyed by the opaque job id and never leave the private volume:
`<jobs_dir>/<job_id>.input` / `.keys` / `.result`. Writes are atomic
(temp file + os.replace), names are validated so a client cannot traverse
paths, and TTL sweeping removes every artifact of expired jobs.
"""

import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Job


class ArtifactError(ValueError):
    """An artifact key is invalid or the artifact does not exist."""


class ArtifactStore:
    """Filesystem spool with atomic writes and id-scoped names."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str, suffix: str) -> Path:
        # Job ids are uuid4 strings (hex + dashes, 36 chars). Never accept a
        # path as an artifact key — no traversal outside the spool directory.
        allowed = set("0123456789abcdef-")
        if len(job_id) != 36 or any(char not in allowed for char in job_id.lower()):
            msg = "Некорректный идентификатор задания."
            raise ArtifactError(msg)
        return self._root / f"{job_id}.{suffix}"

    @property
    def root(self) -> Path:
        """Resolved spool root (for explicit containment checks by callers)."""
        return self._root.resolve()

    def path_for(self, job_id: str, suffix: str) -> Path:
        """Absolute path of a stored artifact, validated against the spool root."""
        path = self._path(job_id, suffix).resolve()
        if not path.is_relative_to(self.root):
            msg = "Некорректный идентификатор задания."
            raise ArtifactError(msg)
        return path

    def write(self, job_id: str, suffix: str, data: bytes) -> None:
        """Write `data` atomically (a crash never leaves a half-written file)."""
        target = self._path(job_id, suffix)
        fd, tmp = tempfile.mkstemp(prefix=f".{job_id}.", dir=str(self._root))
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            Path(tmp).replace(target)
        finally:
            leftover = Path(tmp)
            if leftover.exists():
                leftover.unlink()

    def copy_from(self, job_id: str, suffix: str, src: Path) -> None:
        """
        Atomically copy a staged upload into the spool in bounded chunks.

        The whole file is never materialised in one `bytes` object (§8.5/§10);
        use this on the job-submit path instead of `read_staged` + `write`.
        """
        target = self._path(job_id, suffix)
        fd, tmp = tempfile.mkstemp(prefix=f".{job_id}.", dir=str(self._root))
        try:
            with os.fdopen(fd, "wb") as out, src.open("rb") as source:
                while True:
                    chunk = source.read(64 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            Path(tmp).replace(target)
        finally:
            leftover = Path(tmp)
            if leftover.exists():
                leftover.unlink()

    def read(self, job_id: str, suffix: str) -> bytes:
        """Read an artifact, or raise `ArtifactError` when it is gone."""
        path = self._path(job_id, suffix)
        try:
            return path.read_bytes()
        except FileNotFoundError as error:
            msg = "Результат задания недоступен (истёк срок хранения)."
            raise ArtifactError(msg) from error

    def exists(self, job_id: str, suffix: str) -> bool:
        """Whether the artifact still exists."""
        return self._path(job_id, suffix).is_file()

    def size(self, job_id: str, suffix: str) -> int:
        """Artifact size in bytes (0 when missing)."""
        path = self._path(job_id, suffix)
        return path.stat().st_size if path.is_file() else 0

    def delete(self, job_id: str) -> None:
        """Remove every artifact of a job (TTL / cancellation cleanup)."""
        for suffix in ("input", "keys", "result", "error", "name", "kind"):
            path = self._path(job_id, suffix)
            with suppress(FileNotFoundError):
                path.unlink()


def cleanup_artifacts(store: ArtifactStore, jobs: list[Job]) -> int:
    """Delete artifacts of terminal/expired jobs; returns removed job count."""
    removed = 0
    for job in jobs:
        store.delete(job.id)
        removed += 1
    return removed
