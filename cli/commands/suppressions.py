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
                "source": item.get("source"),
                "source_service_type": item.get("source_service_type"),
                "source_ref": item.get("source_ref"),
                "last_synced_at": item.get("last_synced_at"),
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


def _matchers_from_order_labels(
    labels: JSONObject, label_keys: tuple[str, ...]
) -> list[JSONObject]:
    matchers: list[JSONObject] = []
    missing: list[str] = []
    for label_key in label_keys:
        key = str(label_key or "").strip()
        if not key:
            continue
        if key not in labels:
            missing.append(key)
            continue
        value = labels.get(key)
        if value is None:
            missing.append(key)
            continue
        matchers.append(
            {
                "label_key": key,
                "operator": "eq",
                "value": str(value),
            }
        )
    if missing:
        raise click.BadParameter(
            "Order is missing requested label keys: " + ", ".join(sorted(set(missing)))
        )
    if not matchers:
        raise click.BadParameter("At least one label key is required")
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


@suppressions.command("status")
@click.option(
    "--status", type=click.Choice(["scheduled", "active", "expired", "canceled"]), default=None
)
@click.option("--enabled/--disabled", default=None)
@click.option("--scope", type=click.Choice(["all", "matchers"]), default=None)
@click.option("--limit", type=int, default=100, show_default=True)
@click.option("--offset", type=int, default=0, show_default=True)
@click.pass_context
def list_suppression_statuses(
    ctx: click.Context,
    status: str | None,
    enabled: bool | None,
    scope: str | None,
    limit: int,
    offset: int,
) -> None:
    """List reader-safe suppression status rows."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = client.list_suppression_statuses(
            status=status,
            enabled=enabled,
            scope=scope,
            limit=limit,
            offset=offset,
        )
        if output_format == "table":
            print_output(_suppression_rows(to_plain_data(payload)), output_format)
            return
        print_output(payload, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Failed to list suppression status: {exc}")
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
        if not matchers:
            raise click.BadParameter("At least one matcher is required")
        payload = {
            "name": name,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "matchers": matchers,
            "reason": reason,
            "created_by": created_by or getpass.getuser() or "cli",
            "summary_ticket_enabled": summary_ticket_enabled,
        }
        response = client.create_suppression(payload)
        print_output(response, output_format, table_renderer=_suppression_detail_table)
    except (click.BadParameter, PoundCakeClientError) as exc:
        print_error(f"Failed to create suppression: {exc}")
        raise click.Abort() from exc


@suppressions.command("from-order")
@click.argument("order_id", type=int)
@click.option("--name", required=True)
@click.option("--starts-at", required=True, help="ISO-8601 start time")
@click.option("--ends-at", required=True, help="ISO-8601 end time")
@click.option(
    "--label-key",
    "label_keys",
    multiple=True,
    required=True,
    help="Order label key to copy into an eq matcher; repeat to add more",
)
@click.option("--reason", default=None)
@click.option("--created-by", default=None)
@click.option("--summary-ticket-enabled/--summary-ticket-disabled", default=True, show_default=True)
@click.pass_context
def create_suppression_from_order(
    ctx: click.Context,
    order_id: int,
    name: str,
    starts_at: str,
    ends_at: str,
    label_keys: tuple[str, ...],
    reason: str | None,
    created_by: str | None,
    summary_ticket_enabled: bool,
) -> None:
    """Create a suppression using label values copied from an existing order."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        labels = client.get_order_labels(order_id)
        matchers = _matchers_from_order_labels(labels, label_keys)
        payload = {
            "name": name,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "matchers": matchers,
            "reason": reason,
            "created_by": created_by or getpass.getuser() or "cli",
            "summary_ticket_enabled": summary_ticket_enabled,
        }
        response = client.create_suppression(payload)
        print_output(response, output_format, table_renderer=_suppression_detail_table)
    except (click.BadParameter, PoundCakeClientError) as exc:
        print_error(f"Failed to create suppression from order: {exc}")
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


@suppressions.command("stats")
@click.argument("suppression_id", type=int)
@click.pass_context
def suppression_stats(ctx: click.Context, suppression_id: int) -> None:
    """Show suppression counters."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = client.get_suppression_stats(suppression_id)
        print_output(payload, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Failed to load suppression stats: {exc}")
        raise click.Abort() from exc


@suppressions.command("update")
@click.argument("suppression_id", type=int)
@click.option("--name", default=None)
@click.option("--starts-at", default=None, help="ISO-8601 start time")
@click.option("--ends-at", default=None, help="ISO-8601 end time")
@click.option("--reason", default=None)
@click.option("--summary-ticket-enabled/--summary-ticket-disabled", default=None)
@click.option("--matcher-key", default=None)
@click.option("--matcher-operator", default="eq", show_default=True)
@click.option("--matcher-value", default=None)
@click.option("--matcher-json", multiple=True, help="JSON object matcher payload")
@click.pass_context
def update_suppression(
    ctx: click.Context,
    suppression_id: int,
    name: str | None,
    starts_at: str | None,
    ends_at: str | None,
    reason: str | None,
    summary_ticket_enabled: bool | None,
    matcher_key: str | None,
    matcher_operator: str,
    matcher_value: str | None,
    matcher_json: tuple[str, ...],
) -> None:
    """Update a suppression window."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload: JSONObject = {}
        if name is not None:
            payload["name"] = name
        if starts_at is not None:
            payload["starts_at"] = starts_at
        if ends_at is not None:
            payload["ends_at"] = ends_at
        if reason is not None:
            payload["reason"] = reason
        if summary_ticket_enabled is not None:
            payload["summary_ticket_enabled"] = summary_ticket_enabled
        if matcher_key or matcher_json:
            payload["matchers"] = _build_matchers(
                matcher_key,
                matcher_operator,
                matcher_value,
                matcher_json,
            )
        if not payload:
            raise click.BadParameter("No update fields provided")
        response = client.update_suppression(suppression_id, payload)
        print_output(response, output_format, table_renderer=_suppression_detail_table)
    except (click.BadParameter, PoundCakeClientError) as exc:
        print_error(f"Failed to update suppression: {exc}")
        raise click.Abort() from exc
