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
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import valkey

if TYPE_CHECKING:
    from src.config import Settings

LOGGER = logging.getLogger("docmasker.cache")

#: Cache key / entry schema. Bump CACHE_NAMESPACE_VERSION when either changes.
CACHE_KEY_SCHEMA_VERSION = 1
CACHE_ENTRY_VERSION = 1
#: Increment these release-checklist constants when the corresponding behavior
#: changes output (brief §4.3). The values live in Settings so operators can
#: bump them via env without a code deploy; these defaults document the floor.
ENGINE_COMPAT_VERSION = 1


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

    digest_input = settings.masking_config_digest
    if digest_input == "auto":
        digest_input = config_digest()
    payload = {
        "operation": fingerprint.operation,
        "input": fingerprint.input_hash,
        "keys": fingerprint.keys_hash,
        "output_name": fingerprint.output_name,
        "ruleset": settings.masking_ruleset_version,
        "config_digest": digest_input,
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

    def publish(self, data: bytes) -> CacheEntry:
        """Write an immutable artifact once; its id is the content hash."""
        digest = sha256_hex(data)
        target = self._path(digest)
        if not target.is_file():
            fd, tmp = tempfile.mkstemp(prefix=".cache-", dir=str(self._root))
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
        return CacheEntry(
            artifact_id=digest,
            content_hash=digest,
            size=len(data),
            content_type="application/zip",
            manifest_version=CACHE_ENTRY_VERSION,
        )

    def read(self, entry: CacheEntry) -> bytes | None:
        """Return the artifact bytes, or None when missing/corrupt (cache miss)."""
        path = self._path(entry.artifact_id)
        try:
            data = path.read_bytes()
        except OSError:
            return None
        if len(data) != entry.size or sha256_hex(data) != entry.content_hash:
            return None
        return data

    def exists(self, entry: CacheEntry) -> bool:
        return self.read(entry) is not None

    def delete(self, artifact_id: str) -> None:
        with contextlib.suppress(OSError, ValueError):
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

    @property
    def metrics(self) -> dict[str, int]:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "writes": self._writes,
            "failures": self._failures,
            "evictions": self._evictions,
        }

    def _evict(self, key: str) -> None:
        self._evictions += 1
        self._misses += 1
        if self._client is None:
            return
        try:
            self._client.delete(key)
        except Exception:  # noqa: BLE001
            self._failures += 1

    def get(self, key: str) -> bytes | None:
        """Lookup: verify the entry and its artifact before serving a hit."""
        if self._client is None:
            self._misses += 1
            return None
        try:
            raw = self._client.get(key)
        except Exception:  # noqa: BLE001
            self._failures += 1
            self._misses += 1
            LOGGER.warning("cache read failed; treating as miss")
            return None
        if raw is None:
            self._misses += 1
            return None
        entry = self._parse_entry(str(raw))
        if entry is None:
            self._evict(key)
            return None
        data = self._store.read(entry)
        if data is None:
            self._evict(key)
            return None
        self._hits += 1
        return data

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
        except Exception:  # noqa: BLE001
            self._failures += 1
            LOGGER.warning("cache write failed; result is still valid uncached")
            return None
        self._writes += 1
        return entry

    def invalidate(self, key: str) -> None:
        if self._client is None:
            return
        try:
            self._client.delete(key)
        except Exception:  # noqa: BLE001
            self._failures += 1


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
