"""Valkey result-cache tests (Phase 3, brief §4.6)."""

import json
import os
import threading
import time
from pathlib import Path

import pytest

from src.config import Settings
from src.jobs.cache import (
    CacheArtifactStore,
    ResultCache,
    build_cache_key,
    cache_gc_stats,
    canonical_json,
    fingerprint_for,
    sha256_hex,
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "database_url": "postgresql+psycopg://dm:dm@localhost:5432/dm",
            "jobs_dir": str(tmp_path),
            "cache_enabled": True,
            "cache_key_secret": "unit-test-stable-secret-32chars",
            "valkey_url": "redis://127.0.0.1:1/0",  # unreachable -> outage path
            "cache_connect_timeout_seconds": 0.1,
            "cache_socket_timeout_seconds": 0.1,
            "cache_ttl_seconds": 60,
        }
    )


def _key(settings: Settings, *, op: str, data: bytes, keys: bytes | None = None, name: str = "") -> str:
    return build_cache_key(settings, fingerprint_for(op, input_bytes=data, keys_bytes=keys, filename=name))


# --- §4.6 key construction ---------------------------------------------------


def test_same_inputs_produce_the_same_key(settings: Settings) -> None:
    assert _key(settings, op="mask_text", data="Иван Петров".encode()) == _key(
        settings, op="mask_text", data="Иван Петров".encode()
    )


def test_different_docx_bytes_produce_different_keys(settings: Settings) -> None:
    assert _key(settings, op="mask_file", data=b"a", name="x.docx") != _key(
        settings, op="mask_file", data=b"b", name="x.docx"
    )


def test_different_keys_yaml_changes_demask_key(settings: Settings) -> None:
    different = [
        _key(settings, op="unmask_file", data=b"docx", keys=b"k1", name="x.docx"),
        _key(settings, op="unmask_text", data=b"[PERSON_1]", keys=b"k2"),
    ]
    assert _key(settings, op="unmask_file", data=b"docx", keys=b"k1", name="x.docx") != _key(
        settings, op="unmask_file", data=b"docx", keys=b"k2", name="x.docx"
    )
    assert _key(settings, op="unmask_text", data=b"[PERSON_1]", keys=b"k1") != different[1]
    assert len({_key(settings, op="unmask_text", data=b"[PERSON_1]", keys=b"k1"), different[1]}) == 2


def test_changed_output_filename_changes_key(settings: Settings) -> None:
    assert _key(settings, op="mask_file", data=b"docx", name="a.docx") != _key(
        settings, op="mask_file", data=b"docx", name="b.docx"
    )


def test_exception_digest_changes_key(settings: Settings) -> None:
    changed = Settings.model_validate({**settings.model_dump(), "masking_config_digest": "new"})
    assert _key(settings, op="mask_text", data=b"x") != _key(changed, op="mask_text", data=b"x")


def test_ruleset_version_bump_invalidates(settings: Settings) -> None:
    bumped = Settings.model_validate({**settings.model_dump(), "masking_ruleset_version": 2})
    assert _key(settings, op="mask_text", data=b"x") != _key(bumped, op="mask_text", data=b"x")


def test_manifest_and_namespace_versions_invalidate(settings: Settings) -> None:
    manifest = Settings.model_validate({**settings.model_dump(), "result_manifest_version": 2})
    namespace = Settings.model_validate({**settings.model_dump(), "cache_namespace_version": 2})
    assert _key(settings, op="mask_text", data=b"x") != _key(manifest, op="mask_text", data=b"x")
    assert _key(settings, op="mask_text", data=b"x") != _key(namespace, op="mask_text", data=b"x")


def test_key_does_not_leak_source_hash(settings: Settings) -> None:
    key = _key(settings, op="mask_text", data="Иван Петров".encode())
    assert sha256_hex("Иван Петров".encode()) not in key
    assert "Иван" not in key
    assert key.startswith("docmasker:result:v1:mask_text:")


def test_canonical_json_is_stable() -> None:
    assert canonical_json({"b": 1, "a": [2, 3]}) == canonical_json({"a": [2, 3], "b": 1})


# --- §4.4 / §4.5 artifacts and outage ---------------------------------------


def test_artifact_store_verifies_integrity(tmp_path: Path) -> None:
    store = CacheArtifactStore(tmp_path)
    entry = store.publish(b"result-zip")
    assert store.exists(entry)
    assert store.read(entry) == b"result-zip"
    store.delete(entry.artifact_id)
    assert store.read(entry) is None  # missing artifact -> cache miss


def test_corrupt_artifact_is_a_miss(tmp_path: Path) -> None:
    store = CacheArtifactStore(tmp_path)
    entry = store.publish(b"result-zip")
    store._path(entry.artifact_id).write_bytes(b"tampered")
    assert store.read(entry) is None


