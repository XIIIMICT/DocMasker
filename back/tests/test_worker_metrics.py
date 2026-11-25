"""The worker's dedicated stdlib scrape listener is isolated and non-fatal."""

import threading
import urllib.error
import urllib.request

import pytest

from src.jobs import metrics as worker_metrics
from src.metrics import mark_worker_heartbeat, reset_metrics


@pytest.fixture(autouse=True)
def _clean_metrics() -> None:
    reset_metrics()


def test_metrics_listener_serves_prometheus_text_on_its_own_thread() -> None:
    mark_worker_heartbeat()
    server = worker_metrics.start_metrics_server(host="127.0.0.1", port=0)
    assert server is not None
    try:
        port = server.server_address[1]
        assert any(thread.name == "dm-metrics" and thread.daemon for thread in threading.enumerate())
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as response:
            body = response.read().decode()
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("text/plain")
        assert "# HELP dm_worker_heartbeat_timestamp_seconds" in body
        assert "dm_worker_heartbeat_timestamp_seconds " in body

        with pytest.raises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/jobs/secret?key=value", timeout=5)
        assert raised.value.code == 404
    finally:
        worker_metrics.stop_metrics_server(server)


def test_metrics_listener_bind_failure_is_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    def _busy(*_args: object, **_kwargs: object) -> None:
        msg = "port is busy"
        raise OSError(msg)

    monkeypatch.setattr(worker_metrics, "ThreadingHTTPServer", _busy)
    assert worker_metrics.start_metrics_server(host="127.0.0.1", port=8002) is None
