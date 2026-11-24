"""Phase 6 §8.1: stable error contract, request ids, no PII/traceback leakage."""

from fastapi import FastAPI, status
from httpx2 import ASGITransport, AsyncClient

from src.errors import REQUEST_ID_HEADER, ApiError, install_error_handlers
from src.main import app


def _client(target: FastAPI) -> AsyncClient:
    # raise_app_exceptions=False mirrors Granian: the ServerErrorMiddleware
    # response (our sanitized 500) is what the client actually receives.
    return AsyncClient(transport=ASGITransport(app=target, raise_app_exceptions=False), base_url="http://test")


async def test_every_error_has_code_detail_request_id_and_header() -> None:
    """Validation, domain, not-found, conflict and internal errors share one shape."""
    probe = FastAPI()
    install_error_handlers(probe)

    @probe.get("/boom")
    async def boom() -> None:
        msg = "local /var/secrets/db.py:ivan@mail.ru КЛЮЧИ"
        raise RuntimeError(msg)

    @probe.get("/domain")
    async def domain() -> None:
        code = "invalid_keys"
        detail = "Файл ключей не является корректным YAML."
        raise ApiError(code, detail, 422)

    @probe.get("/conflict")
    async def conflict() -> None:
        code = "result_not_ready"
        detail = "Результат ещё не готов."
        raise ApiError(code, detail, status.HTTP_409_CONFLICT)

    async with _client(probe) as client:
        responses = [await client.get(path) for path in ("/missing", "/domain", "/conflict", "/boom")]
        bad_json = await client.post("/domain", content=b"not json", headers={"Content-Type": "application/json"})

    assert [r.status_code for r in responses] == [404, 422, 409, 500]
    codes = []
    for response in (*responses, bad_json):
        body = response.json()
        assert set(body) == {"code", "detail", "request_id"}
        assert isinstance(body["request_id"], str)
        assert body["request_id"]
        assert isinstance(body["detail"], str)
        assert body["detail"]
        assert response.headers[REQUEST_ID_HEADER] == body["request_id"]
        # No traceback, path, DB text, PII or keys ever leak.
        assert "/var/secrets" not in str(body)
        assert "ivan@mail.ru" not in str(body)
        assert "Traceback" not in str(body)
        codes.append(body["code"])
    assert codes == ["not_found", "invalid_keys", "result_not_ready", "internal_error", "error"]
    assert responses[1].json()["code"] == "invalid_keys"
    assert responses[1].json()["detail"] == "Файл ключей не является корректным YAML."


async def test_request_validation_error_does_not_echo_submitted_pii() -> None:
    probe = FastAPI()
    install_error_handlers(probe)

    @probe.post("/echo")
    async def echo(payload: dict[str, int]) -> dict[str, int]:
        return payload

    async with _client(probe) as client:
        response = await client.post("/echo", json={"value": "Иван Петров ivan@mail.ru"})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert "Иван Петров" not in str(body)
    assert "ivan@mail.ru" not in str(body)
    assert body["request_id"] == response.headers[REQUEST_ID_HEADER]


async def test_success_response_carries_request_id() -> None:
    async with _client(app) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER]


async def test_router_http_exception_maps_to_stable_code() -> None:
    """The existing router's fixed Russian detail strings get machine codes."""
    async with _client(app) as client:
        response = await client.post("/api/mask", json={"text": "   "})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "empty_text"
    assert body["detail"] == "Текст пуст."
    assert body["request_id"] == response.headers[REQUEST_ID_HEADER]
