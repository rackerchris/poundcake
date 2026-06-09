#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""Main CLI entry point for PoundCake CLI."""

from __future__ import annotations

import sys
from typing import Optional

import click

from cli.client import PoundCakeClient
from cli.commands import (
    api,
    auth,
    comm_policy,
    communications,
    dishes,
    ingredients,
    observability,
    orders,
    overview,
    settings,
    scheduled_tasks,
    suppressions,
    recipes,
)

# New command modules for E2E test support
from cli.commands import webhook as webhook_cmd
from cli.commands import plugin as plugin_cmd
from cli.commands import health as health_cmd
from cli.commands import activity as activity_cmd


@click.group()
@click.option(
    "--url",
    "-u",
    envvar="POUNDCAKE_URL",
    default="http://localhost:8080",
    help="PoundCake API URL",
)
@click.option(
    "--token",
    "-t",
    envvar="POUNDCAKE_TOKEN",
    help="PoundCake session token for authentication",
)
@click.option(
    "--username",
    envvar="POUNDCAKE_USERNAME",
    help="Username for password-based auto-login",
)
@click.option(
    "--password",
    envvar="POUNDCAKE_PASSWORD",
    help="Password for password-based auto-login",
)
@click.option(
    "--format",
    "-f",
    type=click.Choice(["json", "yaml", "table"]),
    default="table",
    help="Output format",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose output",
)
@click.option(
    "--webhook-token",
    envvar="POUNDCAKE_WEBHOOK_TOKEN",
    default=None,
    help="Bearer token for webhook POST endpoints",
)
@click.pass_context
def cli(
    ctx: click.Context,
    url: str,
    token: Optional[str],
    username: Optional[str],
    password: Optional[str],
    format: str,
    verbose: bool,
    webhook_token: Optional[str],
) -> None:
    """PoundCake CLI - operate orders, recipes, and communications."""
    ctx.ensure_object(dict)

    ctx.obj["client"] = PoundCakeClient(
        url,
        token,
        username=username,
        password=password,
        webhook_token=webhook_token,
    )
    ctx.obj["format"] = format
    ctx.obj["verbose"] = verbose


cli.add_command(auth.auth)
cli.add_command(api.api)
cli.add_command(overview.overview)
cli.add_command(orders.orders)
cli.add_command(communications.communications)
cli.add_command(suppressions.suppressions)
cli.add_command(dishes.dishes)
cli.add_command(comm_policy.comm_policy)
cli.add_command(recipes.recipes)
cli.add_command(ingredients.ingredients)
cli.add_command(settings.settings)
cli.add_command(observability.observability)
cli.add_command(health_cmd.ready_cmd)
cli.add_command(health_cmd.health_cmd)
cli.add_command(health_cmd.health_status_cmd)
cli.add_command(webhook_cmd.webhook)
cli.add_command(plugin_cmd.plugins)
cli.add_command(activity_cmd.activity)
cli.add_command(scheduled_tasks.scheduled_tasks)


def main() -> None:
    """Main entry point for the CLI."""
    try:
        cli(obj={})
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
