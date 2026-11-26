"""CI guard: internal/admin ports must be published on loopback only.

Scans the Compose files for host port mappings. Monitoring, database, CI and
backend ports are internal/admin surfaces and must start with `127.0.0.1:`.
The public front service is the one deliberate exception (it is the intended
user-facing entry point; public TLS/auth remains a reverse-proxy concern).

Usage: python infra/check-monitoring-binds.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "compose.yaml",
    "infra/compose.db.yaml",
    "infra/compose.monitoring.yaml",
    "infra/compose.ci.yaml",
)
PUBLIC_SERVICES = frozenset({"front"})
SERVICE_RE = re.compile(r"^  ([A-Za-z0-9_-]+):\s*(?:#.*)?$")
PORTS_RE = re.compile(r"^\s*ports:\s*(.*)$")
ITEM_RE = re.compile(r"^\s*-\s*[\"']?([^\"'#]+)[\"']?")


def _bad_mapping(mapping: str) -> bool:
    """True when a host:container mapping is published beyond loopback."""
    if ":" not in mapping:
        return False  # container-only port declaration, not published
    host = mapping.split(":", 1)[0].strip()
    return host not in {"127.0.0.1", "::1"}


def check_file(relative: str) -> list[str]:
    problems: list[str] = []
    service = ""
    in_ports = False
    path = ROOT / relative
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        service_match = SERVICE_RE.match(raw)
        if service_match:
            service = service_match.group(1)
            in_ports = False
            continue
        ports_match = PORTS_RE.match(raw)
        if ports_match:
            in_ports = True
            inline = ports_match.group(1).strip()
            if inline.startswith("["):
                for mapping in re.findall(r"\"([^\"]+)\"|'([^']+)'", inline):
                    value = mapping[0] or mapping[1]
                    if value and service not in PUBLIC_SERVICES and _bad_mapping(value):
                        problems.append(f"{relative}:{number}: {service}: {value}")
            continue
        if in_ports:
            if not raw.strip():
                continue
            item = ITEM_RE.match(raw)
            if item:
                value = item.group(1).strip()
                if service not in PUBLIC_SERVICES and _bad_mapping(value):
                    problems.append(f"{relative}:{number}: {service}: {value}")
                continue
            in_ports = False
    return problems


def main() -> int:
    problems = [problem for relative in FILES for problem in check_file(relative)]
    if problems:
        print("Host-wide internal port bindings are not allowed:")
        for problem in problems:
            print(f"  {problem}")
        print("Bind to 127.0.0.1 or keep the port private to the Compose network.")
        return 1
    print("Compose host bindings OK (internal services are loopback-only).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
