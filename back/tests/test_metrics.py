"""Metrics contract: exact names/labels, bounded labels and safe exposure."""

import pytest
from fastapi import FastAPI
from httpx2 import ASGITransport, AsyncClient

import src.main as main_module
from src.errors import install_error_handlers
from src.metrics import (
    generate_metrics,
    http_request_finished,
    http_request_started,
    inc_lease_expired,
    inc_lease_renew_failure,
    mark_http_failure,
    mark_worker_heartbeat,
    observe_cache_operation,
    observe_dependency_check,
    observe_event_loop_lag,
    observe_http_request,
    observe_job_created,
    observe_job_duration,
    observe_job_failed,
    observe_job_queue_age,
    observe_job_retried,
    observe_job_succeeded,
    observe_upload,
    reset_metrics,
    safe_error_code,
    safe_kind,
    safe_route,
    set_artifact_disk_free,
    set_db_pool_metrics,
    set_disk_free,
    set_job_running,
    set_jobs_active,
)


@pytest.fixture(autouse=True)
def _clean_metrics() -> None:
    reset_metrics()


def _fake_route(path: str) -> object:
    return type("Route", (), {"path": path})()


def test_every_required_metric_family_is_exposed() -> None:
    observe_cache_operation(operation=" miss ", outcome="error")  # unknown values collapse, never raise
    observe_cache_operation(operation="failure", outcome="error")
    observe_cache_operation(operation="eviction", outcome="ok")
    observe_cache_operation(operation="write", outcome="ok")
    observe_upload(outcome="error")
    set_disk_free(storage="jobs", free_bytes=512)
    observe_job_succeeded("mask_file")
    observe_job_retried("unmask_text")
    set_job_running(kind="mask_text", running=True)
    observe_job_duration("mask_text", 1.0)
    inc_lease_renew_failure()
    inc_lease_expired()
    set_artifact_disk_free(100)
    mark_worker_heartbeat()

    body = generate_metrics().decode()
    families = {line.split()[2] for line in body.splitlines() if line.startswith("# HELP ")}
    expected = {
        "dm_http_requests_total",
        "dm_http_request_duration_seconds",
        "dm_http_last_failure_timestamp_seconds",
        "dm_http_requests_in_progress",
        "dm_dependency_check_duration_seconds",
        "dm_db_connections_in_use",
        "dm_db_pool_capacity",
        "dm_runtime_event_loop_lag_seconds",
        "dm_runtime_disk_free_bytes",
        "dm_cache_operations_total",
        "dm_uploads_total",
        "dm_jobs_created_total",
        "dm_jobs_succeeded_total",
        "dm_jobs_failed_total",
        "dm_jobs_retried_total",
        "dm_jobs_active",
        "dm_jobs_running",
        "dm_job_duration_seconds",
        "dm_job_queue_age_seconds",
        "dm_lease_renew_failures_total",
        "dm_lease_expired_total",
        "dm_artifact_disk_free_bytes",
        "dm_worker_heartbeat_timestamp_seconds",
    }
    assert expected <= families
    assert not {name for name in families if not name.startswith("dm_")}


def test_exposition_exposes_the_exact_contract() -> None:
    observe_http_request(method="GET", route=_fake_route("/api/jobs/{job_id}"), status=200, duration_seconds=0.01)
    http_request_started()
    http_request_started()
    http_request_finished()
    mark_http_failure()
    observe_dependency_check(dependency="postgres", outcome="ok", duration_seconds=0.02)
    set_db_pool_metrics(in_use=2, capacity=10)
    set_disk_free(storage="logs", free_bytes=1024)
    observe_event_loop_lag(0.003)
    observe_cache_operation(operation="hit", outcome="ok")
    observe_upload(outcome="ok")
    observe_job_created("mask_text")
    observe_job_failed(kind="mask_text", error_code="invalid_keys")
    set_jobs_active({"mask_file": 3})
    observe_job_queue_age("mask_text", 1.5)

    body = generate_metrics().decode()
    for expected in (
        'dm_http_requests_total{method="GET",route="/api/jobs/{job_id}",status="200"} 1.0',
        'dm_http_request_duration_seconds_count{method="GET",route="/api/jobs/{job_id}"} 1',
        "dm_http_last_failure_timestamp_seconds ",
        "dm_http_requests_in_progress 1.0",
        'dm_dependency_check_duration_seconds_count{dependency="postgres",outcome="ok"} 1',
        "dm_db_connections_in_use 2.0",
        "dm_db_pool_capacity 10.0",
        "dm_runtime_event_loop_lag_seconds_count 1",
        'dm_runtime_disk_free_bytes{storage="logs"} 1024.0',
        'dm_cache_operations_total{operation="hit",outcome="ok"} 1.0',
        'dm_uploads_total{outcome="ok"} 1.0',
        'dm_jobs_created_total{kind="mask_text"} 1.0',
        'dm_jobs_failed_total{error_code="invalid_keys",kind="mask_text"} 1.0',
        'dm_jobs_active{kind="mask_file"} 3.0',
        'dm_job_queue_age_seconds_count{kind="mask_text"} 1',
    ):
        assert expected in body, expected
    assert "/api/jobs/abc" not in body


