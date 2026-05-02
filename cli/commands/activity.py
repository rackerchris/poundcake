"""Activity commands (GET /activity/suppressed)."""

from __future__ import annotations

import click

from cli.client import PoundCakeClientError
from cli.commands.common import get_client, get_output_format
from cli.utils import print_error, print_output


@click.group(name="activity")
def activity() -> None:
    """View activity records."""


@activity.command("suppressed")
@click.argument("suppression_id", type=int)
@click.pass_context
def suppressed_cmd(ctx: click.Context, suppression_id: int) -> None:
    """Show activity suppressed by a suppression (GET /activity/suppressed)."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        response = client.get_activity_suppressed(suppression_id)
        print_output(response, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Activity suppressed lookup failed: {exc}")
        raise click.Abort() from exc
