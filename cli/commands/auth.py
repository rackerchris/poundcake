"""Authentication and access-management commands for the PoundCake CLI."""

from __future__ import annotations

import time

import click

from cli.client import PoundCakeClientError, ProviderInfo
from cli.commands.common import (
    build_preview_changes,
    get_client,
    get_output_format,
    print_dry_run_preview,
    validate_request_payload,
)
from cli.utils import print_error, print_info, print_output, print_success, to_plain_data
from api.schemas.schemas import AuthRoleBindingCreate, AuthRoleBindingUpdate


@click.group()
def auth() -> None:
    """Manage CLI authentication and RBAC bindings."""


def _provider_payload(provider: ProviderInfo) -> dict[str, object]:
    return {
        "name": provider.name,
        "label": provider.label,
        "login_mode": provider.login_mode,
        "cli_login_mode": provider.cli_login_mode,
        "browser_login": provider.browser_login,
        "device_login": provider.device_login,
        "password_login": provider.password_login,
    }


def _binding_by_id(ctx: click.Context, binding_id: int) -> dict[str, object]:
    client = get_client(ctx)
    for binding in client.list_auth_bindings():
        payload = binding.model_dump(mode="json", by_alias=True)
        if payload.get("id") == binding_id:
            return payload
    raise PoundCakeClientError(f"Binding {binding_id} was not found")


def _choose_provider(providers: list[ProviderInfo]) -> str:
    if len(providers) == 1:
        return providers[0].name
    click.echo("Enabled auth providers:")
    for provider in providers:
        click.echo(f"  - {provider.name}: {provider.label} ({provider.cli_login_mode})")
    selected = click.prompt(
        "Provider",
        type=click.Choice(
            [provider.name for provider in providers],
            case_sensitive=False,
        ),
    )
    return str(selected)


def _login_capable_providers(providers: list[ProviderInfo]) -> list[ProviderInfo]:
    return [provider for provider in providers if provider.password_login or provider.device_login]


def _run_device_flow(ctx: click.Context, provider: ProviderInfo) -> None:
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        start = client.start_device_login(provider.name)
        print_info(
            f"Open {start.verification_uri_complete or start.verification_uri} and approve code {start.user_code}."
        )
        deadline = time.time() + max(start.expires_in, start.interval)
        while time.time() < deadline:
            time.sleep(max(start.interval, 1))
            poll = client.poll_device_login(provider.name, start.device_code)
            status = str(poll.status or "").strip().lower()
            if status == "pending":
                continue
            if status == "expired":
                raise PoundCakeClientError(str(poll.detail or "Device login expired"))
            session = poll.session
            if status == "authorized" and session is not None:
                print_output(to_plain_data(session), output_format)
                return
        raise PoundCakeClientError("Device login timed out before authorization completed")
    except PoundCakeClientError as exc:
        print_error(f"Login failed: {exc}")
        raise click.Abort() from exc


@auth.command("providers")
@click.pass_context
def providers_cmd(ctx: click.Context) -> None:
    """List enabled auth providers."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        providers = client.get_auth_providers()
        print_output([_provider_payload(provider) for provider in providers], output_format)
    except PoundCakeClientError as exc:
        print_error(f"Could not load providers: {exc}")
        raise click.Abort() from exc


@auth.command("me")
@click.pass_context
def me_cmd(ctx: click.Context) -> None:
    """Show the current authenticated principal."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        me = client.auth_me()
        print_output(
            {
                "username": me.username,
                "display_name": me.display_name,
                "provider": me.provider,
                "role": me.role,
                "principal_type": me.principal_type,
                "principal_id": me.principal_id,
                "is_superuser": me.is_superuser,
                "permissions": me.permissions,
                "groups": me.groups,
                "expires_at": me.expires_at,
            },
            output_format,
        )
    except PoundCakeClientError as exc:
        print_error(f"Could not load current principal: {exc}")
        raise click.Abort() from exc


