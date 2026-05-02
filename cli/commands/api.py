"""Low-level API request commands for the PoundCake CLI."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from cli.client import PoundCakeClientError
from cli.commands.common import get_client, get_output_format
from cli.utils import parse_json_object, print_error, print_output


def _parse_headers(values: tuple[str, ...]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw in values:
        key, separator, value = raw.partition(":")
        if not separator or not key.strip():
            raise click.BadParameter("Each --header value must use the form 'Name: value'")
        headers[key.strip()] = value.strip()
    return headers


def _resolve_body(body_json: str | None, body_file) -> object | None:
    if body_file is not None:
        if body_file == sys.stdin:
            text = sys.stdin.read()
        else:
            text = body_file.read_text()
        return parse_json_object(text, "body-file")
    return parse_json_object(body_json, "body-json")


def _request_api(
    ctx: click.Context,
    *,
    method: str,
    path: str,
    body_json: str | None,
    body_file: str | Path | object | None,
    query_json: str | None,
    header: tuple[str, ...],
    no_session: bool,
) -> None:
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = client.api_request(
            method,
            path,
            json=_resolve_body(body_json, body_file),
            params=parse_json_object(query_json, "query-json"),
            use_session=not no_session,
            extra_headers=_parse_headers(header),
        )
        print_output(payload, output_format)
    except (click.BadParameter, PoundCakeClientError) as exc:
        print_error(f"API request failed: {exc}")
        raise click.Abort() from exc


@click.group(name="api")
def api() -> None:
    """Run direct PoundCake API requests through the CLI auth/session layer."""


@api.command("request")
@click.argument("method")
@click.argument("path")
@click.option("--body-json", default=None, help="JSON object request body")
@click.option(
    "--body-file",
    type=click.Path(exists=True, path_type=Path, allow_dash=True),
    default=None,
    help="File containing request body JSON (takes precedence over --body-json)",
)
@click.option("--query-json", default=None, help="JSON object query parameters")
@click.option("--header", multiple=True, help="Extra request header in 'Name: value' form")
@click.option(
    "--no-session",
    is_flag=True,
    help="Skip CLI session auth for public or route-level-token endpoints",
)
@click.pass_context
def request_cmd(
    ctx: click.Context,
    method: str,
    path: str,
    body_json: str | None,
    body_file: Path | None,
    query_json: str | None,
    header: tuple[str, ...],
    no_session: bool,
) -> None:
    """Run a direct API request."""
    _request_api(
        ctx,
        method=method,
        path=path,
        body_json=body_json,
        body_file=body_file,
        query_json=query_json,
        header=header,
        no_session=no_session,
    )


def _verb_command(name: str, http_method: str) -> click.Command:
    @click.command(name=name)
    @click.argument("path")
    @click.option("--body-json", default=None, help="JSON object request body")
    @click.option(
        "--body-file",
        type=click.Path(exists=True, path_type=Path, allow_dash=True),
        default=None,
        help="File containing request body JSON (takes precedence over --body-json)",
    )
    @click.option("--query-json", default=None, help="JSON object query parameters")
    @click.option("--header", multiple=True, help="Extra request header in 'Name: value' form")
    @click.option(
        "--no-session",
        is_flag=True,
        help="Skip CLI session auth for public or route-level-token endpoints",
    )
    @click.pass_context
    def _command(
        ctx: click.Context,
        path: str,
        body_json: str | None,
        body_file: Path | None,
        query_json: str | None,
        header: tuple[str, ...],
        no_session: bool,
    ) -> None:
        _request_api(
            ctx,
            method=http_method,
            path=path,
            body_json=body_json,
            body_file=body_file,
            query_json=query_json,
            header=header,
            no_session=no_session,
        )

    return _command


for _name, _method in (
    ("get", "GET"),
    ("post", "POST"),
    ("put", "PUT"),
    ("patch", "PATCH"),
    ("delete", "DELETE"),
):
    api.add_command(_verb_command(_name, _method))
