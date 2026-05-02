"""Dish execution commands for the PoundCake CLI."""

from __future__ import annotations

from shared.types import JSONObject


import click

from cli.client import PoundCakeClientError
from cli.commands.common import get_client, get_output_format
from cli.utils import filter_by_search, print_error, print_output, render_sections, to_plain_data


def _dish_rows(rows: list[JSONObject]) -> list[JSONObject]:
    return [
        {
            "id": item.get("id"),
            "recipe": item.get("recipe_name") or f"Recipe #{item.get('recipe_id')}",
            "phase": item.get("run_phase"),
            "processing_status": item.get("processing_status"),
            "execution_status": item.get("dish_exec_status"),
            "order_id": item.get("order_id"),
            "updated_at": item.get("updated_at"),
        }
        for item in rows
    ]


def _dish_detail_table(payload: JSONObject) -> str:
    item = payload.get("dish") or {}
    return render_sections(
        [
            (
                "Dish",
                {
                    "id": item.get("id"),
                    "recipe": item.get("recipe_name") or f"Recipe #{item.get('recipe_id')}",
                    "order_id": item.get("order_id"),
                    "phase": item.get("run_phase"),
                    "processing_status": item.get("processing_status"),
                    "execution_status": item.get("dish_exec_status"),
                    "expected_duration_sec": item.get("expected_run_secs"),
                    "actual_duration_sec": item.get("run_time_secs"),
                    "started_at": item.get("started_at"),
                    "completed_at": item.get("completed_at"),
                    "updated_at": item.get("updated_at"),
                },
            ),
            (
                "Ingredient Status",
                [
                    {
                        "id": row.get("id"),
                        "ingredient_id": row.get("ingredient_id"),
                        "service_type": row.get("service_type"),
                        "service_exec": row.get("service_exec"),
                        "status": row.get("service_exec_status"),
                        "step_order": row.get("step_order"),
                        "parallel_group": row.get("parallel_group"),
                    }
                    for row in payload.get("ingredient_status") or []
                ],
            ),
        ]
    )


@click.group(name="dishes")
def dishes() -> None:
    """Inspect dish execution."""


@dishes.command("list")
@click.option(
    "--processing-status",
    type=click.Choice(
        [
            "new",
            "processing",
            "finalizing",
            "complete",
            "failed",
            "errored",
            "timeout",
            "canceled",
        ]
    ),
    default=None,
)
@click.option("--order-id", type=int, default=None)
@click.option("--phase", default=None, help="Client-side run phase filter")
@click.option(
    "--search",
    default=None,
    help="Client-side search across recipe, order id, and run phase",
)
@click.option("--limit", type=int, default=100, show_default=True)
@click.option("--offset", type=int, default=0, show_default=True)
@click.pass_context
def list_dishes(
    ctx: click.Context,
    processing_status: str | None,
    order_id: int | None,
    phase: str | None,
    search: str | None,
    limit: int,
    offset: int,
) -> None:
    """List dishes."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        rows = client.list_dish_statuses(
            processing_status=processing_status,
            order_id=order_id,
            limit=limit,
            offset=offset,
        )
        if phase:
            rows = [item for item in rows if str(item.run_phase or "") == phase]
        rows = filter_by_search(
            rows,
            search,
            ["run_phase", "recipe_name", "order_id"],
        )
        if output_format == "table":
            print_output(_dish_rows(to_plain_data(rows)), output_format)
            return
        print_output(rows, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Failed to list dishes: {exc}")
        raise click.Abort() from exc


@dishes.command("show")
@click.argument("dish_id", type=int)
@click.pass_context
def show_dish(ctx: click.Context, dish_id: int) -> None:
    """Show a dish."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = {
            "dish": client.get_dish_status(dish_id),
            "ingredient_status": client.get_dish_ingredient_status(dish_id),
        }
        print_output(payload, output_format, table_renderer=_dish_detail_table)
    except PoundCakeClientError as exc:
        print_error(f"Failed to show dish: {exc}")
        raise click.Abort() from exc
