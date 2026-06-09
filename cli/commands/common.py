"""Shared helpers for CLI command modules."""

from __future__ import annotations

from shared.types import JSONObject

from pathlib import Path
from typing import Any, cast

import click
from pydantic import BaseModel as PydanticModel

from cli.client import PoundCakeClient
from cli.utils import (
    build_diff_rows,
    load_data_file,
    print_output,
    redact_sensitive_data,
    render_sections,
    require_mapping,
)

_AUTO_AUTH_EXEMPT_COMMAND_SUFFIXES = (
    " auth login",
    " auth providers",
    " auth logout",
    " api request",
    " api get",
    " api post",
    " api put",
    " api patch",
    " api delete",
    " ready",
)


def get_client(ctx: click.Context) -> PoundCakeClient:
    client = cast(PoundCakeClient, ctx.obj["client"])
    command_path = str(getattr(ctx, "command_path", "") or "")
    if not any(command_path.endswith(suffix) for suffix in _AUTO_AUTH_EXEMPT_COMMAND_SUFFIXES):
        client.ensure_authenticated()
    return client


def get_output_format(ctx: click.Context) -> str:
    return cast(str, ctx.obj["format"])


def read_mapping_file(path: Path, label: str) -> JSONObject:
    return require_mapping(load_data_file(path), label)


def compact_update_payload(payload: JSONObject) -> JSONObject:
    return {key: value for key, value in payload.items() if value is not None}


def validate_request_payload(
    client: PoundCakeClient,
    payload: JSONObject,
    model: type[PydanticModel],
    context: str,
) -> JSONObject:
    return client._validate_request_payload(payload, model, context)


def print_dry_run_preview(
    ctx: click.Context,
    *,
    command: str,
    target: str,
    payload: JSONObject,
    summary: JSONObject,
    impact: str,
    changes: list[dict[str, str]] | None = None,
    redact: bool = False,
) -> None:
    output_format = get_output_format(ctx)
    rendered_payload: Any = redact_sensitive_data(payload) if redact else payload
    body = {
        "dry_run": True,
        "command": command,
        "target": target,
        "impact": impact,
        "summary": summary,
        "changes": changes or [],
        "payload": rendered_payload,
    }
    if output_format == "table":
        click.echo(
            render_sections(
                [
                    (
                        "Dry Run",
                        {
                            "command": command,
                            "target": target,
                            "impact": impact,
                        },
                    ),
                    ("Summary", summary),
                ]
                + ([("Changes", changes)] if changes else [])
                + [("Payload", rendered_payload)]
            )
        )
        return
    print_output(body, output_format)


def merge_preview_state(current: JSONObject, update: JSONObject) -> JSONObject:
    return {**current, **update}


def build_preview_changes(
    current: JSONObject,
    next: JSONObject,
    *,
    labels: dict[str, str] | None = None,
    redact: bool = False,
) -> list[dict[str, str]]:
    rendered_current = redact_sensitive_data(current) if redact else current
    rendered_next = redact_sensitive_data(next) if redact else next
    return build_diff_rows(rendered_current, rendered_next, labels=labels)
