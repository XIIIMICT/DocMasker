"""
Bounded offload for the synchronous API (ADR-0002, Phase 6 §10).

`natasha`, `phonenumbers`, OOXML/XML parsing, ZIP work, DB and filesystem I/O
are blocking and must never run inside the ASGI event loop. Two **separate**
bounded pools exist (§10: "one semaphore must not substitute for all resource
limits"):

* `run_cpu` — heavy detection/transformation (the bounded ThreadPoolExecutor
  below, `JOBS_CONCURRENCY * 2` slots); overflow → 429.
* `run_blocking` — short DB/Valkey/ZIP/filesystem calls on a smaller dedicated
  IO pool; this keeps a slow PostgreSQL/Valkey query from starving CPU work
  without letting DB calls execute on the event loop.

The worker process uses neither — it has its own bounded thread pool and runs
its operations in worker threads by design.
"""

import asyncio
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from src.config import Settings, get_settings

LOGGER = logging.getLogger("docmasker.executor")


class ExecutorBusyError(RuntimeError):
    """Too many synchronous CPU operations are already running (429)."""


_CONFIG: Settings | None = None
_EXECUTOR: ThreadPoolExecutor | None = None
_LIMIT = 0
_INIT_LOCK = threading.Lock()
_SEMAPHORES: dict[asyncio.AbstractEventLoop, tuple[asyncio.Semaphore, int]] = {}

# Dedicated IO pool: bounded independently of the CPU pool. Short blocking
# DB/Valkey/ZIP/filesystem calls go here, never on the event loop.
_IO_EXECUTOR: ThreadPoolExecutor | None = None
_IO_LIMIT = 0
_IO_SEMAPHORES: dict[asyncio.AbstractEventLoop, tuple[asyncio.Semaphore, int]] = {}


def _ensure(settings: Settings, loop: asyncio.AbstractEventLoop) -> tuple[ThreadPoolExecutor, asyncio.Semaphore]:
    """Create the CPU executor once per process and a semaphore per event loop."""
    global _EXECUTOR, _LIMIT, _CONFIG  # noqa: PLW0603
    limit = max(1, settings.jobs_concurrency * 2)
    with _INIT_LOCK:
        if _EXECUTOR is None or limit != _LIMIT or _CONFIG is not settings:
            if _EXECUTOR is not None:
                _EXECUTOR.shutdown(wait=False, cancel_futures=True)
            _EXECUTOR = ThreadPoolExecutor(max_workers=limit, thread_name_prefix="dm-sync")
            _LIMIT = limit
            _CONFIG = settings
        executor = _EXECUTOR
        cached = _SEMAPHORES.get(loop)
        if cached is None or cached[1] != limit:
            semaphore = asyncio.Semaphore(limit)
            _SEMAPHORES[loop] = (semaphore, limit)
        else:
            semaphore = cached[0]
        return executor, semaphore


def _ensure_io(settings: Settings, loop: asyncio.AbstractEventLoop) -> tuple[ThreadPoolExecutor, asyncio.Semaphore]:
    """Separate bounded IO pool, sized from the DB pool capacity."""
    global _IO_EXECUTOR, _IO_LIMIT  # noqa: PLW0603
    limit = max(2, settings.db_pool_size + settings.db_max_overflow)
    with _INIT_LOCK:
        if _IO_EXECUTOR is None or limit != _IO_LIMIT:
            if _IO_EXECUTOR is not None:
                _IO_EXECUTOR.shutdown(wait=False, cancel_futures=True)
            _IO_EXECUTOR = ThreadPoolExecutor(max_workers=limit, thread_name_prefix="dm-io")
            _IO_LIMIT = limit
        executor = _IO_EXECUTOR
        cached = _IO_SEMAPHORES.get(loop)
        if cached is None or cached[1] != limit:
            semaphore = asyncio.Semaphore(limit)
            _IO_SEMAPHORES[loop] = (semaphore, limit)
        else:
            semaphore = cached[0]
        return executor, semaphore


def reset_executor() -> None:
    """Drop the cached executors (tests / shutdown)."""
    global _EXECUTOR, _LIMIT, _CONFIG, _IO_EXECUTOR, _IO_LIMIT  # noqa: PLW0603
    if _EXECUTOR is not None:
        _EXECUTOR.shutdown(wait=False, cancel_futures=True)
    if _IO_EXECUTOR is not None:
        _IO_EXECUTOR.shutdown(wait=False, cancel_futures=True)
    _EXECUTOR = None
    _IO_EXECUTOR = None
    _LIMIT = 0
    _IO_LIMIT = 0
    _CONFIG = None
    _SEMAPHORES.clear()
    _IO_SEMAPHORES.clear()


async def run_cpu[T](fn: Callable[..., T], *args: object, **kwargs: object) -> T:
    """Run a heavy blocking callable off the event loop, bounded by CPU slots."""
    settings = get_settings()
    loop = asyncio.get_running_loop()
    executor, semaphore = _ensure(settings, loop)
    if semaphore.locked():
        raise ExecutorBusyError("Сервер занят обработкой запросов. Повторите позже.")
    await semaphore.acquire()
    try:
        return await loop.run_in_executor(executor, lambda: fn(*args, **kwargs))
    finally:
        semaphore.release()


async def run_blocking[T](fn: Callable[..., T], *args: object, **kwargs: object) -> T:
    """
    Run a short blocking IO call (DB/Valkey/ZIP/filesystem) off the event loop.

    Uses the dedicated IO pool and a separate semaphore so a slow DB query
    cannot consume CPU-detection slots. Exceptions propagate unchanged.
    """
    settings = get_settings()
    loop = asyncio.get_running_loop()
    executor, semaphore = _ensure_io(settings, loop)
    async with semaphore:
        return await loop.run_in_executor(executor, lambda: fn(*args, **kwargs))
