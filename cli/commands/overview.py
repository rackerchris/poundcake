"""Overview command for the PoundCake CLI."""

from __future__ import annotations

import click

from cli.client import PoundCakeClientError
from cli.commands.common import get_client, get_output_format
from cli.utils import print_error, print_output, render_sections


def _overview_table(payload: dict) -> str:
    health = payload["health"]
    overview = payload["overview"]
    sections = [
        (
            "Summary",
            {
                "platform_status": health.get("status"),
                "version": health.get("version"),
                "orders_new": overview.get("queue", {}).get("orders_new"),
                "orders_processing": overview.get("queue", {}).get("orders_processing"),
                "failed_orders": overview.get("failures", {}).get("orders_failed"),
                "failed_dishes": overview.get("failures", {}).get("dishes_failed"),
                "active_suppressions": overview.get("suppressions", {}).get("active"),
            },
        ),
        (
            "Recent Orders",
            [
                {
                    "id": item.get("id"),
                    "name": item.get("alert_group_name"),
                    "status": item.get("processing_status"),
                    "alert": item.get("alert_status"),
                    "severity": item.get("severity"),
                }
                for item in payload["incidents"][:8]
            ],
        ),
        (
            "Recent Communications",
            [
                {
                    "id": item.get("communication_id"),
                    "reference": item.get("reference_name") or item.get("reference_id"),
                    "channel": item.get("channel"),
                    "status": item.get("remote_state") or item.get("lifecycle_state"),
                    "destination": item.get("destination"),
                }
                for item in payload["communications"][:8]
            ],
        ),
        (
            "Recent Activity",
            [
                {
                    "type": item.get("type"),
                    "title": item.get("title"),
                    "status": item.get("status"),
                    "target": item.get("target_id"),
                }
                for item in payload["activity"][:10]
            ],
        ),
        (
            "Recent Suppressions",
            [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "status": item.get("status"),
                    "ends_at": item.get("ends_at"),
                }
                for item in payload["suppressions"][:8]
            ],
        ),
    ]
    return render_sections(sections)


@click.command("overview")
@click.option(
    "--activity-limit",
    type=int,
    default=10,
    show_default=True,
    help="Recent activity records to fetch",
)
@click.option(
    "--order-limit", type=int, default=8, show_default=True, help="Recent orders to fetch"
)
@click.option(
    "--communication-limit",
    type=int,
    default=8,
    show_default=True,
    help="Recent communications to fetch",
)
@click.option(
    "--suppression-limit",
    type=int,
    default=8,
    show_default=True,
    help="Recent suppressions to fetch",
)
@click.pass_context
def overview(
    ctx: click.Context,
    activity_limit: int,
    order_limit: int,
    communication_limit: int,
    suppression_limit: int,
) -> None:
    """Show the monitoring overview dashboard in the CLI."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = {
            "health": client.health(),
            "overview": client.observability_overview(),
            "activity": client.list_observability_activity(limit=activity_limit),
            "incidents": client.list_order_statuses(limit=order_limit),
            "communications": client.list_communications(limit=communication_limit),
            "suppressions": client.list_suppressions(limit=suppression_limit),
        }
        print_output(payload, output_format, table_renderer=_overview_table)
    except PoundCakeClientError as exc:
        print_error(f"Failed to load overview: {exc}")
        raise click.Abort() from exc
