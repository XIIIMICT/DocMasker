"""
Thin Valkey wrapper over the artifact store: bounded, TTL-based, outage-tolerant.

Cache outages degrade to misses, never to failed operations. GC stays
conservative: artifacts may outlive their Valkey key by the grace window.
"""

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import valkey

from src.metrics import observe_cache_operation

from .artifacts import CacheArtifactStore, CacheGcReport, cache_gc_stats
from .keys import CACHE_ENTRY_VERSION, CacheEntry, canonical_json

if TYPE_CHECKING:
    from src.config import Settings

LOGGER = logging.getLogger("docmasker.cache")

#: An artifact may outlive a Valkey key by this much, so an LRU/TTL removal or
#: a stale mtime can never make a live key point at a deleted file. Reuse
#: refreshes the artifact's mtime before the key is (re)published.
GC_GRACE_SECONDS = 3600.0
#: Even a misconfigured short TTL keeps artifacts for at least a day, so a key
#: published under an earlier, longer TTL cannot become a dangling reference.
GC_MIN_RETENTION_SECONDS = 86400.0


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
