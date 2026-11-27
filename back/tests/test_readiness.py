"""Phase 6 §10: readiness probe and the Prometheus-only scrape endpoint."""

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


async def test_metrics_scrape_is_prometheus_only_and_db_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A DB outage must not affect the scrape: it no longer queries the store."""

    def _forbidden_repository() -> object:
        msg = "scrape must not query PostgreSQL"
        raise AssertionError(msg)

    monkeypatch.setattr(main_module, "job_repository", _forbidden_repository)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# HELP dm_jobs_active" in response.text
    assert "active_jobs" not in response.text  # the legacy JSON key is gone
    assert "# HELP dm_dependency_check_duration_seconds" in response.text
