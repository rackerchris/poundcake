"""Tests for keeping packaged Alembic migrations in sync."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _revision_ids(path: Path) -> dict[str, str]:
    revisions: dict[str, str] = {}
    for migration in sorted(path.glob("*.py")):
        if migration.name == "__init__.py":
            continue
        module = ast.parse(migration.read_text())
        revision = None
        for node in module.body:
            if isinstance(node, ast.Assign):
                has_revision_target = any(
                    isinstance(target, ast.Name) and target.id == "revision"
                    for target in node.targets
                )
                if has_revision_target:
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        revision = node.value.value
                        break
            if isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.target.id == "revision":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        revision = node.value.value
                        break
        assert revision, f"{migration} does not declare a revision id"
        revisions[migration.name] = revision
    return revisions


def test_helm_packaged_alembic_revisions_match_source_tree() -> None:
    source = _revision_ids(ROOT / "alembic/versions")
    packaged = _revision_ids(ROOT / "helm/files/poundcake-alembic/versions")

    assert packaged == source
