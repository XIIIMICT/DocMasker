"""
Immutable, content-addressed result artifacts and their conservative GC.

Large DOCX/ZIP payloads stay on the private volume; Valkey holds only compact
references. Selection reads metadata only and re-checks mtime under a shared
lock before unlink, so reuse refreshment cannot be made dangling by a sweep.
"""

import contextlib
import hashlib
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from src.metrics import observe_cache_gc, observe_cache_operation

from .keys import CACHE_ENTRY_VERSION, CacheEntry, sha256_hex

#: Guards artifact write/touch/collect in one process (publishers and GC share
#: it). The mtime re-check plus atomic unlink keeps cross-process races safe.
_ARTIFACT_LOCK = threading.RLock()
_GC_STATS = {"candidates": 0, "deleted": 0, "reclaimed_bytes": 0, "failures": 0}


@dataclass(slots=True)
class CacheGcReport:
    """Bounded result of one cache-artifact collection pass."""

    candidates: int = 0
    deleted: int = 0
    reclaimed_bytes: int = 0
    failures: int = 0


def cache_gc_stats() -> dict[str, int]:
    """Safe aggregate counters (no artifact ids, keys or cache payloads)."""
    with _ARTIFACT_LOCK:
        return dict(_GC_STATS)


class CacheArtifactStore:
    """Immutable, content-addressed result artifacts (no Valkey blobs)."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root) / "cache"
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, artifact_id: str) -> Path:
        allowed = set("0123456789abcdef")
        if len(artifact_id) != 64 or any(char not in allowed for char in artifact_id):
            raise ValueError("Некорректный идентификатор артефакта кэша.")
        return self._root / artifact_id

    def _write_atomic(self, target: Path, data: bytes) -> None:
        fd, tmp = tempfile.mkstemp(prefix=".cache-", dir=str(self._root))
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            Path(tmp).replace(target)
        finally:
            leftover = Path(tmp)
            with contextlib.suppress(OSError):
                if leftover.exists():
                    leftover.unlink()

    def publish(self, data: bytes) -> CacheEntry:
        """Publish an immutable artifact; reuse refreshes its retention clock."""
        digest = sha256_hex(data)
        target = self._path(digest)
        now_ns = time.time_ns()
        with _ARTIFACT_LOCK:
            try:
                os.utime(target, ns=(now_ns, now_ns))
            except FileNotFoundError:
                self._write_atomic(target, data)
        return CacheEntry(
            artifact_id=digest,
            content_hash=digest,
            size=len(data),
            content_type="application/zip",
            manifest_version=CACHE_ENTRY_VERSION,
        )

    def publish_from_path(self, source: Path) -> CacheEntry:
        """Publish a large result by streaming; its id is the content hash."""
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        artifact_id = digest.hexdigest()
        target = self._path(artifact_id)
        with _ARTIFACT_LOCK:
            if not target.is_file():
                fd, tmp = tempfile.mkstemp(prefix=".cache-", dir=str(self._root))
                try:
                    with source.open("rb") as reader, os.fdopen(fd, "wb") as writer:
                        shutil.copyfileobj(reader, writer, length=1024 * 1024)
                        writer.flush()
                        os.fsync(writer.fileno())
                    Path(tmp).replace(target)
                finally:
                    leftover = Path(tmp)
                    with contextlib.suppress(OSError):
                        if leftover.exists():
                            leftover.unlink()
            else:
                os.utime(target, ns=(time.time_ns(), time.time_ns()))
        return CacheEntry(artifact_id, artifact_id, size, "application/zip", CACHE_ENTRY_VERSION)

    def copy_to(self, entry: CacheEntry, target: Path) -> bool:
        """Hash-verify a cached artifact while copying it to a local path."""
        try:
            source_path = self._path(entry.artifact_id)
        except ValueError:
            return False
        digest = hashlib.sha256()
        size = 0
        fd, tmp = tempfile.mkstemp(prefix=".cache-copy-", dir=str(target.parent))
        try:
            with source_path.open("rb") as reader, os.fdopen(fd, "wb") as writer:
                for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            verified = size == entry.size and digest.hexdigest() == entry.content_hash
        except OSError:
            verified = False
        if verified:
            Path(tmp).replace(target)
        else:
            with contextlib.suppress(OSError):
                Path(tmp).unlink()
        return verified

    def touch(self, artifact_id: str) -> bool:
        """Refresh retention when an existing artifact is reused (a cache hit)."""
        now_ns = time.time_ns()
        with _ARTIFACT_LOCK:
            try:
                os.utime(self._path(artifact_id), ns=(now_ns, now_ns))
            except OSError, ValueError:
                return False
            else:
                return True

    def is_present(self, artifact_id: str) -> bool:
        """Cheap existence check (no content read) for publication verification."""
        with _ARTIFACT_LOCK:
            try:
                return self._path(artifact_id).is_file()
            except OSError, ValueError:
                return False

    def read(self, entry: CacheEntry) -> bytes | None:
        """Return the artifact bytes, or None when missing/corrupt (cache miss)."""
        try:
            path = self._path(entry.artifact_id)
            data = path.read_bytes()
        except OSError, ValueError:
            return None
        if len(data) != entry.size or sha256_hex(data) != entry.content_hash:
            return None
        return data

    def exists(self, entry: CacheEntry) -> bool:
        return self.read(entry) is not None

    def select_gc_candidates(
        self,
        retention_seconds: float,
        *,
        now: float | None = None,
    ) -> tuple[tuple[str, int, int], ...]:
        """List stale artifacts by metadata only (never reads their contents)."""
        threshold_ns = int(((time.time() if now is None else now) - retention_seconds) * 1_000_000_000)
        candidates: list[tuple[str, int, int]] = []
        paths: list[Path] = []
        with _ARTIFACT_LOCK:
            with contextlib.suppress(OSError):
                paths = list(self._root.iterdir())
            for path in paths:
                if path.name.startswith(".cache-"):
                    continue
                try:
                    if not path.is_file():
                        continue
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_mtime_ns <= threshold_ns:
                    candidates.append((path.name, stat.st_size, stat.st_mtime_ns))
        return tuple(sorted(candidates))

    def collect(
        self,
        retention_seconds: float,
        *,
        dry_run: bool = False,
        now: float | None = None,
    ) -> CacheGcReport:
        """
        Delete only artifacts whose retention clock has expired.

        A file refreshed between selection and deletion is skipped (the mtime
        is re-checked under the shared lock), so a live Valkey reference is
        never made dangling by this pass.
        """
        candidates = self.select_gc_candidates(retention_seconds, now=now)
        report = CacheGcReport(candidates=len(candidates))
        if not dry_run:
            threshold_ns = int(((time.time() if now is None else now) - retention_seconds) * 1_000_000_000)
            for artifact_id, size, mtime_ns in candidates:
                with _ARTIFACT_LOCK:
                    try:
                        path = self._path(artifact_id)
                        stat = path.stat()
                    except OSError, ValueError:
                        continue
                    if stat.st_mtime_ns != mtime_ns or stat.st_mtime_ns > threshold_ns:
                        continue
                    try:
                        path.unlink()
                    except OSError:
                        report.failures += 1
                        continue
                report.deleted += 1
                report.reclaimed_bytes += size
        with _ARTIFACT_LOCK:
            _GC_STATS["candidates"] = report.candidates
            _GC_STATS["deleted"] += report.deleted
            _GC_STATS["reclaimed_bytes"] += report.reclaimed_bytes
            _GC_STATS["failures"] += report.failures
        observe_cache_gc(
            candidates=report.candidates,
            deleted=report.deleted,
            reclaimed_bytes=report.reclaimed_bytes,
            failures=report.failures,
        )
        observe_cache_operation(operation="gc", outcome="error" if report.failures else "ok")
        return report

    def delete(self, artifact_id: str) -> None:
        with _ARTIFACT_LOCK, contextlib.suppress(OSError, ValueError):
            self._path(artifact_id).unlink()
