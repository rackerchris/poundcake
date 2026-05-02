"""Helpers for parsing and rendering Git-backed alert-rule documents."""

from __future__ import annotations

from api.types import JSONObject

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

ALERT_RULE_SOURCE_ANNOTATION = "poundcake.io/alert-rule-sources"
ALERT_RULE_SOURCE_FORMAT_ADDITIONAL_MAP = "additionalPrometheusRulesMap"
ALERT_RULE_SOURCE_FORMAT_GROUP_LIST = "group_list"
ALERT_RULE_SOURCE_FORMAT_GROUPS = "groups"
ALERT_RULE_SOURCE_FORMAT_RULES = "rules"
ALERT_RULE_SOURCE_FORMAT_SPEC_GROUPS = "spec.groups"
ALERT_RULE_REPO_SUFFIXES = {".json", ".yaml", ".yml"}


@dataclass(frozen=True)
class AlertRuleSource:
    """Repo source details for an alert rule."""

    relative_path: str
    source_format: str = ALERT_RULE_SOURCE_FORMAT_ADDITIONAL_MAP
    wrapper_key: str | None = None

    def as_annotation_value(self) -> dict[str, str]:
        payload = {
            "file": self.relative_path,
            "format": self.source_format,
        }
        if self.wrapper_key:
            payload["wrapper_key"] = self.wrapper_key
        return payload

    @classmethod
    def from_annotation_value(cls, payload: Any) -> AlertRuleSource | None:
        if not isinstance(payload, dict):
            return None
        file_name = str(payload.get("file") or "").strip()
        source_format = str(payload.get("format") or "").strip()
        if not file_name or not source_format:
            return None
        wrapper_key = str(payload.get("wrapper_key") or "").strip() or None
        return cls(relative_path=file_name, source_format=source_format, wrapper_key=wrapper_key)


@dataclass(frozen=True)
class AlertRuleRepoEntry:
    """Discovered alert rule plus the repo source it came from."""

    alert_name: str
    group_name: str
    rule_data: JSONObject
    source: AlertRuleSource


@dataclass(frozen=True)
class AlertRuleRepoIndex:
    """Alert-rule lookup keyed by alert name for a checked-out repository."""

    by_alert_name: dict[str, AlertRuleRepoEntry]
    files_scanned: int


def default_wrapper_key_for_path(relative_path: str) -> str:
    """Derive a stable wrapper key from a repo-relative path."""
    return Path(relative_path).stem or "alert-rules"


def normalize_repo_relative_path(value: str) -> str:
    """Validate and normalize a repo-relative path."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("must not be empty")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("must be a relative repo path")
    normalized = str(path).strip("/")
    if not normalized or normalized == ".":
        raise ValueError("must not be empty")
    return normalized


def looks_like_repo_relative_rule_path(value: str) -> bool:
    """Return True when a rule source looks like a repo-relative file path."""
    raw = str(value or "").strip()
    if not raw:
        return False
    if "/" in raw:
        return True
    return raw.endswith((".json", ".yaml", ".yml"))


def iter_rule_groups(document: Any) -> list[tuple[JSONObject, str, str | None]]:
    """Extract rule-group payloads from supported alert-rule document shapes."""
    if not document:
        return []
    if isinstance(document, list):
        return [
            (item, ALERT_RULE_SOURCE_FORMAT_GROUP_LIST, None)
            for item in document
            if isinstance(item, dict)
        ]
    if not isinstance(document, dict):
        raise ValueError("alert rule document must be an object or list")

    groups: list[tuple[JSONObject, str, str | None]] = []

    direct_groups = document.get("groups")
    if isinstance(direct_groups, list):
        groups.extend(
            (item, ALERT_RULE_SOURCE_FORMAT_GROUPS, None)
            for item in direct_groups
            if isinstance(item, dict)
        )

    if isinstance(document.get("rules"), list):
        groups.append((document, ALERT_RULE_SOURCE_FORMAT_RULES, None))

    spec = document.get("spec")
    if isinstance(spec, dict) and isinstance(spec.get("groups"), list):
        groups.extend(
            (item, ALERT_RULE_SOURCE_FORMAT_SPEC_GROUPS, None)
            for item in spec["groups"]
            if isinstance(item, dict)
        )

    rule_map = document.get("additionalPrometheusRulesMap")
    if isinstance(rule_map, dict):
        for wrapper_key, value in rule_map.items():
            if isinstance(value, dict) and isinstance(value.get("groups"), list):
                groups.extend(
                    (item, ALERT_RULE_SOURCE_FORMAT_ADDITIONAL_MAP, str(wrapper_key))
                    for item in value["groups"]
                    if isinstance(item, dict)
                )

    return groups


def load_repo_documents(path: Path) -> list[Any]:
    """Load zero or more structured documents from a repo file."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload = json.loads(text)
        return [] if payload is None else [payload]
    return [payload for payload in yaml.safe_load_all(text) if payload is not None]


