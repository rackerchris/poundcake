"""Order commands for the PoundCake CLI."""

from __future__ import annotations

from shared.types import JSONObject

import time

import click

from cli.client import PoundCakeClientError
from cli.commands.common import get_client, get_output_format
from cli.utils import (
    filter_by_search,
    print_error,
    print_output,
    print_success,
    render_sections,
    to_plain_data,
)


def _order_rows(rows: list[JSONObject]) -> list[JSONObject]:
    return [
        {
            "id": item.get("id"),
            "name": item.get("alert_group_name"),
            "status": item.get("processing_status"),
            "alert": item.get("alert_status"),
            "severity": item.get("severity"),
            "instance": item.get("instance"),
            "routes": item.get("communication_route_count"),
            "updated_at": item.get("updated_at"),
        }
        for item in rows
    ]


def _order_detail_table(order: JSONObject) -> str:
    sections: list[tuple[str, object]] = [
        (
            "Order",
            {
                "id": order.get("id"),
                "name": order.get("alert_group_name"),
                "processing_status": order.get("processing_status"),
                "alert_status": order.get("alert_status"),
                "severity": order.get("severity"),
                "instance": order.get("instance"),
                "req_id": order.get("req_id"),
                "counter": order.get("counter"),
                "remediation_outcome": order.get("remediation_outcome"),
                "auto_close_eligible": order.get("auto_close_eligible"),
                "communication_route_count": order.get("communication_route_count"),
                "clear_timeout_sec": order.get("clear_timeout_sec"),
                "clear_deadline_at": order.get("clear_deadline_at"),
                "clear_timed_out_at": order.get("clear_timed_out_at"),
                "starts_at": order.get("starts_at"),
                "ends_at": order.get("ends_at"),
                "updated_at": order.get("updated_at"),
            },
        )
    ]
    labels = order.get("labels")
    if isinstance(labels, dict) and labels:
        sections.append(("Labels", labels))
    return render_sections(sections)


def _timeline_table(payload: JSONObject) -> str:
    return render_sections(
        [
            ("Order", _order_rows([payload["order"]])[0]),
            (
                "Timeline",
                [
                    {
                        "timestamp": item.get("timestamp"),
                        "event_type": item.get("event_type"),
                        "status": item.get("status"),
                        "title": item.get("title"),
                    }
                    for item in payload.get("events") or []
                ],
            ),
        ]
    )


def _execution_history_table(rows: list[JSONObject]) -> str:
    return render_sections(
        [
            (
                "Order Execution History",
                [
                    {
                        "id": item.get("id"),
                        "dish_id": item.get("dish_id"),
                        "recipe_ingredient_id": item.get("recipe_ingredient_id"),
                        "service_type": item.get("service_type"),
                        "service_exec": item.get("service_exec"),
                        "status": item.get("service_exec_status"),
                        "attempt": item.get("attempt"),
                        "service_exec_id": item.get("service_exec_id"),
                        "started_at": item.get("service_exec_start_time"),
                        "completed_at": item.get("service_exec_completed_time"),
                    }
                    for item in rows
                ],
            )
        ]
    )


@click.group(name="orders")
def orders() -> None:
    """Inspect orders and their workflow state."""


