"""
Bounded CPU offload for the synchronous API (ADR-0002 / F-01).

`natasha`, `phonenumbers`, `python-docx` and ZIP work are blocking CPU/bound
I/O and must never run inside the ASGI event loop. A bounded semaphore caps
how many such operations are in flight; over the cap the caller gets a clean
429 instead of an unbounded pile-up. The worker process does not use this
semaphore — it has its own bounded thread pool.
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


def _ensure(settings: Settings, loop: asyncio.AbstractEventLoop) -> tuple[ThreadPoolExecutor, asyncio.Semaphore]:
    """Create the executor once per process and a semaphore per event loop."""
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


def reset_executor() -> None:
    """Drop the cached executor (tests / shutdown)."""
    global _EXECUTOR, _LIMIT, _CONFIG  # noqa: PLW0603
    if _EXECUTOR is not None:
        _EXECUTOR.shutdown(wait=False, cancel_futures=True)
    _EXECUTOR = None
    _LIMIT = 0
    _CONFIG = None
    _SEMAPHORES.clear()


async def run_cpu[T](fn: Callable[..., T], *args: object, **kwargs: object) -> T:
    """Run a blocking callable off the event loop, bounded by a semaphore."""
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
