"""
Tiny stdlib-only Prometheus listener for the worker process.

It runs on a daemon thread so a metrics outage (a busy port, a scrape flood)
can never delay or kill job processing. A failure to bind is logged by
exception type and the worker continues without metrics. Only the fixed
`/metrics` path is served; no request line, header or query string is logged,
so nothing from a job or an artifact can leak through the listener.
"""

import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit

from prometheus_client import CONTENT_TYPE_LATEST

from src.metrics import generate_metrics

LOGGER = logging.getLogger("docmasker.worker")

SCRAPE_PATH = "/metrics"


class _MetricsHandler(BaseHTTPRequestHandler):
    """Serve the private registry only, with no access logging."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if urlsplit(self.path).path != SCRAPE_PATH:
            self.send_error(404, "Not Found")
            return
        payload = generate_metrics()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPE_LATEST)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_HEAD(self) -> None:
        if urlsplit(self.path).path != SCRAPE_PATH:
            self.send_error(404, "Not Found")
            return
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPE_LATEST)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Suppress access logs: they would contain untrusted request data."""


def start_metrics_server(host: str = "0.0.0.0", port: int = 8002) -> ThreadingHTTPServer | None:  # noqa: S104
    """Bind the scrape listener in a daemon thread; never fatal on bind errors."""
    try:
        server = ThreadingHTTPServer((host, port), _MetricsHandler)
    except OSError as error:
        LOGGER.warning("worker metrics listener unavailable (error_type=%s, port=%d)", type(error).__name__, port)
        return None
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, name="dm-metrics", daemon=True)
    thread.start()
    LOGGER.info("worker metrics listener started (port=%d)", server.server_address[1])
    return server


def stop_metrics_server(server: ThreadingHTTPServer | None) -> None:
    """Stop the listener during a clean worker shutdown."""
    if server is None:
        return
    try:
        server.shutdown()
        server.server_close()
    except OSError as error:
        LOGGER.warning("worker metrics listener stop failed (error_type=%s)", type(error).__name__)
