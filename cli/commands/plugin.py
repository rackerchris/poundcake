"""Plugin management commands (GET /plugins/{type}/health, PUT /plugins/{type}/configuration)."""

from __future__ import annotations

import json
from pathlib import Path

import click

from cli.client import PoundCakeClientError
from cli.commands.common import get_client, get_output_format
from cli.utils import (
    load_data_file,
    parse_json_object,
    parse_json_value,
    print_error,
    print_output,
)


@click.group(name="plugin")
def plugin() -> None:
    """Manage plugin configuration (routes: /plugins/{type}/health, /plugins/{type}/configuration)."""


@plugin.command("health")
@click.argument("service_type")
@click.pass_context
def health_cmd(ctx: click.Context, service_type: str) -> None:
    """GET plugin health for a service type (GET /plugins/{service_type}/health)."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        response = client.api_request("GET", f"plugins/{service_type}/health")
        print_output(response, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Plugin health check failed for {service_type}: {exc}")
        raise click.Abort() from exc


@plugin.command("configuration")
@click.argument("service_type")
@click.option(
    "--config-json",
    default=None,
    help="JSON configuration object for PUT /plugins/{type}/configuration",
)
@click.option(
    "--config-file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="JSON file with configuration for PUT /plugins/{type}/configuration",
)
@click.option("--output-json", is_flag=True, help="Output result as raw JSON for piping")
@click.pass_context
def configure_cmd(
    ctx: click.Context,
    service_type: str,
    config_json: str | None,
    config_file: Path | None,
    output_json: bool,
) -> None:
    """PUT plugin configuration for a service type."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)

    if config_file is not None:
        cfg = load_data_file(config_file)
    elif config_json:
        cfg = parse_json_object(config_json, "config-json")
    else:
        raise click.BadParameter("Provide --config-json or --config-file")

    if not isinstance(cfg, dict):
        raise click.BadParameter("Configuration must be a JSON object")

    try:
        response = client.configure_plugin_config(service_type, cfg)
        if output_json:
            click.echo(json.dumps(response, indent=2, default=str))
        else:
            print_output(response, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Plugin configuration failed for {service_type}: {exc}")
        raise click.Abort() from exc
