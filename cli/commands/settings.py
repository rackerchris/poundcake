"""Settings commands for the PoundCake CLI."""

from __future__ import annotations

import click

from cli.client import PoundCakeClientError
from cli.commands.common import get_client, get_output_format
from cli.utils import print_error, print_output, render_sections


def _settings_table(payload: dict[str, object]) -> str:
    return render_sections(
        [
            (
                "Settings",
                {
                    "version": payload.get("version"),
                    "auth_enabled": payload.get("auth_enabled"),
                    "rbac_enabled": payload.get("rbac_enabled"),
                    "global_communications_configured": payload.get("global_communications_configured"),
                    "prometheus_use_crds": payload.get("prometheus_use_crds"),
                    "prometheus_crd_namespace": payload.get("prometheus_crd_namespace"),
                    "prometheus_url": payload.get("prometheus_url"),
                    "git_provider": payload.get("git_provider"),
                    "git_repo_url": payload.get("git_repo_url"),
                    "git_branch": payload.get("git_branch"),
                    "git_rules_path": payload.get("git_rules_path"),
                    "git_workflows_path": payload.get("git_workflows_path"),
                    "git_actions_path": payload.get("git_actions_path"),
                },
            ),
            (
                "Auth Providers",
                [
                    {
                        "name": item.get("name"),
                        "label": item.get("label"),
                        "login_mode": item.get("login_mode"),
                        "cli_login_mode": item.get("cli_login_mode"),
                    }
                    for item in payload.get("auth_providers") or []
                ],
            ),
        ]
    )


@click.group(name="settings")
def settings() -> None:
    """Inspect operator-facing application settings."""


@settings.command("show")
@click.pass_context
def show_settings(ctx: click.Context) -> None:
    """Show the PoundCake settings contract."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = client.get_settings()
        print_output(payload, output_format, table_renderer=_settings_table)
    except PoundCakeClientError as exc:
        print_error(f"Failed to load settings: {exc}")
        raise click.Abort() from exc