@orders.command("list")
@click.option(
    "--processing-status",
    type=click.Choice(
        [
            "new",
            "processing",
            "resolving",
            "complete",
            "failed",
            "canceled",
        ]
    ),
    default=None,
)
@click.option("--alert-status", type=click.Choice(["firing", "resolved"]), default=None)
@click.option("--alert-group-name", default=None)
@click.option("--req-id", default=None)
@click.option(
    "--search",
    default=None,
    help="Client-side search across name, instance, severity, and request id",
)
@click.option("--limit", type=int, default=100, show_default=True)
@click.option("--offset", type=int, default=0, show_default=True)
@click.pass_context
def list_orders(
    ctx: click.Context,
    processing_status: str | None,
    alert_status: str | None,
    alert_group_name: str | None,
    req_id: str | None,
    search: str | None,
    limit: int,
    offset: int,
) -> None:
    """List orders."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        orders_data = client.list_order_statuses(
            processing_status=processing_status,
            alert_status=alert_status,
            alert_group_name=alert_group_name,
            req_id=req_id,
            limit=limit,
            offset=offset,
        )
        orders_data = filter_by_search(
            orders_data,
            search,
            ["alert_group_name", "instance", "severity", "req_id"],
        )
        if output_format == "table":
            print_output(_order_rows(to_plain_data(orders_data)), output_format)
            return
        print_output(orders_data, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Failed to list orders: {exc}")
        raise click.Abort() from exc


@orders.command("show")
@click.argument("order_id", type=int)
@click.pass_context
def show_order(ctx: click.Context, order_id: int) -> None:
    """Show an order."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = client.get_order_status(order_id)
        print_output(payload, output_format, table_renderer=_order_detail_table)
    except PoundCakeClientError as exc:
        print_error(f"Failed to show order: {exc}")
        raise click.Abort() from exc


@orders.command("timeline")
@click.argument("order_id", type=int)
@click.pass_context
def order_timeline(ctx: click.Context, order_id: int) -> None:
    """Show timeline events for an order."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = client.get_order_timeline(order_id)
        print_output(payload, output_format, table_renderer=_timeline_table)
    except PoundCakeClientError as exc:
        print_error(f"Failed to show order timeline: {exc}")
        raise click.Abort() from exc


@orders.command("watch")
@click.option(
    "--processing-status",
    type=click.Choice(
        [
            "new",
            "processing",
            "resolving",
            "complete",
            "failed",
            "canceled",
        ]
    ),
    default=None,
)
@click.option("--alert-status", type=click.Choice(["firing", "resolved"]), default=None)
@click.option("--alert-group-name", default=None)
@click.option("--req-id", default=None)
@click.option("--search", default=None)
@click.option("--limit", type=int, default=25, show_default=True)
@click.option("--offset", type=int, default=0, show_default=True)
@click.option(
    "--interval", type=int, default=5, show_default=True, help="Refresh interval in seconds"
)
@click.option("--once", is_flag=True, help="Render one refresh and exit")
@click.pass_context
def watch_orders(
    ctx: click.Context,
    processing_status: str | None,
    alert_status: str | None,
    alert_group_name: str | None,
    req_id: str | None,
    search: str | None,
    limit: int,
    offset: int,
    interval: int,
    once: bool,
) -> None:
    """Continuously refresh the order list."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        while True:
            rows = client.list_order_statuses(
                processing_status=processing_status,
                alert_status=alert_status,
                alert_group_name=alert_group_name,
                req_id=req_id,
                limit=limit,
                offset=offset,
            )
            rows = filter_by_search(
                rows, search, ["alert_group_name", "instance", "severity", "req_id"]
            )
            click.clear()
            click.echo(f"Orders (refreshed at {time.strftime('%H:%M:%S')})")
            click.echo("=" * 80)
            if output_format == "table":
                print_output(_order_rows(to_plain_data(rows)), output_format)
            else:
                print_output(rows, output_format)
            if once:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print_success("Stopped watching orders.")
    except PoundCakeClientError as exc:
        print_error(f"Failed to watch orders: {exc}")
        raise click.Abort() from exc


@orders.command("execution-history")
@click.argument("order_id", type=int)
@click.pass_context
def order_execution_history(ctx: click.Context, order_id: int) -> None:
    """Show admin execution history for all dish ingredient rows in an order."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = client.get_order_execution_history(order_id)
        print_output(payload, output_format, table_renderer=_execution_history_table)
    except PoundCakeClientError as exc:
        print_error(f"Failed to show order execution history: {exc}")
        raise click.Abort() from exc
