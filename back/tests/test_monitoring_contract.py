"""Monitoring contract: Grafana dashboards and alerts query only Prometheus.

The legacy JSON `/metrics` answer was removed (`docs/WORKFLOW.md` §Deprecation
policy: loud breaking removal, no compatibility window). This test keeps the
remaining consumer contract explicit: every series used by a provisioned
dashboard or alert rule comes from the `dm_*` exposition (or Prometheus' `up`),
and the scrape path stays `/metrics`. The backup sidecar's `dm_pg_backup_*`
series are part of the same contract.
"""

import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_ALERT_RULES = _ROOT / "infra" / "prometheus" / "alert-rules.yml"
_DASHBOARDS_DIR = _ROOT / "infra" / "grafana" / "provisioning" / "dashboards-json"
_SCRAPE_CONFIG = _ROOT / "infra" / "prometheus" / "prometheus.yml.tpl"

# Legacy JSON keys must never reappear in a dashboard or an alert expression.
_LEGACY_KEYS = (
    "active_jobs",
    "cache.hits",
    "cache.misses",
    "cache.writes",
    "cache.evictions",
    "gc_candidates",
    "gc_deleted",
    "gc_reclaimed_bytes",
    "gc_failures",
)

# PromQL functions/operators and label names are not series names.
_NON_SERIES_TOKENS = frozenset(
    {
        "and",
        "avg",
        "by",
        "clamp_min",
        "count",
        "d",
        "dependency",
        "error_code",
        "h",
        "histogram_quantile",
        "increase",
        "instance",
        "job",
        "kind",
        "le",
        "m",
        "max",
        "min",
        "min_over_time",
        "operation",
        "or",
        "outcome",
        "rate",
        "route",
        "s",
        "status",
        "storage",
        "sum",
        "time",
        "vector",
        "w",
        "without",
        "y",
    }
)


def _dashboard_expressions(value: object) -> list[str]:
    """Collect every Grafana target expression from a provisioned dashboard."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "expr" and isinstance(item, str):
                found.append(item)
            else:
                found.extend(_dashboard_expressions(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_dashboard_expressions(item))
    return found


def _alert_expressions(text: str) -> list[str]:
    """Collect alert `expr` values, including YAML block scalars."""
    found: list[str] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        match = re.match(r"^(\s*)expr:\s*(.*)$", lines[index])
        if match:
            indent = len(match.group(1))
            value = match.group(2).strip()
            if value in {"|", "|-", ">", ">-"}:
                block: list[str] = []
                index += 1
                while index < len(lines):
                    line = lines[index]
                    if line.strip() and len(line) - len(line.lstrip()) <= indent:
                        break
                    if line.strip():
                        block.append(line.strip())
                    index += 1
                found.append(" ".join(block))
                continue
            if value:
                found.append(value)
        index += 1
    return found


def _series_in_expression(expression: str) -> set[str]:
    """Return series-like tokens; Loki log queries contain no Prometheus series."""
    if "|" in expression:
        return set()
    without_strings = re.sub(r'"[^"]*"', "", expression)
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", without_strings)
    return set(tokens) - _NON_SERIES_TOKENS


@pytest.fixture(scope="module")
def expressions() -> list[str]:
    dashboards = sorted(_DASHBOARDS_DIR.glob("*.json"))
    assert len(dashboards) >= 4, "all provisioned dashboards must stay covered"
    found: list[str] = []
    for path in dashboards:
        found.extend(_dashboard_expressions(json.loads(path.read_text(encoding="utf-8"))))
    found.extend(_alert_expressions(_ALERT_RULES.read_text(encoding="utf-8")))
    assert found, "no dashboard/alert expressions found"
    return found


def test_dashboards_and_alerts_reference_only_prometheus_series(expressions: list[str]) -> None:
    series = {name for expression in expressions for name in _series_in_expression(expression)}
    assert series, "expected real Prometheus series in the monitoring contract"
    assert {"dm_http_requests_total", "dm_jobs_active"} <= series
    assert {"dm_pg_backup_last_success_age_seconds", "dm_pg_backup_runs_total"} <= series
    assert series <= {"up"} | {name for name in series if name.startswith("dm_")}
    for key in _LEGACY_KEYS:
        assert not any(key in expression for expression in expressions), key


def test_prometheus_still_scrapes_the_metrics_path() -> None:
    config = _SCRAPE_CONFIG.read_text(encoding="utf-8")
    assert config.count("metrics_path: /metrics") >= 3
    assert "job_name: docmasker-back" in config
    assert "job_name: docmasker-worker" in config
    assert "job_name: docmasker-pg-backup" in config