def test_valkey_outage_degrades_to_misses(tmp_path: Path, settings: Settings) -> None:
    cache = ResultCache(settings, CacheArtifactStore(tmp_path))
    assert cache.get("any-key") is None
    assert cache.put("any-key", b"payload") is None
    assert cache.metrics["misses"] >= 1
    assert cache.metrics["failures"] >= 1


class _FakeValkey:
    """In-memory stand-in for the Valkey client (deterministic, no server)."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value

    def delete(self, key: str) -> None:
        self.data.pop(key, None)


def test_hit_miss_write_and_eviction(tmp_path: Path, settings: Settings) -> None:
    store = CacheArtifactStore(tmp_path)
    cache = ResultCache(settings, store)
    cache._client = _FakeValkey()
    key = _key(settings, op="mask_text", data=b"x")
    assert cache.get(key) is None  # miss
    assert cache.put(key, b"payload") is not None
    assert cache.get(key) == b"payload"  # hit
    assert cache.metrics["hits"] == 1
    # Corrupt the referenced artifact -> eviction, not a failed request.
    entry_json = cache._client.data[key]

    artifact_id = json.loads(entry_json)["artifact_id"]
    store.delete(artifact_id)
    assert cache.get(key) is None
    assert cache.metrics["evictions"] == 1


def test_path_based_put_and_get_stream_large_results(tmp_path: Path, settings: Settings) -> None:
    cache = ResultCache(settings, CacheArtifactStore(tmp_path))
    cache._client = _FakeValkey()
    source = tmp_path / "result.zip"
    payload = b"large-result" * 4096
    source.write_bytes(payload)
    target = tmp_path / "downloaded.zip"

    assert cache.put_from_path("key", source) is not None
    assert cache.get_to_path("key", target) is True
    assert target.read_bytes() == payload
    source.unlink()
    assert cache.get_to_path("key", target) is True  # cached artifact is independent
    assert target.read_bytes() == payload


def test_disabled_cache_never_writes(tmp_path: Path) -> None:
    settings = Settings.model_validate(
        {
            "database_url": "postgresql+psycopg://dm:dm@localhost:5432/dm",
            "jobs_dir": str(tmp_path),
            "cache_enabled": False,
        }
    )
    cache = ResultCache(settings, CacheArtifactStore(tmp_path))
    assert cache.put("k", b"v") is None
    assert cache.get("k") is None


# --- §4.6 real-Valkey integration (skipped when no server is configured) -----


@pytest.fixture
def live_cache(tmp_path: Path) -> ResultCache:
    import os

    url = os.environ.get("TEST_VALKEY_URL")
    if not url:
        pytest.skip("TEST_VALKEY_URL is not set")
    settings = Settings.model_validate(
        {
            "database_url": "postgresql+psycopg://dm:dm@localhost:5432/dm",
            "jobs_dir": str(tmp_path),
            "cache_enabled": True,
            "cache_key_secret": "integration-test-secret-32chars",
            "valkey_url": url,
            "cache_connect_timeout_seconds": 1,
            "cache_socket_timeout_seconds": 1,
            "cache_ttl_seconds": 60,
        }
    )
    cache = ResultCache(settings, CacheArtifactStore(tmp_path))
    if cache._client is None:
        pytest.skip("Valkey is not reachable")
    return cache


def test_real_valkey_hit_miss_and_version_invalidation(live_cache: ResultCache) -> None:
    cfg = live_cache._settings
    key = _key(cfg, op="mask_text", data=b"cached-input")
    live_cache.invalidate(key)
    assert live_cache.get(key) is None
    assert live_cache.put(key, b"cached-result") is not None
    assert live_cache.get(key) == b"cached-result"
    # A different fingerprint namespace must not collide.
    bumped = Settings.model_validate({**cfg.model_dump(), "masking_ruleset_version": 99})
    assert live_cache.get(_key(bumped, op="mask_text", data=b"cached-input")) is None


def test_real_valkey_corrupt_entry_evicts(live_cache: ResultCache) -> None:

    cfg = live_cache._settings
    key = _key(cfg, op="mask_text", data=b"corrupt-entry")
    live_cache.invalidate(key)
    live_cache.put(key, b"payload")
    client = live_cache._client
    assert client is not None
    client.set(key, json.dumps({"broken": True}))
    assert live_cache.get(key) is None
    assert live_cache.metrics["evictions"] >= 1


# --- P1.3: bounded cache-artifact lifecycle ----------------------------------


def _cache_with_store(tmp_path: Path, settings: Settings) -> tuple[ResultCache, CacheArtifactStore]:
    store = CacheArtifactStore(tmp_path)
    cache = ResultCache(settings, store)
    cache._client = _FakeValkey()
    return cache, store


def _age(path: Path, seconds: float) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_expired_key_releases_its_artifact(tmp_path: Path, settings: Settings) -> None:
    cache, store = _cache_with_store(tmp_path, settings)
    key = "cached-result"
    entry = cache.put(key, b"payload")
    assert entry is not None
    _age(store._path(entry.artifact_id), 100)
    report = cache.gc(retention_seconds=10)
    assert report.deleted == 1
    assert report.reclaimed_bytes == len(b"payload")
    assert not store.is_present(entry.artifact_id)
    # A stale Valkey reference becomes a miss, never an error.
    assert cache.get(key) is None


def test_lru_evicted_key_does_not_leak_files_forever(tmp_path: Path, settings: Settings) -> None:
    cache, store = _cache_with_store(tmp_path, settings)
    entry = cache.put("evicted-key", b"payload")
    assert entry is not None
    cache._client.delete("evicted-key")  # LRU-style removal, key is gone
    _age(store._path(entry.artifact_id), 100)
    assert cache.gc(retention_seconds=10).deleted == 1


def test_invalid_artifact_reference_is_evicted(tmp_path: Path, settings: Settings) -> None:
    cache, _store = _cache_with_store(tmp_path, settings)
    cache._client.set(
        "key",
        json.dumps(
            {
                "artifact_id": "../outside-spool",
                "content_hash": "0" * 64,
                "size": 1,
                "content_type": "application/zip",
                "manifest_version": 1,
                "entry_version": 1,
            }
        ),
    )
    assert cache.get("key") is None
    assert cache.metrics["evictions"] >= 1


def test_shared_hash_is_not_deleted_when_one_key_is_invalidated(tmp_path: Path, settings: Settings) -> None:
    cache, _store = _cache_with_store(tmp_path, settings)
    first = cache.put("key-a", b"shared")
    second = cache.put("key-b", b"shared")
    assert first is not None
    assert second is not None
    assert first.artifact_id == second.artifact_id
    cache.invalidate("key-a")
    assert cache.gc(retention_seconds=3600).deleted == 0
    assert cache.get("key-b") == b"shared"


def test_republishing_an_old_hash_refreshes_retention(tmp_path: Path, settings: Settings) -> None:
    cache, store = _cache_with_store(tmp_path, settings)
    entry = cache.put("key-old", b"same")
    assert entry is not None
    path = store._path(entry.artifact_id)
    _age(path, 100)
    refreshed = cache.put("key-new", b"same")
    assert refreshed is not None
    assert time.time() - path.stat().st_mtime < 5
    assert cache.gc(retention_seconds=10).deleted == 0
    assert cache.get("key-new") == b"same"


def test_gc_dry_run_reports_without_deleting(tmp_path: Path, settings: Settings) -> None:
    cache, store = _cache_with_store(tmp_path, settings)
    entry = cache.put("key", b"payload")
    assert entry is not None
    _age(store._path(entry.artifact_id), 100)
    selected = store.select_gc_candidates(10)
    assert [artifact for artifact, _size, _mtime in selected] == [entry.artifact_id]
    dry = cache.gc(retention_seconds=10, dry_run=True)
    assert dry.candidates == 1
    assert dry.deleted == 0
    assert store.is_present(entry.artifact_id)


def test_concurrent_get_put_and_gc_never_serve_wrong_bytes(tmp_path: Path, settings: Settings) -> None:
    cache, _store = _cache_with_store(tmp_path, settings)
    cache.put("key", b"result")
    errors: list[str] = []

    def reader() -> None:
        for _ in range(20):
            value = cache.get("key")
            if value is not None and value != b"result":
                errors.append("wrong-result")

    def collector() -> None:
        for _ in range(20):
            # Large retention: never delete a live artifact during this test.
            cache.gc(retention_seconds=3600)

    threads = [threading.Thread(target=reader) for _ in range(3)] + [threading.Thread(target=collector)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors


def test_outage_and_recent_artifacts_are_not_aggressively_collected(tmp_path: Path) -> None:
    settings = Settings.model_validate(
        {
            "database_url": "postgresql+psycopg://dm:dm@localhost:5432/dm",
            "jobs_dir": str(tmp_path),
            "cache_enabled": False,
        }
    )
    store = CacheArtifactStore(tmp_path)
    entry = store.publish(b"payload")  # no Valkey client at all
    report = ResultCache(settings, store).gc(retention_seconds=10)
    assert report.deleted == 0
    assert store.read(entry) == b"payload"


def test_gc_metrics_are_safe_aggregates(tmp_path: Path, settings: Settings) -> None:
    cache, store = _cache_with_store(tmp_path, settings)
    entry = cache.put("key", b"payload")
    assert entry is not None
    _age(store._path(entry.artifact_id), 100)
    cache.gc(retention_seconds=10)
    stats = cache_gc_stats()
    assert stats["candidates"] >= 1
    assert stats["deleted"] >= 1
    assert stats["reclaimed_bytes"] >= len(b"payload")
    assert all(isinstance(value, int) for value in stats.values())