@auth.command("login")
@click.option("--provider", default=None, help="Auth provider name")
@click.option("--username", default=None, help="Username for password-based providers")
@click.option("--password", default=None, help="Password for password-based providers")
@click.pass_context
def login_cmd(
    ctx: click.Context,
    provider: str | None,
    username: str | None,
    password: str | None,
) -> None:
    """Log in and persist the session locally."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        providers = client.get_auth_providers()
        if not providers:
            raise PoundCakeClientError("No auth providers are enabled")
        login_providers = _login_capable_providers(providers)
        if provider:
            selected_provider = provider
        else:
            if not login_providers:
                raise PoundCakeClientError("No CLI-capable auth providers are configured")
            selected_provider = _choose_provider(login_providers)
        selected_info = next((item for item in providers if item.name == selected_provider), None)
        if selected_info is None:
            raise PoundCakeClientError(f"Provider '{selected_provider}' is not enabled")
        if selected_info.device_login and not selected_info.password_login:
            _run_device_flow(ctx, selected_info)
            return
        if not selected_info.password_login:
            if selected_info.name in {"auth0", "azure_ad"}:
                raise PoundCakeClientError(
                    f"{selected_info.label} CLI device login is not configured"
                )
            raise PoundCakeClientError(f"Provider '{selected_provider}' does not support CLI login")
        final_username = username or click.prompt("Username", type=str)
        final_password = password or click.prompt("Password", hide_input=True, type=str)
        result = client.login(selected_provider, final_username, final_password)
        print_output(
            {
                "username": result.username,
                "display_name": result.display_name,
                "provider": result.provider,
                "role": result.role,
                "is_superuser": result.is_superuser,
                "permissions": result.permissions or [],
                "expires_at": result.expires_at,
                "credential_type": "session_token",
                "session_token": result.session_id,
            },
            output_format,
        )
    except PoundCakeClientError as exc:
        print_error(f"Login failed: {exc}")
        raise click.Abort() from exc


@auth.command("logout")
@click.pass_context
def logout_cmd(ctx: click.Context) -> None:
    """Clear the local session and attempt remote logout."""
    client = get_client(ctx)
    try:
        had_session = client.logout()
        if had_session:
            print_success("Logged out and cleared stored session.")
        else:
            print_success("No stored session was present.")
    except PoundCakeClientError as exc:
        print_error(f"Logout failed: {exc}")
        raise click.Abort() from exc


@auth.group("principals")
def principals_group() -> None:
    """Inspect observed principals."""


@principals_group.command("list")
@click.option("--provider", default=None, help="Filter by provider")
@click.option("--search", default=None, help="Search usernames")
@click.option("--limit", default=100, type=int, show_default=True)
@click.option("--offset", default=0, type=int, show_default=True)
@click.pass_context
def principals_list_cmd(
    ctx: click.Context,
    provider: str | None,
    search: str | None,
    limit: int,
    offset: int,
) -> None:
    """List observed principals available for user-specific bindings."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        principals = client.list_auth_principals(
            provider=provider,
            search=search,
            limit=limit,
            offset=offset,
        )
        print_output(principals, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Could not load principals: {exc}")
        raise click.Abort() from exc


@auth.group("bindings")
def bindings_group() -> None:
    """Manage RBAC role bindings."""


@bindings_group.command("list")
@click.option("--provider", default=None, help="Filter by provider")
@click.pass_context
def bindings_list_cmd(ctx: click.Context, provider: str | None) -> None:
    """List current role bindings."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        print_output(client.list_auth_bindings(provider=provider), output_format)
    except PoundCakeClientError as exc:
        print_error(f"Could not load bindings: {exc}")
        raise click.Abort() from exc


@bindings_group.command("create")
@click.option("--provider", required=True, help="Provider name")
@click.option(
    "--type",
    "binding_type",
    required=True,
    type=click.Choice(["group", "user"], case_sensitive=False),
    help="Binding target type",
)
@click.option(
    "--role",
    required=True,
    type=click.Choice(["reader", "operator", "admin"], case_sensitive=False),
    help="PoundCake role to grant",
)
@click.option("--group", "external_group", default=None, help="External group name")
@click.option("--principal-id", default=None, type=int, help="Observed principal id")
@click.option("--dry-run", is_flag=True, help="Validate and preview the binding without creating it")
@click.pass_context
def bindings_create_cmd(
    ctx: click.Context,
    provider: str,
    binding_type: str,
    role: str,
    external_group: str | None,
    principal_id: int | None,
    dry_run: bool,
) -> None:
    """Create a role binding."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    if binding_type == "group" and not external_group:
        raise click.BadParameter("--group is required for group bindings")
    if binding_type == "user" and principal_id is None:
        raise click.BadParameter("--principal-id is required for user bindings")
    payload = {
        "provider": provider,
        "binding_type": binding_type,
        "role": role,
        "external_group": external_group,
        "principal_id": principal_id,
    }
    try:
        validated = validate_request_payload(
            client,
            payload,
            AuthRoleBindingCreate,
            "Invalid auth binding request payload",
        )
        if dry_run:
            print_dry_run_preview(
                ctx,
                command="auth bindings create",
                target=str(validated.get("external_group") or validated.get("principal_id") or "new binding"),
                payload=validated,
                summary={
                    "provider": validated.get("provider"),
                    "binding_type": validated.get("binding_type"),
                    "role": validated.get("role"),
                },
                impact="Saving creates a new RBAC binding that affects future authorization checks immediately.",
                changes=build_preview_changes(
                    {},
                    {
                        "provider": validated.get("provider"),
                        "binding_type": validated.get("binding_type"),
                        "role": validated.get("role"),
                        "target": validated.get("external_group") or validated.get("principal_id"),
                    },
                    labels={
                        "provider": "Provider",
                        "binding_type": "Binding type",
                        "role": "Role",
                        "target": "Target",
                    },
                ),
            )
            return
        print_output(client.create_auth_binding(validated), output_format)
    except PoundCakeClientError as exc:
        print_error(f"Could not create binding: {exc}")
        raise click.Abort() from exc


@bindings_group.command("update")
@click.argument("binding_id", type=int)
@click.option(
    "--role",
    required=True,
    type=click.Choice(["reader", "operator", "admin"], case_sensitive=False),
    help="Updated PoundCake role",
)
@click.option(
    "--group", "external_group", default=None, help="Updated group name for group bindings"
)
@click.option("--dry-run", is_flag=True, help="Validate and preview the binding update without saving it")
@click.pass_context
def bindings_update_cmd(
    ctx: click.Context,
    binding_id: int,
    role: str,
    external_group: str | None,
    dry_run: bool,
) -> None:
    """Update a role binding."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    payload: dict[str, object] = {"role": role}
    if external_group is not None:
        payload["external_group"] = external_group
    try:
        validated = validate_request_payload(
            client,
            payload,
            AuthRoleBindingUpdate,
            "Invalid auth binding update payload",
        )
        if dry_run:
            current_binding = _binding_by_id(ctx, binding_id)
            next_target = validated.get("external_group", current_binding.get("external_group"))
            print_dry_run_preview(
                ctx,
                command="auth bindings update",
                target=f"binding {binding_id}",
                payload=validated,
                summary={
                    "binding_id": binding_id,
                    "updated_fields": sorted(validated.keys()),
                },
                impact="Saving changes updates the bound role or group value used by future authorization checks immediately.",
                changes=build_preview_changes(
                    {
                        "role": current_binding.get("role"),
                        "external_group": current_binding.get("external_group"),
                    },
                    {
                        "role": validated.get("role", current_binding.get("role")),
                        "external_group": next_target,
                    },
                    labels={
                        "role": "Role",
                        "external_group": "External group",
                    },
                ),
            )
            return
        print_output(client.update_auth_binding(binding_id, validated), output_format)
    except PoundCakeClientError as exc:
        print_error(f"Could not update binding: {exc}")
        raise click.Abort() from exc


@bindings_group.command("delete")
@click.argument("binding_id", type=int)
@click.option("--dry-run", is_flag=True, help="Preview the binding deletion without deleting it")
@click.pass_context
def bindings_delete_cmd(ctx: click.Context, binding_id: int, dry_run: bool) -> None:
    """Delete a role binding."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        if dry_run:
            current_binding = _binding_by_id(ctx, binding_id)
            print_dry_run_preview(
                ctx,
                command="auth bindings delete",
                target=f"binding {binding_id}",
                payload={"binding_id": binding_id},
                summary={"binding_id": binding_id},
                impact="Saving deletes this RBAC binding, removing one access path immediately.",
                changes=build_preview_changes(
                    {
                        "provider": current_binding.get("provider"),
                        "binding_type": current_binding.get("binding_type"),
                        "role": current_binding.get("role"),
                        "target": current_binding.get("external_group")
                        or ((current_binding.get("principal") or {}).get("display_name"))
                        or ((current_binding.get("principal") or {}).get("username")),
                    },
                    {},
                    labels={
                        "provider": "Provider",
                        "binding_type": "Binding type",
                        "role": "Role",
                        "target": "Target",
                    },
                ),
            )
            return
        print_output(client.delete_auth_binding(binding_id), output_format)
    except PoundCakeClientError as exc:
        print_error(f"Could not delete binding: {exc}")
        raise click.Abort() from exc
