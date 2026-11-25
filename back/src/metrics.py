"""
Prometheus metrics for the backend and the worker (monitoring unification).

Logs answer "what happened"; metrics answer "how many and how often". Percentiles
come from histogram buckets recorded at write time, never from log lines.

Design notes:

* A private `CollectorRegistry` is used instead of the process-global default,
  so importing this module (in tests, in the worker, in the API) has no side
  effects beyond creating the collectors once.
* HTTP labels use the ASGI ROUTE TEMPLATE (`/api/jobs/{job_id}`), never the
  concrete path: an id or a filename in a label would create unbounded series
  and could leak sensitive data. Unmatched routes collapse to `unmatched`.
* `dm_jobs_active`/`dm_db_connections_in_use` are gauges: a scrape reads the
  value left by the last update, which is the point of a gauge.
* Every emission goes through `_emit`: a metrics outage is logged by exception
  type and never breaks a request, a job or a heartbeat.

No user data, filenames, keys, entity values, SQL text or raw exception text
ever reaches a metric name, label or help string.
"""

import logging
from collections.abc import Callable, Mapping
from contextlib import suppress

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

LOGGER = logging.getLogger("docmasker.metrics")

_LATENCY_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
)
_LONG_BUCKETS: tuple[float, ...] = (*_LATENCY_BUCKETS, 60.0, 120.0, 300.0, 900.0, 3600.0)

# Bounded vocabularies. Anything unexpected is collapsed before it reaches a
# label, so a corrupted DB value or an exotic HTTP method cannot blow up
# cardinality or carry sensitive text into the exposition.
_JOB_KINDS = frozenset({"mask_text", "mask_file", "unmask_text", "unmask_file"})
_JOB_ERROR_CODES = frozenset(
    {
        "invalid_input",
        "invalid_keys",
        "invalid_package",
        "unsupported_operation",
        "unknown_kind",
        "invalid_configuration",
        "cancelled",
        "queue_timeout",
        "dependency_unavailable",
        "artifact_io",
        "worker_lost",
        "internal_error",
        "attempts_exhausted",
        "staging_failed",
    }
)
_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_STORAGES = frozenset({"logs", "jobs"})
_CACHE_OPERATIONS = frozenset({"hit", "miss", "write", "failure", "eviction"})
_UPLOAD_OUTCOMES = frozenset({"ok", "error"})

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS_TOTAL = Counter(
    "dm_http_requests_total",
    "HTTP requests handled, by method, route template and status code.",
    labelnames=("method", "route", "status"),
    registry=REGISTRY,
)
HTTP_REQUEST_DURATION = Histogram(
    "dm_http_request_duration_seconds",
    "HTTP request latency by route template.",
    labelnames=("method", "route"),
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)
HTTP_LAST_FAILURE = Gauge(
    "dm_http_last_failure_timestamp_seconds",
    "Time of the last server error or interrupted response, zero before any failure.",
    registry=REGISTRY,
)
HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "dm_http_requests_in_progress",
    "HTTP requests currently being handled.",
    registry=REGISTRY,
)