def test_bounded_label_helpers_collapse_unknown_values() -> None:
    assert safe_kind("../secrets") == "unmatched"
    assert safe_error_code("password=secret") == "internal_error"
    assert safe_error_code("artifact_io") == "artifact_io"
    assert safe_route(None) == "unmatched"
    assert safe_route(_fake_route("/api/jobs/{job_id}")) == "/api/jobs/{job_id}"

    observe_http_request(method="TRACE", route=_fake_route("/api/jobs/{job_id}"), status=200, duration_seconds=0.1)
    body = generate_metrics().decode()
    assert 'method="OTHER",route="/api/jobs/{job_id}"' in body  # bounded method, template route


def test_metrics_emission_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BrokenCounter:
        def labels(self, **_kwargs: object) -> object:
            msg = "registry is down"
            raise RuntimeError(msg)

    import src.metrics as metrics_module

    monkeypatch.setattr(metrics_module, "HTTP_REQUESTS_TOTAL", _BrokenCounter())
    # No assertion on logs: logging is best-effort too; the contract is no raise.
    observe_http_request(method="GET", route=None, status=500, duration_seconds=0.0)
    mark_http_failure()


async def test_backend_metrics_endpoint_negotiates_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Repo:
        def count_active(self) -> int:
            return 2

    class _Cache:
        @property
        def metrics(self) -> dict[str, int]:
            return {"hits": 1, "misses": 0, "writes": 0, "evictions": 0}

    monkeypatch.setattr(main_module, "job_repository", _Repo)
    monkeypatch.setattr(main_module, "result_cache", _Cache)
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        prometheus = await client.get("/metrics", headers={"Accept": "text/plain"})
        legacy = await client.get("/metrics")

    assert prometheus.status_code == 200
    assert prometheus.headers["content-type"].startswith("text/plain")
    assert "# HELP dm_http_requests_total" in prometheus.text
    assert legacy.status_code == 200
    assert legacy.json() == {"active_jobs": 2, "cache": {"hits": 1, "misses": 0, "writes": 0, "evictions": 0}}


async def test_http_middleware_records_the_route_template_only() -> None:
    probe = FastAPI()
    install_error_handlers(probe)

    @probe.get("/api/jobs/{job_id}")
    async def status(job_id: str) -> dict[str, str]:
        return {"job_id": job_id}

    transport = ASGITransport(app=probe)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/jobs/ivan-petrov@example.ru")

    assert response.status_code == 200
    assert response.headers["x-request-id"]
    body = generate_metrics().decode()
    assert 'route="/api/jobs/{job_id}"' in body
    assert "ivan-petrov@example.ru" not in body


async def test_ready_emits_dependency_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Repo:
        def ping(self) -> None:
            return None

    class _Cache:
        def ping(self) -> bool:
            return True

    monkeypatch.setattr(main_module, "job_repository", _Repo)
    monkeypatch.setattr(main_module, "result_cache", _Cache)
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")

    assert response.status_code == 200
    body = generate_metrics().decode()
    assert 'dm_dependency_check_duration_seconds_count{dependency="postgres",outcome="ok"} 1' in body
    assert 'dm_dependency_check_duration_seconds_count{dependency="valkey",outcome="ok"} 1' in body
