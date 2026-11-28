"""Preflight must expose the DB target without credentials and fail closed."""

from __future__ import annotations

import os

import pytest

from src.scripts.check_migration_target import main, resolve_target, validate_image_pin

_ENVVARS = (
    "DATABASE_URL",
    "POSTGRES_HOST_DOCKER",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "ALLOW_LOCAL_DB",
    "DM_BACK_IMAGE",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in _ENVVARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _compose_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST_DOCKER", "postgres")
    monkeypatch.setenv("POSTGRES_DB", "docmasker")
    monkeypatch.setenv("POSTGRES_USER", "dm")
    monkeypatch.setenv("POSTGRES_PASSWORD", "super-secret-value")
    monkeypatch.setenv("DM_BACK_IMAGE", "ghcr.io/example/dm-back:v1.2.3")


def test_local_compose_target_is_refused_without_confirmation(
    clean_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _compose_env(clean_env)
    assert main() == 1
    out, err = capsys.readouterr()
    assert "target host     : postgres" in out
    assert "target port     : 5432" in out
    assert "target database : docmasker" in out
    assert "target schema   : public" in out
    assert "POSTGRES_HOST_DOCKER (compose x-db-env)" in out
    assert "ALLOW_LOCAL_DB=1" in err
    assert "super-secret-value" not in out + err


def test_local_compose_target_allowed_after_explicit_confirmation(
    clean_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _compose_env(clean_env)
    clean_env.setenv("ALLOW_LOCAL_DB", "1")
    assert main() == 0
    out, err = capsys.readouterr()
    assert "Preflight OK" in out
    assert err == ""


def test_external_url_reports_schema_and_never_prints_password(
    clean_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clean_env.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://dm:super-secret-value@db.example.org:6432/docmasker"
        "?options=-csearch_path%3Ddm_migration_smoke",
    )
    clean_env.setenv("DM_BACK_IMAGE", "ghcr.io/example/dm-back:sha-abc1234")
    assert main() == 0
    out, err = capsys.readouterr()
    assert "target host     : db.example.org" in out
    assert "target port     : 6432" in out
    assert "target database : docmasker" in out
    assert "target schema   : dm_migration_smoke" in out
    assert "DATABASE_URL (-e)" in out
    assert "super-secret-value" not in out + err
    assert err == ""


def test_explicit_schema_query_param_is_supported(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DATABASE_URL", "postgresql://dm:pw@db.example.org/dm?schema=scratch")
    target = resolve_target(dict(os.environ))
    assert target.schema == "scratch"
    assert target.port == 5432


def test_local_url_is_refused_even_when_compose_values_are_present(clean_env: pytest.MonkeyPatch) -> None:
    _compose_env(clean_env)
    clean_env.setenv("DATABASE_URL", "postgresql://dm:super-secret-value@127.0.0.1:15433/docmasker")
    clean_env.setenv("POSTGRES_HOST_DOCKER", "external.example.org")
    assert main() == 1
    target = resolve_target(dict(os.environ))
    assert target.host == "127.0.0.1"
    assert target.is_local


def test_invalid_url_port_fails_without_echoing_the_url(
    clean_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clean_env.setenv("DATABASE_URL", "postgresql://dm:super-secret-value@db.example.org:notaport/dm")
    assert main() == 1
    out, err = capsys.readouterr()
    assert "invalid port" in err
    assert "super-secret-value" not in out + err


def test_missing_compose_database_fails(clean_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    clean_env.setenv("DM_BACK_IMAGE", "ghcr.io/example/dm-back:v1.2.3")
    clean_env.setenv("POSTGRES_USER", "dm")
    clean_env.setenv("POSTGRES_PASSWORD", "pw")
    assert main() == 1
    assert "POSTGRES_DB is required" in capsys.readouterr().err


@pytest.mark.parametrize("image", ["", "ghcr.io/example/dm-back", "ghcr.io/example/dm-back:latest", ":latest"])
def test_image_pin_rejects_ambiguous_images(clean_env: pytest.MonkeyPatch, image: str) -> None:
    clean_env.setenv("DM_BACK_IMAGE", image)
    with pytest.raises(ValueError, match="DM_BACK_IMAGE"):
        validate_image_pin(dict(os.environ))


@pytest.mark.parametrize(
    "image",
    [
        "ghcr.io/example/dm-back:v1.2.3",
        "ghcr.io/example/dm-back:sha-abc1234",
        "ghcr.io/example/dm-back@sha256:deadbeef",
    ],
)
def test_image_pin_accepts_release_tag_or_digest(clean_env: pytest.MonkeyPatch, image: str) -> None:
    clean_env.setenv("DM_BACK_IMAGE", image)
    assert validate_image_pin(dict(os.environ)) == image