DEPENDENCY_DURATION = Histogram(
    "dm_dependency_check_duration_seconds",
    "Readiness dependency check duration.",
    labelnames=("dependency", "outcome"),
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

DB_CONNECTIONS_IN_USE = Gauge(
    "dm_db_connections_in_use",
    "Database connections currently checked out by this process.",
    registry=REGISTRY,
)
DB_POOL_CAPACITY = Gauge(
    "dm_db_pool_capacity",
    "Configured database pool size plus overflow for this process.",
    registry=REGISTRY,
)

RUNTIME_EVENT_LOOP_LAG = Histogram(
    "dm_runtime_event_loop_lag_seconds",
    "Event-loop scheduling delay measured by the runtime sampler.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
    registry=REGISTRY,
)
RUNTIME_DISK_FREE_BYTES = Gauge(
    "dm_runtime_disk_free_bytes",
    "Free bytes on application filesystems.",
    labelnames=("storage",),
    registry=REGISTRY,
)

CACHE_OPERATIONS_TOTAL = Counter(
    "dm_cache_operations_total",
    "Result-cache operations by bounded operation and outcome.",
    labelnames=("operation", "outcome"),
    registry=REGISTRY,
)
UPLOADS_TOTAL = Counter(
    "dm_uploads_total",
    "Upload staging attempts by outcome.",
    labelnames=("outcome",),
    registry=REGISTRY,
)

JOBS_CREATED_TOTAL = Counter(
    "dm_jobs_created_total",
    "Durable jobs created, by kind.",
    labelnames=("kind",),
    registry=REGISTRY,
)
JOBS_SUCCEEDED_TOTAL = Counter(
    "dm_jobs_succeeded_total",
    "Durable jobs completed successfully, by kind.",
    labelnames=("kind",),
    registry=REGISTRY,
)
JOBS_FAILED_TOTAL = Counter(
    "dm_jobs_failed_total",
    "Durable jobs that reached a terminal failure, by kind and safe error code.",
    labelnames=("kind", "error_code"),
    registry=REGISTRY,
)
JOBS_RETRIED_TOTAL = Counter(
    "dm_jobs_retried_total",
    "Durable jobs scheduled for another attempt, by kind.",
    labelnames=("kind",),
    registry=REGISTRY,
)
JOBS_ACTIVE = Gauge(
    "dm_jobs_active",
    "Non-terminal jobs by kind (queue depth).",
    labelnames=("kind",),
    registry=REGISTRY,
)
JOBS_RUNNING = Gauge(
    "dm_jobs_running",
    "Jobs currently executing in this worker, by kind.",
    labelnames=("kind",),
    registry=REGISTRY,
)
JOB_DURATION = Histogram(
    "dm_job_duration_seconds",
    "End-to-end execution duration of a claimed job.",
    labelnames=("kind",),
    buckets=_LONG_BUCKETS,
    registry=REGISTRY,
)
JOB_QUEUE_AGE = Histogram(
    "dm_job_queue_age_seconds",
    "Time a job spent in the queue before its worker claimed it.",
    labelnames=("kind",),
    buckets=_LONG_BUCKETS,
    registry=REGISTRY,
)
LEASE_RENEW_FAILURES_TOTAL = Counter(
    "dm_lease_renew_failures_total",
    "Job lease renewal attempts that failed or were rejected.",
    registry=REGISTRY,
)
LEASE_EXPIRED_TOTAL = Counter(
    "dm_lease_expired_total",
    "Job leases that expired or were reclaimed by another worker.",
    registry=REGISTRY,
)
ARTIFACT_DISK_FREE_BYTES = Gauge(
    "dm_artifact_disk_free_bytes",
    "Free bytes on the private jobs/artifacts volume.",
    registry=REGISTRY,
)
WORKER_HEARTBEAT_TIMESTAMP = Gauge(
    "dm_worker_heartbeat_timestamp_seconds",
    "Unix time of the last successful worker heartbeat.",
    registry=REGISTRY,
)

_ALL_COLLECTORS = (
    HTTP_REQUESTS_TOTAL,
    HTTP_REQUEST_DURATION,
    HTTP_LAST_FAILURE,
    HTTP_REQUESTS_IN_PROGRESS,
    DEPENDENCY_DURATION,
    DB_CONNECTIONS_IN_USE,
    DB_POOL_CAPACITY,
    RUNTIME_EVENT_LOOP_LAG,
    RUNTIME_DISK_FREE_BYTES,
    CACHE_OPERATIONS_TOTAL,
    UPLOADS_TOTAL,
    JOBS_CREATED_TOTAL,
    JOBS_SUCCEEDED_TOTAL,
    JOBS_FAILED_TOTAL,
    JOBS_RETRIED_TOTAL,
    JOBS_ACTIVE,
    JOBS_RUNNING,
    JOB_DURATION,
    JOB_QUEUE_AGE,
    LEASE_RENEW_FAILURES_TOTAL,
    LEASE_EXPIRED_TOTAL,
    ARTIFACT_DISK_FREE_BYTES,
    WORKER_HEARTBEAT_TIMESTAMP,
)


def _emit(action: Callable[[], None]) -> None:
    """Run one metric update; a failure never propagates to the caller."""
    try:
        action()
    except Exception as error:  # noqa: BLE001 - a metrics failure must never break the app
        with suppress(Exception):
            LOGGER.warning("metrics emission failed (error_type=%s)", type(error).__name__)


def get_registry() -> CollectorRegistry:
    """Return the private registry (for the ASGI and worker scrape endpoints)."""
    return REGISTRY


def generate_metrics() -> bytes:
    """Render the private registry in Prometheus text exposition format."""
    return generate_latest(REGISTRY)


def reset_metrics() -> None:
    """Clear all collectors (tests that assert on values call this)."""
    for collector in _ALL_COLLECTORS:
        _emit(collector.clear)


def safe_kind(kind: object) -> str:
    """Keep only the four canonical job kinds."""
    return kind if isinstance(kind, str) and kind in _JOB_KINDS else "unmatched"


def safe_error_code(code: object) -> str:
    """Keep only stable, bounded worker error codes."""
    return code if isinstance(code, str) and code in _JOB_ERROR_CODES else "internal_error"


def safe_route(route: object) -> str:
    """Read a route TEMPLATE (string or ASGI route object), never a client path."""
    if isinstance(route, str):
        return route or "unmatched"
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path else "unmatched"


def _safe_label(value: object, allowed: frozenset[str], fallback: str) -> str:
    return value if isinstance(value, str) and value in allowed else fallback


def observe_http_request(*, method: object, route: object, status: int, duration_seconds: float) -> None:
    """Record one finished request against its route template."""
    safe_method = _safe_label(method, _HTTP_METHODS, "OTHER")
    safe_route_name = safe_route(route)
    safe_status = status if isinstance(status, int) and 100 <= status <= 599 else 500
    _emit(lambda: HTTP_REQUESTS_TOTAL.labels(method=safe_method, route=safe_route_name, status=str(safe_status)).inc())
    _emit(
        lambda: HTTP_REQUEST_DURATION.labels(method=safe_method, route=safe_route_name).observe(
            max(0.0, duration_seconds)
        )
    )


def http_request_started() -> None:
    _emit(HTTP_REQUESTS_IN_PROGRESS.inc)


def http_request_finished() -> None:
    _emit(HTTP_REQUESTS_IN_PROGRESS.dec)


def mark_http_failure() -> None:
    _emit(HTTP_LAST_FAILURE.set_to_current_time)


def observe_dependency_check(*, dependency: str, outcome: str, duration_seconds: float) -> None:
    """Record a readiness check (dependency is postgres or valkey)."""
    name = _safe_label(dependency, frozenset({"postgres", "valkey"}), "postgres")
    result = _safe_label(outcome, frozenset({"ok", "error"}), "error")
    _emit(lambda: DEPENDENCY_DURATION.labels(dependency=name, outcome=result).observe(max(0.0, duration_seconds)))


def set_db_pool_metrics(*, in_use: int, capacity: int) -> None:
    _emit(lambda: DB_CONNECTIONS_IN_USE.set(max(0, in_use)))
    _emit(lambda: DB_POOL_CAPACITY.set(max(0, capacity)))


def observe_event_loop_lag(seconds: float) -> None:
    _emit(lambda: RUNTIME_EVENT_LOOP_LAG.observe(max(0.0, seconds)))


def set_disk_free(*, storage: str, free_bytes: float) -> None:
    name = _safe_label(storage, _STORAGES, "jobs")
    _emit(lambda: RUNTIME_DISK_FREE_BYTES.labels(storage=name).set(free_bytes))


def observe_cache_operation(*, operation: str, outcome: str) -> None:
    name = _safe_label(operation, _CACHE_OPERATIONS, "failure")
    result = _safe_label(outcome, frozenset({"ok", "error"}), "error")
    _emit(lambda: CACHE_OPERATIONS_TOTAL.labels(operation=name, outcome=result).inc())


def observe_upload(*, outcome: str) -> None:
    result = _safe_label(outcome, _UPLOAD_OUTCOMES, "error")
    _emit(lambda: UPLOADS_TOTAL.labels(outcome=result).inc())


def observe_job_created(kind: object) -> None:
    _emit(lambda: JOBS_CREATED_TOTAL.labels(kind=safe_kind(kind)).inc())


def observe_job_succeeded(kind: object) -> None:
    _emit(lambda: JOBS_SUCCEEDED_TOTAL.labels(kind=safe_kind(kind)).inc())


def observe_job_failed(*, kind: object, error_code: object) -> None:
    _emit(lambda: JOBS_FAILED_TOTAL.labels(kind=safe_kind(kind), error_code=safe_error_code(error_code)).inc())


def observe_job_retried(kind: object) -> None:
    _emit(lambda: JOBS_RETRIED_TOTAL.labels(kind=safe_kind(kind)).inc())


def set_jobs_active(counts: Mapping[str, int]) -> None:
    """Set per-kind queue depth; clear stale kinds so a gauge never drifts."""
    _emit(JOBS_ACTIVE.clear)
    for kind, count in counts.items():
        name = safe_kind(kind)
        _emit(lambda name=name, count=count: JOBS_ACTIVE.labels(kind=name).set(max(0, count)))


def set_job_running(*, kind: object, running: bool) -> None:
    name = safe_kind(kind)
    _emit(lambda: JOBS_RUNNING.labels(kind=name).inc() if running else JOBS_RUNNING.labels(kind=name).dec())


def observe_job_duration(kind: object, duration_seconds: float) -> None:
    _emit(lambda: JOB_DURATION.labels(kind=safe_kind(kind)).observe(max(0.0, duration_seconds)))


def observe_job_queue_age(kind: object, age_seconds: float) -> None:
    _emit(lambda: JOB_QUEUE_AGE.labels(kind=safe_kind(kind)).observe(max(0.0, age_seconds)))


def inc_lease_renew_failure() -> None:
    _emit(LEASE_RENEW_FAILURES_TOTAL.inc)


def inc_lease_expired() -> None:
    _emit(LEASE_EXPIRED_TOTAL.inc)


def set_artifact_disk_free(free_bytes: float) -> None:
    _emit(lambda: ARTIFACT_DISK_FREE_BYTES.set(free_bytes))


def mark_worker_heartbeat() -> None:
    _emit(WORKER_HEARTBEAT_TIMESTAMP.set_to_current_time)
