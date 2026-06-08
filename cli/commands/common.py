"""Shared helpers for CLI command modules."""

from __future__ import annotations

from shared.types import JSONObject

from pathlib import Path
from typing import cast

import click

from cli.client import PoundCakeClient
from cli.utils import load_data_file, require_mapping

_AUTO_AUTH_EXEMPT_COMMAND_SUFFIXES = (
    " auth login",
    " auth providers",
    " auth logout",
    " api request",
    " api get",
    " api post",
    " api put",
    " api patch",
    " api delete",
    " ready",
    " health",
)


def get_client(ctx: click.Context) -> PoundCakeClient:
    client = cast(PoundCakeClient, ctx.obj["client"])
    command_path = str(getattr(ctx, "command_path", "") or "")
    if not any(command_path.endswith(suffix) for suffix in _AUTO_AUTH_EXEMPT_COMMAND_SUFFIXES):
        client.ensure_authenticated()
    return client


def get_output_format(ctx: click.Context) -> str:
    return cast(str, ctx.obj["format"])


def read_mapping_file(path: Path, label: str) -> JSONObject:
    return require_mapping(load_data_file(path), label)


def compact_update_payload(payload: JSONObject) -> JSONObject:
    return {key: value for key, value in payload.items() if value is not None}
