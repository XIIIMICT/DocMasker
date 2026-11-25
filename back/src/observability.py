"""Periodic runtime sampling: event-loop delay and free disk on safe paths."""

import asyncio
import logging
import shutil
from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Match
from starlette.types import Scope

from .app_logging import log_warning
from .metrics import observe_event_loop_lag, set_disk_free

LOGGER = logging.getLogger("docmasker.application")


def route_template(app: Starlette, scope: Scope) -> str:
    """
    Resolve the ASGI route TEMPLATE for a request, never the concrete path.

    Starlette sets `scope["route"]` inside the router; with BaseHTTPMiddleware
    that assignment is not always visible to the outer middleware, so fall
    back to matching the registered routes. A match yields `route.path` (with
    `{param}` placeholders); unmatched requests collapse to `unmatched`.
    """
    route = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    for candidate in app.routes:
        match, _child_scope = candidate.matches(scope)
        if match in (Match.FULL, Match.PARTIAL):
            path = getattr(candidate, "path", None)
            return path if isinstance(path, str) and path else "unmatched"
    return "unmatched"


async def sample_runtime_metrics(*, log_dir: Path, jobs_dir: Path) -> None:
    """Sample loop delay every second; filesystem calls run off-loop every 15s."""
    tick = 0
    while True:
        started = asyncio.get_running_loop().time()
        await asyncio.sleep(1)
        observe_event_loop_lag(max(0.0, asyncio.get_running_loop().time() - started - 1.0))
        if tick % 15 == 0:
            # Only two fixed, non-sensitive storages are ever sampled: the log
            # directory and the private jobs spool. No path is logged.
            for storage, path in (("logs", log_dir), ("jobs", jobs_dir)):
                try:
                    usage = await asyncio.to_thread(shutil.disk_usage, path)
                except OSError as error:
                    log_warning(
                        LOGGER,
                        "disk_sample_failed",
                        "Disk sample unavailable.",
                        storage=storage,
                        error_type=type(error).__name__,
                    )
                    set_disk_free(storage=storage, free_bytes=float("nan"))
                else:
                    set_disk_free(storage=storage, free_bytes=usage.free)
        tick += 1
