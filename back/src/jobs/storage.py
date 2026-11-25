"""
Private spool files for job inputs/results (ADR-0002).

Files are keyed by the opaque job id and never leave the private volume:
`<jobs_dir>/<job_id>.input` / `.keys` / `.result`. Writes are atomic
(temp file + os.replace), names are validated so a client cannot traverse
paths, and TTL sweeping removes every artifact of expired jobs.
"""

import os
import re
import tempfile
import uuid
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Job


class ArtifactError(ValueError):
    """An artifact key is invalid or the artifact does not exist."""


class ArtifactStore:
    """Filesystem spool with atomic writes and id-scoped names."""

    #: Only these suffixes are ever produced by the backend (no user input).
    _SUFFIXES = frozenset({"input", "keys", "result", "error", "name", "kind"})
    _UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    _RESULT_KEY_RE = re.compile(rf"^({_UUID})\.result\.({_UUID})$")

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _canonical_name(self, job_id: str, suffix: str) -> str:
        """
        Sanitise the untrusted job id into a canonical uuid4 filename.

        Parsing through ``uuid.UUID`` removes any traversal/repetition and
        guarantees a fixed-shape name; the suffix is an internal allowlist.
        """
        if suffix not in self._SUFFIXES:
            msg = "Некорректный идентификатор задания."
            raise ArtifactError(msg)
        try:
            canonical = str(uuid.UUID(job_id))
        except (ValueError, AttributeError, TypeError) as error:
            msg = "Некорректный идентификатор задания."
            raise ArtifactError(msg) from error
        if canonical != job_id.lower():
            msg = "Некорректный идентификатор задания."
            raise ArtifactError(msg)
        return f"{canonical}.{suffix}"

    def _path(self, job_id: str, suffix: str) -> Path:
        return Path(os.path.join(str(self._root), self._canonical_name(job_id, suffix)))  # noqa: PTH118 - CodeQL sanitisation

    @property
    def root(self) -> Path:
        """Resolved spool root (for explicit containment checks by callers)."""
        return Path(os.path.realpath(str(self._root)))

    def path_for(self, job_id: str, suffix: str) -> Path:
        """Return an artifact path proven (by realpath) to stay in the spool."""
        root = os.path.realpath(str(self._root))
        candidate = os.path.realpath(os.path.join(root, self._canonical_name(job_id, suffix)))  # noqa: PTH118 - CodeQL sanitisation
        # CodeQL-recommended containment check: normalise first, then require
        # the normalized path to start with the normalized root.
        if not candidate.startswith(root + os.sep):
            msg = "Некорректный идентификатор задания."
            raise ArtifactError(msg)
        return Path(candidate)

    @classmethod
    def result_key(cls, job_id: str, lease_token: str) -> str:
        """Immutable result key for ONE claim/attempt — never a shared job path."""
        try:
            canonical = str(uuid.UUID(job_id))
            token = str(uuid.UUID(lease_token))
        except (ValueError, AttributeError, TypeError) as error:
            msg = "Некорректный идентификатор задания."
            raise ArtifactError(msg) from error
        return f"{canonical}.result.{token}"

    def path_for_result(self, key: str) -> Path:
        """Resolve a DB-referenced result key, validated against the spool root."""
        match = self._RESULT_KEY_RE.fullmatch(key.lower())
        if match is None:
            msg = "Некорректный идентификатор задания."
            raise ArtifactError(msg)
        root = os.path.realpath(str(self._root))
        candidate = os.path.realpath(os.path.join(root, key))  # noqa: PTH118 - CodeQL sanitisation
        if not candidate.startswith(root + os.sep):
            msg = "Некорректный идентификатор задания."
            raise ArtifactError(msg)
        return Path(candidate)

    def write_result(self, job_id: str, lease_token: str, data: bytes) -> str:
        """Write an attempt-scoped immutable result and return its DB key."""
        key = self.result_key(job_id, lease_token)
        target = self.path_for_result(key)
        self._write_at(target, data)
        return key

    def read_result(self, key: str) -> bytes:
        path = self.path_for_result(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as error:
            msg = "Результат задания недоступен (истёк срок хранения)."
            raise ArtifactError(msg) from error

    def delete_attempt_artifacts(self, job_id: str, *, keep_key: str | None = None) -> list[str]:
        """Remove stale attempt results for a job; never touch a published key."""
        try:
            canonical = str(uuid.UUID(job_id))
        except (ValueError, AttributeError, TypeError) as error:
            msg = "Некорректный идентификатор задания."
            raise ArtifactError(msg) from error
        removed: list[str] = []
        for path in self.root.glob(f"{canonical}.result.*"):
            if keep_key is not None and path.name == keep_key:
                continue
            with suppress(FileNotFoundError):
                path.unlink()
                removed.append(path.name)
        return removed

    def _write_at(self, target: Path, data: bytes) -> None:
        """Atomically write `data` to `target` (temp file + os.replace)."""
        fd, tmp = tempfile.mkstemp(prefix=".dm-", dir=str(self._root))
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

    def write(self, job_id: str, suffix: str, data: bytes) -> None:
        """Write `data` atomically (a crash never leaves a half-written file)."""
        self._write_at(self._path(job_id, suffix), data)

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
        """Remove every artifact of a job (terminal GC / confirmed cancellation)."""
        for suffix in ("input", "keys", "result", "error", "name", "kind"):
            path = self._path(job_id, suffix)
            with suppress(FileNotFoundError):
                path.unlink()
        try:
            canonical = str(uuid.UUID(job_id))
        except ValueError, AttributeError, TypeError:
            return
        for path in self.root.glob(f"{canonical}.result.*"):
            with suppress(FileNotFoundError):
                path.unlink()


def cleanup_artifacts(store: ArtifactStore, jobs: list[Job]) -> int:
    """Delete artifacts of terminal/expired jobs; returns removed job count."""
    removed = 0
    for job in jobs:
        store.delete(job.id)
        removed += 1
    return removed
