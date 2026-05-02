"""Shared Prometheus alert-rule helper capabilities."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import yaml

from api.services.alert_rule_repo import (
    ALERT_RULE_REPO_SUFFIXES,
    AlertRuleSource,
    build_alert_rule_repo_index,
    dump_alert_rule_document,
    iter_rule_groups,
    render_alert_rule_document,
)
from api.types import JSONObject


class PrometheusAlertRuleHelper:
    """Pure helper API for parsing, indexing, and rendering alert-rule documents."""

    service_type = "prometheus"

    def parse_documents(self, content: str, *, path: str) -> list[Any]:
        """Parse YAML or JSON alert-rule content into structured documents."""
        normalized_path = str(path or "").strip()
        try:
            if normalized_path.lower().endswith(".json"):
                payload = json.loads(content)
                return [] if payload is None else [payload]
            return [payload for payload in yaml.safe_load_all(content) if payload is not None]
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Failed to parse Prometheus alert-rule file {path}: {exc}") from exc

    def alert_names_from_content(
        self,
        content: str,
        *,
        path: str,
        include_recording_rules: bool = False,
    ) -> set[str]:
        """Extract alert names from supported Prometheus rule document shapes."""
        names: set[str] = set()
        for document in self.parse_documents(content, path=path):
            for group, _source_format, _wrapper_key in iter_rule_groups(document):
                for raw_rule in group.get("rules", []) or []:
                    if not isinstance(raw_rule, dict):
                        continue
                    alert_name = str(raw_rule.get("alert") or "").strip()
                    record_name = str(raw_rule.get("record") or "").strip()
                    name = alert_name or (record_name if include_recording_rules else "")
                    if name:
                        if name in names:
                            raise ValueError(f"Duplicate alert rule {name!r} discovered in {path}")
                        names.add(name)
        return names

    def parse_rules_from_content(self, content: str, *, path: str) -> list[JSONObject]:
        """Return normalized rule records from supported alert-rule documents."""
        records: list[JSONObject] = []
        for document in self.parse_documents(content, path=path):
            for group, source_format, wrapper_key in iter_rule_groups(document):
                group_name = str(group.get("name") or "").strip()
                rules = group.get("rules")
                if not group_name or not isinstance(rules, list):
                    continue
                for raw_rule in rules:
                    if not isinstance(raw_rule, dict):
                        continue
                    alert_name = str(raw_rule.get("alert") or "").strip()
                    record_name = str(raw_rule.get("record") or "").strip()
                    if not alert_name and not record_name:
                        continue
                    records.append(
                        {
                            "name": alert_name or record_name,
                            "alert": alert_name or None,
                            "record": record_name or None,
                            "group": group_name,
                            "path": path,
                            "source_format": source_format,
                            "wrapper_key": wrapper_key,
                            "rule": dict(raw_rule),
                        }
                    )
        return records

    def index_files(self, files: dict[str, str]) -> JSONObject:
        """Build an alert-rule index from repo-relative file content."""
        with TemporaryDirectory(prefix="poundcake-prometheus-rules-") as tmp:
            base = Path(tmp)
            for relative_path, content in files.items():
                path = Path(str(relative_path))
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("alert-rule file paths must be relative")
                if path.suffix.lower() not in ALERT_RULE_REPO_SUFFIXES:
                    continue
                target = base / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(content), encoding="utf-8")
            index = build_alert_rule_repo_index(base)
        return {
            "files_scanned": index.files_scanned,
            "alerts": {
                name: {
                    "alert_name": entry.alert_name,
                    "group_name": entry.group_name,
                    "rule_data": entry.rule_data,
                    "source": entry.source.as_annotation_value(),
                }
                for name, entry in sorted(index.by_alert_name.items())
            },
        }

    def render_document(
        self,
        records: list[tuple[str, JSONObject, AlertRuleSource]],
        *,
        relative_path: str,
    ) -> Any:
        """Render rule records into a supported alert-rule document shape."""
        return render_alert_rule_document(records, relative_path=relative_path)

    def dump_document(self, document: Any, *, relative_path: str) -> str:
        """Serialize an alert-rule document as YAML or JSON based on path suffix."""
        return dump_alert_rule_document(document, relative_path)


def get_prometheus_helper() -> PrometheusAlertRuleHelper:
    """Return a Prometheus helper instance for plugin bootstrap/discovery."""
    return PrometheusAlertRuleHelper()
