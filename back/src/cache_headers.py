"""Transport cache policy for API responses that may carry PII or artifacts."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

API_PREFIX = "/api/"

#: Applied to every `/api/*` response; per-route headers (including the ZIP
#: `Cache-Control: no-store`) are preserved, only these two keys are set.
NO_STORE_HEADERS: dict[str, str] = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def is_api_path(path: str) -> bool:
    """Whether an HTTP path belongs to the API contract (not probes/metrics)."""
    return path.startswith(API_PREFIX)


class ApiNoStoreMiddleware:
    """Pure ASGI middleware: mark API responses uncacheable on the wire."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_api_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        async def send_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in NO_STORE_HEADERS.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_no_store)
