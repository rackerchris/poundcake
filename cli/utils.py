"""Utility functions for CLI parsing and output formatting."""

from __future__ import annotations

from shared.types import JSONObject

from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import click
import yaml
from pydantic import BaseModel as PydanticModel

TableRenderer = Callable[[Any], str]


def compact_json(data: Any) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


def review_value(value: Any) -> str:
    if value in (None, ""):
        return "-"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (dict, list)):
        return compact_json(value)
    return str(value)


def build_diff_rows(
    current: JSONObject,
    next: JSONObject,
    *,
    labels: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    resolved_labels = labels or {}
    keys = list(dict.fromkeys([*current.keys(), *next.keys()]))
    rows: list[dict[str, str]] = []
    for key in keys:
        if compact_json(to_plain_data(current.get(key))) == compact_json(to_plain_data(next.get(key))):
            continue
        rows.append(
            {
                "field": resolved_labels.get(key, titleize(key)),
                "current": review_value(current.get(key)),
                "next": review_value(next.get(key)),
            }
        )
    return rows


def to_plain_data(data: Any) -> Any:
    if isinstance(data, PydanticModel):
        return {
            key: to_plain_data(value)
            for key, value in data.model_dump(mode="json", by_alias=True).items()
        }
    if is_dataclass(data) and not isinstance(data, type):
        return {key: to_plain_data(value) for key, value in asdict(data).items()}
    if isinstance(data, dict):
        return {key: to_plain_data(value) for key, value in data.items()}
    if isinstance(data, (list, tuple)):
        return [to_plain_data(item) for item in data]
    return data


def get_field_value(item: Any, field: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(field, default)
    return getattr(item, field, default)


def format_output(data: Any, format: str = "table") -> str:
    plain = to_plain_data(data)
    if format == "json":
        return json.dumps(plain, indent=2, sort_keys=False)
    if format == "yaml":
        return yaml.safe_dump(plain, default_flow_style=False, sort_keys=False)
    if format == "table":
        return format_table(plain)
    return str(plain)


def format_table(data: Any) -> str:
    if isinstance(data, dict):
        if not data:
            return "No data"
        width = max(len(str(key)) for key in data.keys())
        lines = []
        for key, value in data.items():
            rendered = _table_value(value)
            lines.append(f"{str(key).ljust(width)}  {rendered}")
        return "\n".join(lines)

    if isinstance(data, list):
        if not data:
            return "No items"
        if not isinstance(data[0], dict):
            return "\n".join(str(item) for item in data)

        keys = list(data[0].keys())
        widths = {key: len(str(key)) for key in keys}
        rendered_rows: list[dict[str, str]] = []
        for item in data:
            rendered_row: dict[str, str] = {}
            for key in keys:
                rendered = _table_value(item.get(key, ""))
                if len(rendered) > 72:
                    rendered = rendered[:69] + "..."
                rendered_row[key] = rendered
                widths[key] = max(widths[key], len(rendered))
            rendered_rows.append(rendered_row)

        header = "  ".join(str(key).ljust(widths[key]) for key in keys)
        separator = "  ".join("-" * widths[key] for key in keys)
        rows = [
            "  ".join(rendered_row[key].ljust(widths[key]) for key in keys)
            for rendered_row in rendered_rows
        ]
        return "\n".join([header, separator, *rows])

    return str(data)


def _table_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return compact_json(value)
    if value is None:
        return "-"
    return str(value)


def print_output(
    data: Any, format: str = "table", *, table_renderer: TableRenderer | None = None
) -> None:
    if format == "table" and table_renderer is not None:
        click.echo(table_renderer(to_plain_data(data)))
        return
    click.echo(format_output(data, format))


def print_success(message: str) -> None:
    click.echo(click.style(f"OK: {message}", fg="green"))


def print_error(message: str) -> None:
    click.echo(click.style(f"ERROR: {message}", fg="red"), err=True)


def print_warning(message: str) -> None:
    click.echo(click.style(f"WARN: {message}", fg="yellow"))


def print_info(message: str) -> None:
    click.echo(click.style(f"INFO: {message}", fg="blue"))


def parse_json_value(value: str, label: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise click.BadParameter(f"{label} must be valid JSON: {exc}") from exc


def parse_json_object(value: str | None, label: str) -> JSONObject | None:
    if value is None:
        return None
    parsed = parse_json_value(value, label)
    if not isinstance(parsed, dict):
        raise click.BadParameter(f"{label} must decode to a JSON object")
    return parsed


def load_data_file(path: Path) -> Any:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise click.FileError(str(path), hint=str(exc)) from exc
    if loaded is None:
        return {}
    return loaded


def require_mapping(data: Any, label: str) -> JSONObject:
    if not isinstance(data, dict):
        raise click.BadParameter(f"{label} must be an object")
    return data


def titleize(value: str | None) -> str:
    if not value:
        return "-"
    return str(value).replace("_", " ").strip().title()


def filter_by_search(
    rows: Sequence[Any],
    search: str | None,
    fields: Iterable[str],
) -> list[Any]:
    if not search:
        return list(rows)
    needle = search.lower()
    filtered: list[Any] = []
    for row in rows:
        haystack = " ".join(str(get_field_value(row, field) or "") for field in fields).lower()
        if needle in haystack:
            filtered.append(row)
    return filtered


def render_sections(sections: Sequence[tuple[str, Any]]) -> str:
    rendered: list[str] = []
    for title, value in sections:
        rendered.append(title)
        rendered.append("=" * len(title))
        if isinstance(value, str):
            rendered.append(value)
        else:
            rendered.append(format_table(value))
        rendered.append("")
    return "\n".join(rendered).rstrip()


_SENSITIVE_KEY_PARTS = (
    "auth",
    "credential",
    "password",
    "secret",
    "token",
)


def redact_sensitive_data(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = redact_sensitive_data(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    return value
