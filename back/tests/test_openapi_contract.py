"""OpenAPI contract checks against `app.openapi()` only (no HTTP server, no DB)."""

import json
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any, get_args

import pytest

from src.jobs.models import JobKind, JobPhase, JobStatus
from src.main import app
from src.openapi_export import export_schema


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return deepcopy(app.openapi())


def _response(doc: dict[str, Any], path: str, method: str, code: str) -> dict[str, Any]:
    return doc["paths"][path][method]["responses"][code]


def _json_ref(response: dict[str, Any], media: str = "application/json") -> str:
    return response["content"][media]["schema"]["$ref"]


def _walk(node: object) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                yield key
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def test_paths_and_methods(schema: dict[str, Any]) -> None:
    assert {path: sorted(methods) for path, methods in schema["paths"].items()} == {
        "/api/labels": ["get"],
        "/api/mask": ["post"],
        "/api/mask-file": ["post"],
        "/api/unmask": ["post"],
        "/api/unmask-file": ["post"],
        "/api/jobs": ["post"],
        "/api/jobs/{job_id}": ["delete", "get"],
        "/api/jobs/{job_id}/result": ["get"],
        "/health": ["get"],
        "/ready": ["get"],
    }


def test_json_success_and_accepted_job_responses(schema: dict[str, Any]) -> None:
    for path, model in (("/api/mask", "MaskResponse"), ("/api/unmask", "UnmaskResponse")):
        post = schema["paths"][path]["post"]
        assert _json_ref(_response(schema, path, "post", "200")) == f"#/components/schemas/{model}"
        assert _json_ref(_response(schema, path, "post", "202")) == "#/components/schemas/JobAccepted"
        assert set(post["responses"]) == {"200", "202", "413", "422", "429"}

    assert _json_ref(_response(schema, "/api/labels", "get", "200")).endswith("/LabelsResponse")
    assert _json_ref(_response(schema, "/health", "get", "200")).endswith("/HealthResponse")
    assert _json_ref(_response(schema, "/ready", "get", "200")).endswith("/ReadyResponse")
    assert _json_ref(_response(schema, "/ready", "get", "503")).endswith("/NotReadyResponse")


def test_file_success_is_binary_zip_and_cancel_is_no_content(schema: dict[str, Any]) -> None:
    for path in ("/api/mask-file", "/api/unmask-file", "/api/jobs/{job_id}/result"):
        method = "get" if path.endswith("/result") else "post"
        response = _response(schema, path, method, "200")
        assert list(response["content"]) == ["application/zip"]
        assert response["content"]["application/zip"]["schema"] == {
            "type": "string",
            "format": "binary",
        }
        assert response["headers"]["Cache-Control"]["schema"] == {"type": "string"}
        assert response["headers"]["Content-Disposition"]["schema"] == {"type": "string"}

    for path in ("/api/mask-file", "/api/unmask-file"):
        assert _json_ref(_response(schema, path, "post", "202")).endswith("/JobAccepted")
    result = schema["paths"]["/api/jobs/{job_id}/result"]["get"]["responses"]
    assert _json_ref(result["404"]).endswith("/ApiErrorResponse")
    assert _json_ref(result["409"]).endswith("/ApiErrorResponse")

    delete = _response(schema, "/api/jobs/{job_id}", "delete", "204")
    assert delete == {"description": "Cancellation accepted; no response body."}


def test_job_submit_multipart_and_kind_enum_without_runtime_literal(schema: dict[str, Any]) -> None:
    post = schema["paths"]["/api/jobs"]["post"]
    request = post["requestBody"]["content"]
    assert list(request) == ["multipart/form-data"]
    body_name = request["multipart/form-data"]["schema"]["$ref"].rsplit("/", 1)[-1]
    body = schema["components"]["schemas"][body_name]
    kind = body["properties"]["kind"]
    assert set(body["properties"]) == {"file", "text", "keys", "kind", "filename"}
    assert kind["type"] == "string"
    assert kind["default"] == "mask_text"
    # Documented literal; the endpoint still validates `str` itself so the
    # stable `unknown_kind` 422 code/detail are unchanged.
    assert kind["enum"] == list(get_args(JobKind))
    assert _json_ref(_response(schema, "/api/jobs", "post", "202")).endswith("/JobAccepted")
    assert set(post["responses"]) == {"202", "413", "415", "422", "429"}


def test_job_status_and_phase_are_closed_unions(schema: dict[str, Any]) -> None:
    status = schema["components"]["schemas"]["JobStatusResponse"]["properties"]
    assert status["status"]["enum"] == list(get_args(JobStatus))
    assert status["phase"]["enum"] == list(get_args(JobPhase))
    assert status["result_url"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    accepted = schema["components"]["schemas"]["JobAccepted"]["properties"]
    assert accepted["status"]["enum"] == list(get_args(JobStatus))


def test_sanitized_error_envelope_is_the_only_error_schema(schema: dict[str, Any]) -> None:
    components = schema["components"]["schemas"]
    envelope = components["ApiErrorResponse"]
    assert set(envelope["properties"]) == {"code", "detail", "request_id"}
    assert set(envelope["required"]) == {"code", "detail", "request_id"}
    assert "HTTPValidationError" not in components
    assert "ValidationError" not in components
    for opaque in ("input", "loc", "msg", "ctx"):
        assert opaque not in envelope["properties"]

    for path, methods in schema["paths"].items():
        if not path.startswith("/api/"):
            continue
        for operation in methods.values():
            for code, response in operation["responses"].items():
                if not code.startswith("2"):
                    assert _json_ref(response).endswith("/ApiErrorResponse"), (path, code)
                    assert "X-Request-ID" in response["headers"]


def test_internal_pii_bearing_models_are_not_exposed(schema: dict[str, Any]) -> None:
    components = schema["components"]["schemas"]
    assert "MaskedEntity" not in components
    assert "JobSubmitText" not in components
    assert "JobKindInfo" not in components
    assert set(components["MaskResponse"]["properties"]) == {"text", "stats", "keys"}
    assert set(components["MaskStats"]["properties"]) == {
        "total",
        "occurrences_by_label",
        "unique_by_label",
    }
    dumped = json.dumps(schema, ensure_ascii=False)
    assert "entities" not in components["MaskResponse"]["properties"]
    for private in ("Иван Петров", "ivan@mail.ru", "Профиль: russian-default", "/var/secrets"):
        assert private not in dumped
    assert "example" not in set(_walk(schema))
    assert "examples" not in set(_walk(schema))


def test_openapi_export_is_deterministic(tmp_path: Path) -> None:
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    export_schema(first)
    export_schema(second)
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes().endswith(b"\n")
