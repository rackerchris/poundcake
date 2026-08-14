"""Tests for the executable contract index in docs/CONTRACTS.md."""

from __future__ import annotations

import re
from pathlib import Path


CONTRACT_DOC = Path("docs/CONTRACTS.md")


def test_contract_doc_sections_have_coverage_references() -> None:
    source = CONTRACT_DOC.read_text(encoding="utf-8")
    sections = list(re.finditer(r"^## ([1-9][0-9]*)\. .+$", source, flags=re.MULTILINE))

    assert [match.group(1) for match in sections] == [str(index) for index in range(1, 11)]

    missing: list[str] = []
    for index, match in enumerate(sections):
        next_start = sections[index + 1].start() if index + 1 < len(sections) else len(source)
        section = source[match.start() : next_start]
        if "Coverage:" not in section:
            missing.append(match.group(0).removeprefix("## "))

    assert missing == []


def test_contract_doc_coverage_map_references_all_contract_sections() -> None:
    source = CONTRACT_DOC.read_text(encoding="utf-8")
    coverage_map = source.split("## Contract Coverage Map", 1)[1].split("\n---\n", 1)[0]
    expected_areas = {
        "Workflow execution path",
        "Database access",
        "RBAC and endpoint separation",
        "Credential management",
        "Plugin contract",
        "Service registry",
        "UI contract",
        "Scheduled tasks",
        "Communication policy",
        "Helper capabilities",
    }

    missing = sorted(area for area in expected_areas if area not in coverage_map)

    assert missing == []
