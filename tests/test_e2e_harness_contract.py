"""Contracts for shell e2e harness API access."""

from __future__ import annotations

import re
from pathlib import Path


def test_e2e_poundcake_api_requests_flow_through_cakectl() -> None:
    lib_source = Path("tests/lib.sh").read_text(encoding="utf-8")

    assert "api_request_json()" in lib_source
    assert "${CAKECTL}" in lib_source
    assert "api request" in lib_source

    forbidden_patterns = (
        re.compile(r"curl\b.*\$\{API_URL\}"),
        re.compile(r"curl\b.*\$\{API_ROOT_URL\}"),
        re.compile(r"curl\b.*127\.0\.0\.1:\$\{API_LOCAL_PORT\}"),
        re.compile(r"curl\b.*localhost:\$\{API_LOCAL_PORT\}"),
    )
    allowed_raw_api_scripts = {
        Path("tests/lib.sh"),  # readiness/port-forward bootstrap only
        Path("tests/run_security_abuse_e2e.sh"),  # deliberately forges invalid auth/HMAC traffic
    }

    violations: list[str] = []
    for path in sorted(Path("tests").glob("run*_e2e.sh")):
        if path in allowed_raw_api_scripts:
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if any(pattern.search(line) for pattern in forbidden_patterns):
                violations.append(f"{path}:{line_no}: {line.strip()}")

    assert violations == []