def build_alert_rule_repo_index(base_dir: Path) -> AlertRuleRepoIndex:
    """Scan an alert-rule repo directory and index rules by alert name."""
    by_alert_name: dict[str, AlertRuleRepoEntry] = {}
    files_scanned = 0

    if not base_dir.exists():
        return AlertRuleRepoIndex(by_alert_name=by_alert_name, files_scanned=files_scanned)

    for path in sorted(base_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in ALERT_RULE_REPO_SUFFIXES:
            continue
        files_scanned += 1
        relative_path = path.relative_to(base_dir).as_posix()
        for document in load_repo_documents(path):
            for group, source_format, wrapper_key in iter_rule_groups(document):
                group_name = str(group.get("name") or "").strip()
                rules = group.get("rules")
                if not group_name or not isinstance(rules, list):
                    continue
                source = AlertRuleSource(
                    relative_path=relative_path,
                    source_format=source_format,
                    wrapper_key=wrapper_key
                    or (
                        default_wrapper_key_for_path(relative_path)
                        if source_format == ALERT_RULE_SOURCE_FORMAT_ADDITIONAL_MAP
                        else None
                    ),
                )
                for raw_rule in rules:
                    if not isinstance(raw_rule, dict):
                        continue
                    alert_name = str(raw_rule.get("alert") or raw_rule.get("record") or "").strip()
                    if not alert_name:
                        continue
                    if alert_name in by_alert_name:
                        raise ValueError(f"duplicate alert rule '{alert_name}' discovered in repo")
                    by_alert_name[alert_name] = AlertRuleRepoEntry(
                        alert_name=alert_name,
                        group_name=group_name,
                        rule_data=dict(raw_rule),
                        source=source,
                    )

    return AlertRuleRepoIndex(by_alert_name=by_alert_name, files_scanned=files_scanned)


def infer_document_source(document: Any, relative_path: str) -> AlertRuleSource | None:
    """Infer a preferred source format for an existing alert-rule file."""
    groups = iter_rule_groups(document)
    if not groups:
        return None
    _, source_format, wrapper_key = groups[0]
    return AlertRuleSource(
        relative_path=relative_path,
        source_format=source_format,
        wrapper_key=wrapper_key
        or (
            default_wrapper_key_for_path(relative_path)
            if source_format == ALERT_RULE_SOURCE_FORMAT_ADDITIONAL_MAP
            else None
        ),
    )


def load_alert_rule_sources_from_annotations(annotations: Any) -> dict[str, AlertRuleSource]:
    """Decode per-alert source metadata stored on a PrometheusRule CRD."""
    if not isinstance(annotations, dict):
        return {}
    raw_value = annotations.get(ALERT_RULE_SOURCE_ANNOTATION)
    if not isinstance(raw_value, str) or not raw_value.strip():
        return {}
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}

    parsed: dict[str, AlertRuleSource] = {}
    for rule_name, item in payload.items():
        source = AlertRuleSource.from_annotation_value(item)
        if source is None:
            continue
        parsed[str(rule_name)] = source
    return parsed


def dump_alert_rule_sources_to_annotations(
    existing_annotations: Any,
    sources: dict[str, AlertRuleSource],
) -> JSONObject:
    """Encode per-alert source metadata onto a CRD annotation map."""
    annotations = dict(existing_annotations or {})
    if not sources:
        annotations.pop(ALERT_RULE_SOURCE_ANNOTATION, None)
        return annotations
    annotations[ALERT_RULE_SOURCE_ANNOTATION] = json.dumps(
        {name: source.as_annotation_value() for name, source in sorted(sources.items())},
        sort_keys=True,
    )
    return annotations


def document_has_rules(document: Any) -> bool:
    """Return True when a parsed alert-rule document still contains alert entries."""
    for group, _, _ in iter_rule_groups(document):
        rules = group.get("rules")
        if not isinstance(rules, list):
            continue
        if any(
            isinstance(rule, dict) and str(rule.get("alert") or rule.get("record") or "").strip()
            for rule in rules
        ):
            return True
    return False


def _ensure_group_entry(groups: list[JSONObject], group_name: str) -> JSONObject:
    for group in groups:
        if group.get("name") == group_name:
            if not isinstance(group.get("rules"), list):
                group["rules"] = []
            return group
    group = {"name": group_name, "rules": []}
    groups.append(group)
    return group


