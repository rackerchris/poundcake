"""Webhook commands — POST alerts to the PoundCake webhook endpoint (POST /api/v1/webhook)."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from cli.client import PoundCakeClientError
from cli.commands.common import get_client, get_output_format
from cli.utils import parse_json_object, print_error, print_output


@click.group(name="webhook")
def webhook() -> None:
    """Post alerts via the webhook endpoint (POST /webhook)."""


@webhook.command("post")
@click.argument("payload_source", default="-", required=False)
@click.option(
    "--file",
    "-f",
    type=click.Path(exists=True, path_type=Path, readable=True),
    default=None,
    help="JSON payload file (Alertmanager format)",
)
@click.option(
    "--order-id-only",
    is_flag=True,
    default=False,
    help="Output only the order ID to stdout",
)
@click.pass_context
def post_cmd(
    ctx: click.Context,
    payload_source: str,
    file: Path | None,
    order_id_only: bool,
) -> None:
    """POST a webhook payload to trigger a remediation order (POST /api/v1/webhook)."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)

    # Resolve payload from file, argument, or stdin
    if file is not None:
        data = file.read_text()
    elif payload_source == "-":
        data = sys.stdin.read()
    else:
        data = payload_source

    payload_dict = parse_json_object(data, "payload")
    if not payload_dict:
        raise click.BadParameter("Payload must be valid JSON")

    try:
        response = client.post_webhook(payload_dict)
        if order_id_only:
            for key in ("order_id", "id"):
                if key in response:
                    click.echo(response[key])
                    return
            results = response.get("results", [])
            if isinstance(results, list) and results:
                click.echo(results[0].get("order_id") or results[0].get("id"))
            else:
                click.echo(response.get("order_id") or response.get("id", ""))
        else:
            print_output(response, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Webhook POST failed ({client.base_url}/api/v1/webhook): {exc}")
        raise click.Abort() from exc
