"""
Versioned result cache (Phase 3, ADR-0003 / brief §4).

Valkey holds only compact JSON entries pointing to immutable result artifacts
on the private volume; large DOCX/ZIP payloads never enter Valkey. Keys are
HMAC-SHA256 digests over a canonical fingerprint of every output-affecting
input plus version/config digests, so no raw hashes or PII are guessable or
logged. Cache outages degrade to misses, never to failed operations.
"""

import contextlib
import hashlib
import hmac
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import valkey

from src.metrics import observe_cache_gc, observe_cache_operation

if TYPE_CHECKING:
    from src.config import Settings

LOGGER = logging.getLogger("docmasker.cache")

#: Cache key / entry schema. Bump CACHE_NAMESPACE_VERSION when either changes.
#: 2: the canonical config snapshot digest is always in the key, even when a
#: manual MASKING_CONFIG_DIGEST override is configured.
CACHE_KEY_SCHEMA_VERSION = 2
CACHE_ENTRY_VERSION = 1
#: Increment these release-checklist constants when the corresponding behavior
#: changes output (brief §4.3). The values live in Settings so operators can
#: bump them via env without a code deploy; these defaults document the floor.
ENGINE_COMPAT_VERSION = 1
#: An artifact may outlive a Valkey key by this much, so an LRU/TTL removal or
#: a stale mtime can never make a live key point at a deleted file. Reuse
#: refreshes the artifact's mtime before the key is (re)published.
GC_GRACE_SECONDS = 3600.0
#: Even a misconfigured short TTL keeps artifacts for at least a day, so a key
#: published under an earlier, longer TTL cannot become a dangling reference.
GC_MIN_RETENTION_SECONDS = 86400.0
#: Guards artifact write/touch/collect in one process (publishers and GC share
#: it). The mtime re-check plus atomic unlink keeps cross-process races safe.
_ARTIFACT_LOCK = threading.RLock()
_GC_STATS = {"candidates": 0, "deleted": 0, "reclaimed_bytes": 0, "failures": 0}


def cache_gc_stats() -> dict[str, int]:
    """Safe aggregate counters (no artifact ids, keys or cache payloads)."""
    with _ARTIFACT_LOCK:
        return dict(_GC_STATS)


@dataclass(frozen=True, slots=True)
class CacheFingerprint:
    """Every output-affecting input of one operation (§4.2)."""

    operation: str
    input_hash: str
    keys_hash: str | None = None
    output_name: str | None = None


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """Compact Valkey value: a reference to an immutable artifact."""

    artifact_id: str
    content_hash: str
    size: int
    content_type: str
    manifest_version: int
    entry_version: int = CACHE_ENTRY_VERSION