def upsert_rule_in_document(
    document: Any,
    *,
    source: AlertRuleSource,
    group_name: str,
    rule_name: str,
    rule_data: JSONObject,
) -> Any:
    """Insert or update a rule in a parsed alert-rule document."""
    payload = dict(rule_data)

    if source.source_format == ALERT_RULE_SOURCE_FORMAT_GROUP_LIST:
        groups = list(document) if isinstance(document, list) else []
        group = _ensure_group_entry(groups, group_name)
        rules = group.setdefault("rules", [])
        for idx, existing in enumerate(list(rules)):
            if (
                isinstance(existing, dict)
                and str(existing.get("alert") or existing.get("record") or "").strip() == rule_name
            ):
                rules[idx] = payload
                break
        else:
            rules.append(payload)
        return groups

    if not isinstance(document, dict):
        document = {}

    if source.source_format == ALERT_RULE_SOURCE_FORMAT_RULES:
        current_name = str(document.get("name") or "").strip()
        if current_name and current_name != group_name:
            raise ValueError("top-level rules documents can only store a single group")
        document["name"] = group_name
        rules = document.get("rules")
        if not isinstance(rules, list):
            rules = []
            document["rules"] = rules
        for idx, existing in enumerate(list(rules)):
            if (
                isinstance(existing, dict)
                and str(existing.get("alert") or existing.get("record") or "").strip() == rule_name
            ):
                rules[idx] = payload
                break
        else:
            rules.append(payload)
        return document

    if source.source_format == ALERT_RULE_SOURCE_FORMAT_SPEC_GROUPS:
        spec = document.get("spec")
        if not isinstance(spec, dict):
            spec = {}
            document["spec"] = spec
        groups = spec.get("groups")
        if not isinstance(groups, list):
            groups = []
            spec["groups"] = groups
    elif source.source_format == ALERT_RULE_SOURCE_FORMAT_ADDITIONAL_MAP:
        wrapper_key = source.wrapper_key or default_wrapper_key_for_path(source.relative_path)
        rule_map = document.get("additionalPrometheusRulesMap")
        if not isinstance(rule_map, dict):
            rule_map = {}
            document["additionalPrometheusRulesMap"] = rule_map
        wrapper = rule_map.get(wrapper_key)
        if not isinstance(wrapper, dict):
            wrapper = {}
            rule_map[wrapper_key] = wrapper
        groups = wrapper.get("groups")
        if not isinstance(groups, list):
            groups = []
            wrapper["groups"] = groups
    else:
        groups = document.get("groups")
        if not isinstance(groups, list):
            groups = []
            document["groups"] = groups

    group = _ensure_group_entry(groups, group_name)
    rules = group.setdefault("rules", [])
    for idx, existing in enumerate(list(rules)):
        if (
            isinstance(existing, dict)
            and str(existing.get("alert") or existing.get("record") or "").strip() == rule_name
        ):
            rules[idx] = payload
            break
    else:
        rules.append(payload)
    return document


