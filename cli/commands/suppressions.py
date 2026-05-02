"""Suppression commands for the PoundCake CLI."""

from __future__ import annotations

from shared.types import JSONObject

import getpass
from typing import Any

import click

from cli.client import PoundCakeClientError
from cli.commands.common import get_client, get_output_format
from cli.utils import parse_json_object, print_error, print_output, render_sections, to_plain_data


def _suppression_rows(rows: list[JSONObject]) -> list[JSONObject]:
    return [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "status": item.get("status"),
            "scope": item.get("scope"),
            "enabled": item.get("enabled"),
            "starts_at": item.get("starts_at"),
            "ends_at": item.get("ends_at"),
        }
        for item in rows
    ]


def _suppression_detail_table(item: JSONObject) -> str:
    sections: list[tuple[str, Any]] = [
        (
            "Suppression",
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "status": item.get("status"),
                "scope": item.get("scope"),
                "enabled": item.get("enabled"),
                "starts_at": item.get("starts_at"),
                "ends_at": item.get("ends_at"),
                "reason": item.get("reason"),
                "created_by": item.get("created_by"),
                "summary_ticket_enabled": item.get("summary_ticket_enabled"),
            },
        ),
        (
            "Matchers",
            [
                {
                    "label_key": matcher.get("label_key"),
                    "operator": matcher.get("operator"),
                    "value": matcher.get("value"),
                }
                for matcher in item.get("matchers") or []
            ],
        ),
    ]
    counters = item.get("counters")
    if counters:
        sections.append(("Counters", counters))
    summary = item.get("summary")
    if summary:
        sections.append(("Summary", summary))
    return render_sections(sections)


def _build_matchers(
    matcher_key: str | None,
    matcher_operator: str | None,
    matcher_value: str | None,
    matcher_json: tuple[str, ...],
) -> list[JSONObject]:
    matchers: list[JSONObject] = []
    if matcher_key:
        matchers.append(
            {
                "label_key": matcher_key,
                "operator": matcher_operator or "eq",
                "value": matcher_value,
            }
        )
    for raw in matcher_json:
        matchers.append(parse_json_object(raw, "matcher-json") or {})
    return matchers


@click.group(name="suppressions")
def suppressions() -> None:
    """Manage suppression windows."""


@suppressions.command("list")
@click.option(
    "--status", type=click.Choice(["scheduled", "active", "expired", "canceled"]), default=None
)
@click.option("--enabled/--disabled", default=None)
@click.option("--scope", type=click.Choice(["all", "matchers"]), default=None)
@click.option("--limit", type=int, default=100, show_default=True)
@click.option("--offset", type=int, default=0, show_default=True)
@click.pass_context
def list_suppressions(
    ctx: click.Context,
    status: str | None,
    enabled: bool | None,
    scope: str | None,
    limit: int,
    offset: int,
) -> None:
    """List suppressions."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = client.list_suppressions(
            status=status, enabled=enabled, scope=scope, limit=limit, offset=offset
        )
        if output_format == "table":
            print_output(_suppression_rows(to_plain_data(payload)), output_format)
            return
        print_output(payload, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Failed to list suppressions: {exc}")
        raise click.Abort() from exc


@suppressions.command("show")
@click.argument("suppression_id", type=int)
@click.pass_context
def show_suppression(ctx: click.Context, suppression_id: int) -> None:
    """Show a suppression."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = client.get_suppression(suppression_id)
        print_output(payload, output_format, table_renderer=_suppression_detail_table)
    except PoundCakeClientError as exc:
        print_error(f"Failed to show suppression: {exc}")
        raise click.Abort() from exc


@suppressions.command("create")
@click.option("--name", required=True)
@click.option("--starts-at", required=True, help="ISO-8601 start time")
@click.option("--ends-at", required=True, help="ISO-8601 end time")
@click.option(
    "--scope", type=click.Choice(["all", "matchers"]), default="matchers", show_default=True
)
@click.option("--reason", default=None)
@click.option("--created-by", default=None)
@click.option("--summary-ticket-enabled/--summary-ticket-disabled", default=True, show_default=True)
@click.option("--matcher-key", default=None)
@click.option("--matcher-operator", default="eq", show_default=True)
@click.option("--matcher-value", default=None)
@click.option("--matcher-json", multiple=True, help="JSON object matcher payload")
@click.pass_context
def create_suppression(
    ctx: click.Context,
    name: str,
    starts_at: str,
    ends_at: str,
    scope: str,
    reason: str | None,
    created_by: str | None,
    summary_ticket_enabled: bool,
    matcher_key: str | None,
    matcher_operator: str,
    matcher_value: str | None,
    matcher_json: tuple[str, ...],
) -> None:
    """Create a suppression window."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        matchers = _build_matchers(matcher_key, matcher_operator, matcher_value, matcher_json)
        if scope == "matchers" and not matchers:
            raise click.BadParameter("At least one matcher is required when scope=matchers")
        payload = {
            "name": name,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "scope": scope,
            "matchers": matchers,
            "reason": reason,
            "created_by": created_by or getpass.getuser() or "cli",
            "summary_ticket_enabled": summary_ticket_enabled,
            "enabled": True,
        }
        response = client.create_suppression(payload)
        print_output(response, output_format, table_renderer=_suppression_detail_table)
    except (click.BadParameter, PoundCakeClientError) as exc:
        print_error(f"Failed to create suppression: {exc}")
        raise click.Abort() from exc


@suppressions.command("cancel")
@click.argument("suppression_id", type=int)
@click.pass_context
def cancel_suppression(ctx: click.Context, suppression_id: int) -> None:
    """Cancel a suppression window."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = client.cancel_suppression(suppression_id)
        print_output(payload, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Failed to cancel suppression: {exc}")
        raise click.Abort() from exc
