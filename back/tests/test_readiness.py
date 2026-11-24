"""Phase 6 §10: readiness/metrics endpoints (no PII, DB outage degrades)."""

import pytest
from httpx2 import ASGITransport, AsyncClient

import src.main as main_module
from src.main import app


async def test_ready_when_postgresql_is_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Repo:
        def ping(self) -> None:
            return None

    monkeypatch.setattr(main_module, "job_repository", _Repo)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["labels"] > 0


async def test_ready_returns_503_without_leaking_db_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Repo:
        def ping(self) -> None:
            msg = "host=/var/run/postgresql user=dm password=secret Иван Петров"
            raise RuntimeError(msg)

    monkeypatch.setattr(main_module, "job_repository", _Repo)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body == {"status": "not_ready", "reason": "Хранилище заданий недоступно."}
    assert "password" not in response.text
    assert "Иван" not in response.text


async def test_metrics_are_coarse_and_db_outage_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Repo:
        def count_active(self) -> int:
            msg = "db down"
            raise RuntimeError(msg)

    class _Cache:
        @property
        def metrics(self) -> dict[str, int]:
            return {"hits": 3, "misses": 1, "writes": 1, "evictions": 0}

    monkeypatch.setattr(main_module, "job_repository", _Repo)
    monkeypatch.setattr(main_module, "result_cache", _Cache)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/metrics")
    assert response.status_code == 200
    body = response.json()
    assert body["active_jobs"] is None  # degraded, not a 500
    assert body["cache"]["hits"] == 3
    # No job ids / cache keys / PII.
    assert "job_id" not in response.text