def delete_rule_from_document(
    document: Any,
    *,
    source: AlertRuleSource,
    group_name: str,
    rule_name: str,
) -> tuple[Any, bool]:
    """Delete a rule from a parsed alert-rule document."""
    deleted = False

    if source.source_format == ALERT_RULE_SOURCE_FORMAT_GROUP_LIST:
        if not isinstance(document, list):
            return document, False
        groups = list(document)
        for group in list(groups):
            if group.get("name") != group_name:
                continue
            rules = group.get("rules")
            if not isinstance(rules, list):
                return document, False
            for idx, existing in enumerate(list(rules)):
                if (
                    isinstance(existing, dict)
                    and str(existing.get("alert") or existing.get("record") or "").strip()
                    == rule_name
                ):
                    del rules[idx]
                    deleted = True
                    break
            if deleted and not rules:
                groups.remove(group)
            break
        return groups, deleted

    if not isinstance(document, dict):
        return document, False

    if source.source_format == ALERT_RULE_SOURCE_FORMAT_RULES:
        if str(document.get("name") or "").strip() != group_name:
            return document, False
        rules = document.get("rules")
        if not isinstance(rules, list):
            return document, False
        for idx, existing in enumerate(list(rules)):
            if (
                isinstance(existing, dict)
                and str(existing.get("alert") or existing.get("record") or "").strip() == rule_name
            ):
                del rules[idx]
                deleted = True
                break
        return document, deleted

    if source.source_format == ALERT_RULE_SOURCE_FORMAT_SPEC_GROUPS:
        spec = document.get("spec")
        if not isinstance(spec, dict):
            return document, False
        groups = spec.get("groups")
        if not isinstance(groups, list):
            return document, False
    elif source.source_format == ALERT_RULE_SOURCE_FORMAT_ADDITIONAL_MAP:
        rule_map = document.get("additionalPrometheusRulesMap")
        if not isinstance(rule_map, dict):
            return document, False
        wrapper_key = source.wrapper_key or default_wrapper_key_for_path(source.relative_path)
        wrapper = rule_map.get(wrapper_key)
        if not isinstance(wrapper, dict):
            return document, False
        groups = wrapper.get("groups")
        if not isinstance(groups, list):
            return document, False
    else:
        groups = document.get("groups")
        if not isinstance(groups, list):
            return document, False

    for group in list(groups):
        if group.get("name") != group_name:
            continue
        rules = group.get("rules")
        if not isinstance(rules, list):
            return document, False
        for idx, existing in enumerate(list(rules)):
            if (
                isinstance(existing, dict)
                and str(existing.get("alert") or existing.get("record") or "").strip() == rule_name
            ):
                del rules[idx]
                deleted = True
                break
        if deleted and not rules:
            groups.remove(group)
        break

    if deleted and source.source_format == ALERT_RULE_SOURCE_FORMAT_ADDITIONAL_MAP:
        rule_map = document.get("additionalPrometheusRulesMap")
        if isinstance(rule_map, dict):
            wrapper_key = source.wrapper_key or default_wrapper_key_for_path(source.relative_path)
            wrapper = rule_map.get(wrapper_key)
            if isinstance(wrapper, dict) and not wrapper.get("groups"):
                rule_map.pop(wrapper_key, None)

    return document, deleted


def render_alert_rule_document(
    records: list[tuple[str, JSONObject, AlertRuleSource]],
    *,
    relative_path: str,
) -> Any:
    """Render a document for a set of rules that share a repo file."""
    if not records:
        raise ValueError("at least one rule record is required")

    source_formats = {source.source_format for _, _, source in records}
    if len(source_formats) != 1:
        raise ValueError(f"cannot render mixed alert-rule source formats for '{relative_path}'")
    source_format = next(iter(source_formats))

    if source_format == ALERT_RULE_SOURCE_FORMAT_GROUP_LIST:
        groups: dict[str, JSONObject] = {}
        for group_name, rule_data, _source in records:
            group = groups.setdefault(group_name, {"name": group_name, "rules": []})
            group["rules"].append(dict(rule_data))
        return list(groups.values())

    if source_format == ALERT_RULE_SOURCE_FORMAT_RULES:
        group_names = {group_name for group_name, _, _ in records}
        if len(group_names) != 1:
            raise ValueError(
                f"top-level rules documents require a single group for '{relative_path}'"
            )
        group_name = next(iter(group_names))
        return {
            "name": group_name,
            "rules": [dict(rule_data) for _, rule_data, _source in records],
        }

    if source_format == ALERT_RULE_SOURCE_FORMAT_SPEC_GROUPS:
        spec_groups: dict[str, JSONObject] = {}
        for group_name, rule_data, _source in records:
            group = spec_groups.setdefault(group_name, {"name": group_name, "rules": []})
            group["rules"].append(dict(rule_data))
        return {"spec": {"groups": list(spec_groups.values())}}

    if source_format == ALERT_RULE_SOURCE_FORMAT_ADDITIONAL_MAP:
        wrapper_groups: dict[str, dict[str, JSONObject]] = {}
        for group_name, rule_data, source in records:
            wrapper_key = source.wrapper_key or default_wrapper_key_for_path(relative_path)
            groups = wrapper_groups.setdefault(wrapper_key, {})
            group = groups.setdefault(group_name, {"name": group_name, "rules": []})
            group["rules"].append(dict(rule_data))
        return {
            "additionalPrometheusRulesMap": {
                wrapper_key: {"groups": list(groups.values())}
                for wrapper_key, groups in wrapper_groups.items()
            }
        }

    groups = {}
    for group_name, rule_data, _source in records:
        group = groups.setdefault(group_name, {"name": group_name, "rules": []})
        group["rules"].append(dict(rule_data))
    return {"groups": list(groups.values())}


def dump_alert_rule_document(document: Any, relative_path: str) -> str:
    """Serialize an alert-rule document using the target file type."""
    if relative_path.endswith(".json"):
        return json.dumps(document, indent=2, sort_keys=False) + "\n"
    return yaml.safe_dump(document, sort_keys=False)