@dataclass(slots=True)
class CacheGcReport:
    """Bounded result of one cache-artifact collection pass."""

    candidates: int = 0
    deleted: int = 0
    reclaimed_bytes: int = 0
    failures: int = 0


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> str:  # noqa: ANN401 (arbitrary JSON structure)
    """Stable serialization: sorted keys, compact separators, UTF-8."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_cache_key(settings: Settings, fingerprint: CacheFingerprint) -> str:
    """
    `docmasker:result:v{VERSION}:{operation}:{HMAC(fingerprint)}`.

    The fingerprint includes the exact input/keys hashes, a normalized output
    name when archive entry names depend on it, and every version/digest that
    can change the result. HMAC with a stable secret prevents offline guessing
    of predictable PII inputs and keeps source hashes out of key names.
    """
    from src.masking.config import config_digest

    # The loaded snapshot always participates: a manual override can only add
    # invalidation, never disable it (e.g. MASKING_CONFIG_DIGEST=none).
    digest_input = config_digest()
    payload = {
        "operation": fingerprint.operation,
        "input": fingerprint.input_hash,
        "keys": fingerprint.keys_hash,
        "output_name": fingerprint.output_name,
        "ruleset": settings.masking_ruleset_version,
        "config_digest": digest_input,
        "config_digest_override": settings.masking_config_digest,
        "manifest": settings.result_manifest_version,
        "keys_format": settings.keys_format_version,
        "docx_transformer": settings.docx_transformer_version,
        "engine": ENGINE_COMPAT_VERSION,
        "entry": CACHE_ENTRY_VERSION,
        "key_schema": CACHE_KEY_SCHEMA_VERSION,
    }
    digest = hmac.new(
        settings.cache_key_secret.encode("utf-8"),
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"docmasker:result:v{settings.cache_namespace_version}:{fingerprint.operation}:{digest}"


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


class ResultCache:
    """Thin Valkey wrapper: bounded, TTL-based, outage-tolerant (§4.5)."""

    def __init__(self, settings: Settings, store: CacheArtifactStore) -> None:
        self._settings = settings
        self._store = store
        self._client: Any = None  # valkey.Valkey | None; Any keeps test doubles type-safe
        self._disabled = not settings.cache_enabled
        self._hits = 0
        self._misses = 0
        self._writes = 0
        self._failures = 0
        self._evictions = 0
        if not self._disabled:
            try:
                self._client = valkey.Valkey.from_url(
                    settings.valkey_url,
                    socket_connect_timeout=settings.cache_connect_timeout_seconds,
                    socket_timeout=settings.cache_socket_timeout_seconds,
                    decode_responses=True,
                )
                self._client.ping()
            except Exception:  # noqa: BLE001 - cache outage is a miss, not an error
                LOGGER.warning("Valkey unavailable; continuing uncached")
                self._client = None
                self._failures += 1
                observe_cache_operation(operation="failure", outcome="error")

    def ping(self) -> bool:
        """Readiness probe for the Valkey dependency (never raises)."""
        if self._client is None:
            return False
        try:
            self._client.ping()
        except Exception:  # noqa: BLE001 - cache outage is not an API failure
            observe_cache_operation(operation="failure", outcome="error")
            return False
        return True

    @property
    def metrics(self) -> dict[str, int]:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "writes": self._writes,
            "failures": self._failures,
            "evictions": self._evictions,
            **{f"gc_{name}": value for name, value in cache_gc_stats().items()},
        }

    def _evict(self, key: str) -> None:
        self._evictions += 1
        self._misses += 1
        observe_cache_operation(operation="eviction", outcome="ok")
        if self._client is None:
            return
        try:
            self._client.delete(key)
        except Exception:  # noqa: BLE001
            self._failures += 1
            observe_cache_operation(operation="failure", outcome="error")

    def get(self, key: str) -> bytes | None:
        """Lookup: verify the entry and its artifact before serving a hit."""
        if self._client is None:
            self._misses += 1
            observe_cache_operation(operation="miss", outcome="ok")
            return None
        try:
            raw = self._client.get(key)
        except Exception:  # noqa: BLE001
            self._failures += 1
            self._misses += 1
            observe_cache_operation(operation="failure", outcome="error")
            observe_cache_operation(operation="miss", outcome="ok")
            LOGGER.warning("cache read failed; treating as miss")
            return None
        if raw is None:
            self._misses += 1
            observe_cache_operation(operation="miss", outcome="ok")
            return None
        entry = self._parse_entry(str(raw))
        if entry is None:
            self._evict(key)
            return None
        data = self._store.read(entry)
        if data is None:
            self._evict(key)
            return None
        self._store.touch(entry.artifact_id)  # best effort: reuse refreshes retention
        self._hits += 1
        observe_cache_operation(operation="hit", outcome="ok")
        return data

    def get_to_path(self, key: str, target: Path) -> bool:
        """Stream a verified cache hit into `target`; never returns payload bytes."""
        if self._client is None:
            self._misses += 1
            observe_cache_operation(operation="miss", outcome="ok")
            return False
        try:
            raw = self._client.get(key)
        except Exception:  # noqa: BLE001
            self._failures += 1
            self._misses += 1
            observe_cache_operation(operation="failure", outcome="error")
            observe_cache_operation(operation="miss", outcome="ok")
            return False
        if raw is None:
            self._misses += 1
            observe_cache_operation(operation="miss", outcome="ok")
            return False
        entry = self._parse_entry(str(raw))
        if entry is None or not self._store.copy_to(entry, target):
            self._evict(key)
            return False
        self._store.touch(entry.artifact_id)
        self._hits += 1
        observe_cache_operation(operation="hit", outcome="ok")
        return True

    @staticmethod
    def _parse_entry(raw: str) -> CacheEntry | None:
        try:
            payload = json.loads(raw)
            entry = CacheEntry(**payload)
        except Exception:  # noqa: BLE001
            return None
        return entry if entry.entry_version == CACHE_ENTRY_VERSION else None

    def put(self, key: str, data: bytes) -> CacheEntry | None:
        """Publish an immutable artifact, then store its reference with TTL."""
        if self._client is None:
            return None
        vanished = False
        try:
            entry = self._store.publish(data)
            payload = canonical_json(
                {
                    "artifact_id": entry.artifact_id,
                    "content_hash": entry.content_hash,
                    "size": entry.size,
                    "content_type": entry.content_type,
                    "manifest_version": entry.manifest_version,
                    "entry_version": entry.entry_version,
                }
            )
            self._client.set(key, payload, ex=int(self._settings.cache_ttl_seconds))
            # A GC pass may have removed the artifact just before publication:
            # never leave a live key pointing at a missing file.
            if not self._store.is_present(entry.artifact_id):
                self._client.delete(key)
                vanished = True
        except Exception:  # noqa: BLE001
            self._failures += 1
            observe_cache_operation(operation="failure", outcome="error")
            LOGGER.warning("cache write failed; result is still valid uncached")
            return None
        if vanished:
            self._failures += 1
            observe_cache_operation(operation="failure", outcome="error")
            LOGGER.warning("cache artifact vanished during publication")
            return None
        self._writes += 1
        observe_cache_operation(operation="write", outcome="ok")
        return entry

    def put_from_path(self, key: str, source: Path) -> CacheEntry | None:
        """Stream-publish a result artifact; Valkey keeps only its reference."""
        if self._client is None:
            return None
        vanished = False
        try:
            entry = self._store.publish_from_path(source)
            payload = canonical_json(
                {
                    "artifact_id": entry.artifact_id,
                    "content_hash": entry.content_hash,
                    "size": entry.size,
                    "content_type": entry.content_type,
                    "manifest_version": entry.manifest_version,
                    "entry_version": entry.entry_version,
                }
            )
            self._client.set(key, payload, ex=int(self._settings.cache_ttl_seconds))
            if not self._store.is_present(entry.artifact_id):
                self._client.delete(key)
                vanished = True
        except Exception:  # noqa: BLE001
            self._failures += 1
            observe_cache_operation(operation="failure", outcome="error")
            LOGGER.warning("cache write failed; result is still valid uncached")
            return None
        if vanished:
            self._failures += 1
            observe_cache_operation(operation="failure", outcome="error")
            return None
        self._writes += 1
        observe_cache_operation(operation="write", outcome="ok")
        return entry

    def gc(
        self,
        *,
        dry_run: bool = False,
        retention_seconds: float | None = None,
    ) -> CacheGcReport:
        """Collect stale artifacts; a transient error is a harmless no-op."""
        retention = (
            max(self._settings.cache_ttl_seconds, GC_MIN_RETENTION_SECONDS) + GC_GRACE_SECONDS
            if retention_seconds is None
            else retention_seconds
        )
        try:
            return self._store.collect(retention, dry_run=dry_run)
        except Exception:  # noqa: BLE001 - GC never breaks masking or jobs
            LOGGER.warning("cache artifact GC failed; skipping this pass")
            self._failures += 1
            observe_cache_operation(operation="gc", outcome="error")
            return CacheGcReport(failures=1)

    def invalidate(self, key: str) -> None:
        if self._client is None:
            return
        try:
            self._client.delete(key)
        except Exception:  # noqa: BLE001
            self._failures += 1
            observe_cache_operation(operation="failure", outcome="error")


def fingerprint_for(
    operation: str,
    *,
    input_bytes: bytes | None = None,
    input_digest: str | None = None,
    keys_bytes: bytes | None = None,
    filename: str = "",
) -> CacheFingerprint:
    """
    Build the canonical fingerprint for one operation (§4.2).

    `input_digest` lets the streaming job-submit path reuse a SHA-256 computed
    from a staged file without ever materialising the whole upload in RAM
    (Phase 6 §8.5); the in-memory path passes `input_bytes` as before.
    """
    if input_digest is None:
        if input_bytes is None:
            msg = "Either input_bytes or input_digest is required."
            raise ValueError(msg)
        input_digest = sha256_hex(input_bytes)
    return CacheFingerprint(
        operation=operation,
        input_hash=input_digest,
        keys_hash=sha256_hex(keys_bytes) if keys_bytes is not None else None,
        # Archive entry names depend on the filename stem, so it is part of the
        # fingerprint (§4.2 / test 8). Text operations ignore it.
        output_name=Path(filename).stem if operation.endswith("_file") else None,
    )
