"""Observability activity commands for the PoundCake CLI."""

from __future__ import annotations

import click

from cli.client import PoundCakeClientError
from cli.commands.common import get_client, get_output_format
from cli.utils import print_error, print_output, to_plain_data


def _activity_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "type": item.get("type"),
            "status": item.get("status"),
            "title": item.get("title"),
            "target_kind": item.get("target_kind"),
            "target_id": item.get("target_id"),
            "timestamp": item.get("timestamp"),
        }
        for item in rows
    ]


@click.group(name="observability")
def observability() -> None:
    """Inspect observability activity feeds."""


@observability.command("activity")
@click.option("--type", "activity_type", default=None, help="Filter by activity type")
@click.option("--limit", type=int, default=50, show_default=True)
@click.option("--offset", type=int, default=0, show_default=True)
@click.pass_context
def activity(ctx: click.Context, activity_type: str | None, limit: int, offset: int) -> None:
    """List detailed observability activity records."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = client.list_observability_activity_records(
            activity_type=activity_type,
            limit=limit,
            offset=offset,
        )
        if output_format == "table":
            print_output(_activity_rows(to_plain_data(payload)), output_format)
            return
        print_output(payload, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Failed to list observability activity: {exc}")
        raise click.Abort() from exc
