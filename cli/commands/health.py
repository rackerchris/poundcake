"""Health check commands (GET /ready, GET /health)."""

from __future__ import annotations

import click

from cli.client import PoundCakeClientError
from cli.commands.common import get_client, get_output_format
from cli.utils import print_error, print_output


@click.command("ready")
@click.pass_context
def ready_cmd(ctx: click.Context) -> None:
    """Check if the PoundCake API is ready (GET /ready)."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        response = client.ready()
        print_output(response, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Readiness check failed: {exc}")
        raise click.Abort() from exc


@click.command("health")
@click.pass_context
def health_cmd(ctx: click.Context) -> None:
    """Get full health status (GET /health)."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        response = client.health()
        print_output(response, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Health check failed: {exc}")
        raise click.Abort() from exc
