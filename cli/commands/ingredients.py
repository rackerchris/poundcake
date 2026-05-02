"""Read-only ingredient template commands for the PoundCake CLI."""

from __future__ import annotations

from shared.types import JSONObject


import click

from cli.client import PoundCakeClientError
from cli.commands.common import get_client, get_output_format
from cli.utils import print_error, print_output, render_sections


def _ingredient_row(item: JSONObject) -> list[JSONObject]:
    return {
        "id": item.get("id"),
        "service_type": item.get("service_type"),
        "service_exec": item.get("service_exec"),
        "purpose": item.get("ingredient_purpose"),
        "is_active": item.get("is_active"),
        "updated_at": item.get("updated_at"),
    }


def _ingredient_detail_table(item: JSONObject) -> str:
    return render_sections(
        [
            (
                "Ingredient",
                {
                    "id": item.get("id"),
                    "service_type": item.get("service_type"),
                    "service_exec": item.get("service_exec"),
                    "destination_target": item.get("destination_target"),
                    "task_key_template": item.get("task_key_template"),
                    "purpose": item.get("ingredient_purpose"),
                    "is_active": item.get("is_active"),
                    "is_blocking": item.get("is_blocking"),
                    "default_expected_secs": item.get("default_expected_secs"),
                    "default_timeout": item.get("default_timeout"),
                    "retry_count": item.get("retry_count"),
                    "retry_delay": item.get("retry_delay"),
                    "on_failure": item.get("on_failure"),
                    "updated_at": item.get("updated_at"),
                },
            ),
        ]
    )


@click.group(name="ingredients")
def ingredients() -> None:
    """Inspect registered service ingredient templates."""


@ingredients.command("list")
@click.option("--service-type", default=None)
@click.option("--limit", type=int, default=100, show_default=True)
@click.option("--offset", type=int, default=0, show_default=True)
@click.pass_context
def list_ingredients(
    ctx: click.Context,
    service_type: str | None,
    limit: int,
    offset: int,
) -> None:
    """List registered ingredient templates."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        items = client.list_ingredients(service_type=service_type, limit=limit, offset=offset)
        rows = [_ingredient_row(item) for item in items]
        if output_format == "table":
            print_output(rows, output_format)
            return
        print_output(rows, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Failed to list ingredients: {exc}")
        raise click.Abort() from exc


@ingredients.command("show")
@click.argument("ingredient_id", type=int)
@click.pass_context
def show_ingredient(ctx: click.Context, ingredient_id: int) -> None:
    """Show details for a single ingredient template."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        item = client.get_ingredient(ingredient_id)
        print_output(item, output_format, table_renderer=_ingredient_detail_table)
    except PoundCakeClientError as exc:
        print_error(f"Failed to show ingredient: {exc}")
        raise click.Abort() from exc
