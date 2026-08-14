"""Plugin management commands for the PoundCake CLI."""

from __future__ import annotations

import json
from pathlib import Path

import click

from cli.client import PoundCakeClientError
from cli.commands.common import (
    build_preview_changes,
    compact_update_payload,
    get_client,
    get_output_format,
    merge_preview_state,
    print_dry_run_preview,
    validate_request_payload,
)
from cli.utils import (
    load_data_file,
    parse_json_object,
    print_error,
    print_output,
    render_sections,
    require_mapping,
)
from api.schemas.schemas import (
    ServicePluginConfigurationUpdate,
    ServicePluginCredentialUpdate,
    ServicePluginUpdate,
)


def _plugin_rows(items: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "service_type": item.get("service_type"),
            "enabled": item.get("enabled"),
            "plugin_type": item.get("plugin_type"),
            "health": item.get("health_status"),
            "helper_caps": len(item.get("helper_capabilities") or []),
            "health_check_enabled": item.get("health_check_enabled"),
            "health_check_interval_seconds": item.get("health_check_interval_seconds"),
            "config_editable": item.get("config_editable"),
        }
        for item in items
    ]


def _plugin_detail_table(item: dict[str, object]) -> str:
    return render_sections(
        [
            (
                "Plugin",
                {
                    "service_type": item.get("service_type"),
                    "plugin_type": item.get("plugin_type"),
                    "plugin_tier": item.get("plugin_tier"),
                    "enabled": item.get("enabled"),
                    "config_editable": item.get("config_editable"),
                    "status_message": item.get("status_message"),
                    "registered_ingredient_count": item.get("registered_ingredient_count"),
                    "registered_recipe_count": item.get("registered_recipe_count"),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                },
            ),
            (
                "Health",
                {
                    "health_status": item.get("health_status"),
                    "health_message": item.get("health_message"),
                    "health_error_code": item.get("health_error_code"),
                    "health_latency_ms": item.get("health_latency_ms"),
                    "consecutive_failures": item.get("consecutive_failures"),
                    "last_success_at": item.get("last_success_at"),
                    "last_health_check_at": item.get("last_health_check_at"),
                    "next_health_check_at": item.get("next_health_check_at"),
                    "health_check_state": item.get("health_check_state"),
                    "health_check_order_id": item.get("health_check_order_id"),
                    "health_check_task_id": item.get("health_check_task_id"),
                    "health_check_enabled": item.get("health_check_enabled"),
                    "health_check_interval_seconds": item.get("health_check_interval_seconds"),
                    "health_check_started_at": item.get("health_check_started_at"),
                    "health_check_grace_until": item.get("health_check_grace_until"),
                },
            ),
            (
                "Helper Capabilities",
                {
                    "helper_available": item.get("helper_available"),
                    "helper_capabilities": item.get("helper_capabilities"),
                    "required_helper_capabilities": item.get("required_helper_capabilities"),
                    "missing_helper_capabilities": item.get("missing_helper_capabilities"),
                },
            ),
        ]
    )


def _plugin_configuration_table(item: dict[str, object]) -> str:
    return render_sections(
        [
            (
                "Configuration",
                {
                    "service_type": item.get("service_type"),
                    "credential_type": item.get("credential_type"),
                    "credential_key_id": item.get("credential_key_id"),
                    "credential_configured": item.get("credential_configured"),
                    "updated_at": item.get("updated_at"),
                },
            ),
            ("Config", item.get("config") or {}),
            ("Config Schema", item.get("config_schema") or {}),
            ("Credential Requirements", item.get("credential_requirements") or []),
        ]
    )


def _plugin_action_table(item: dict[str, object]) -> str:
    return render_sections(
        [
            (
                "Result",
                {
                    "service_type": item.get("service_type"),
                    "service_exec": item.get("service_exec"),
                    "status": item.get("status"),
                    "message": item.get("message"),
                    "order_id": item.get("order_id"),
                    "order_req_id": item.get("order_req_id"),
                    "submitted_at": item.get("submitted_at"),
                },
            ),
            ("Details", item.get("details") or {}),
        ]
    )


def _prometheus_rules_table(item: dict[str, object]) -> str:
    return render_sections(
        [
            (
                "Summary",
                {
                    "service_type": item.get("service_type"),
                    "namespace": item.get("namespace"),
                    "resource_count": item.get("resource_count"),
                    "group_count": item.get("group_count"),
                    "rule_count": item.get("rule_count"),
                    "alert_count": item.get("alert_count"),
                    "recording_count": item.get("recording_count"),
                    "checked_at": item.get("checked_at"),
                },
            ),
            (
                "Items",
                [
                    {
                        "name": resource.get("name"),
                        "namespace": resource.get("namespace"),
                        "group_count": resource.get("group_count"),
                        "rule_count": resource.get("rule_count"),
                        "alert_count": resource.get("alert_count"),
                        "recording_count": resource.get("recording_count"),
                    }
                    for resource in item.get("items") or []
                ],
            ),
        ]
    )


def _prometheus_rule_detail_table(item: dict[str, object]) -> str:
    return render_sections(
        [
            (
                "Resource",
                {
                    "service_type": item.get("service_type"),
                    "name": item.get("name"),
                    "namespace": item.get("namespace"),
                    "group_count": item.get("group_count"),
                    "rule_count": item.get("rule_count"),
                    "alert_count": item.get("alert_count"),
                    "recording_count": item.get("recording_count"),
                    "checked_at": item.get("checked_at"),
                },
            ),
            ("Groups", item.get("groups") or []),
            ("Labels", item.get("labels") or {}),
        ]
    )


def _prometheus_rule_record_table(item: dict[str, object]) -> str:
    return render_sections(
        [
            (
                "Rule",
                {
                    "service_type": item.get("service_type"),
                    "namespace": item.get("namespace"),
                    "crd_name": item.get("crd_name"),
                    "group_name": item.get("group_name"),
                    "rule_name": item.get("rule_name"),
                    "rule_kind": item.get("rule_kind"),
                    "checked_at": item.get("checked_at"),
                },
            ),
            ("Source", item.get("source") or {}),
            ("Rule Data", item.get("rule_data") or {}),
        ]
    )


def _load_object_file(path: Path, label: str) -> dict[str, object]:
    return require_mapping(load_data_file(path), label)


def _resolve_payload(
    *,
    file: Path | None,
    json_value: str | None,
    label: str,
) -> dict[str, object] | None:
    if file is not None and json_value is not None:
        raise click.BadParameter(f"--{label}-file cannot be combined with --{label}-json")
    if file is not None:
        return _load_object_file(file, f"{label} file")
    if json_value is not None:
        return parse_json_object(json_value, f"{label}-json")
    return None


@click.group(name="plugins")
def plugins() -> None:
    """Manage service plugins, configuration, credentials, and health."""


@plugins.command("list")
@click.pass_context
def list_plugins(ctx: click.Context) -> None:
    """List enabled service plugins."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        items = client.list_plugins()
        plain = [item.model_dump(mode="json", by_alias=True) for item in items]
        if output_format == "table":
            print_output(_plugin_rows(plain), output_format)
            return
        print_output(items, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Failed to list plugins: {exc}")
        raise click.Abort() from exc


@plugins.command("show")
@click.argument("service_type")
@click.pass_context
def show_plugin(ctx: click.Context, service_type: str) -> None:
    """Show one plugin."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        item = client.get_plugin(service_type)
        print_output(item, output_format, table_renderer=_plugin_detail_table)
    except PoundCakeClientError as exc:
        print_error(f"Failed to show plugin {service_type}: {exc}")
        raise click.Abort() from exc


@plugins.command("update")
@click.argument("service_type")
@click.option("--file", type=click.Path(exists=True, path_type=Path), default=None)
@click.option("--payload-json", default=None, help="Full plugin update payload as JSON object")
@click.option("--enabled/--disabled", default=None)
@click.option("--run-interval-seconds", type=int, default=None)
@click.option("--query-limit", type=int, default=None)
@click.option("--health-check-interval-seconds", type=int, default=None)
@click.option("--status-message", default=None)
@click.option("--dry-run", is_flag=True, help="Validate and preview the plugin update without saving it")
@click.pass_context
def update_plugin(
    ctx: click.Context,
    service_type: str,
    file: Path | None,
    payload_json: str | None,
    enabled: bool | None,
    run_interval_seconds: int | None,
    query_limit: int | None,
    health_check_interval_seconds: int | None,
    status_message: str | None,
    dry_run: bool,
) -> None:
    """Update operator-editable plugin state."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = _resolve_payload(file=file, json_value=payload_json, label="payload")
        if payload is None:
            payload = compact_update_payload(
                {
                    "enabled": enabled,
                    "run_interval_seconds": run_interval_seconds,
                    "query_limit": query_limit,
                    "health_check_interval_seconds": health_check_interval_seconds,
                    "status_message": status_message,
                }
            )
        if not payload:
            raise click.BadParameter("Provide --file, --payload-json, or at least one update flag")
        validated = validate_request_payload(
            client,
            payload,
            ServicePluginUpdate,
            "Invalid plugin update payload",
        )
        if dry_run:
            current_plugin = client.get_plugin(service_type).model_dump(mode="json", by_alias=True)
            next_plugin = merge_preview_state(current_plugin, validated)
            print_dry_run_preview(
                ctx,
                command="plugins update",
                target=service_type,
                payload=validated,
                summary={
                    "updated_fields": sorted(validated.keys()),
                    "disable_requested": validated.get("enabled") is False,
                },
                impact="Plugin runtime state and cadence changes take effect immediately after save.",
                changes=build_preview_changes(
                    {
                        key: current_plugin.get(key)
                        for key in validated.keys()
                    },
                    {
                        key: next_plugin.get(key)
                        for key in validated.keys()
                    },
                    labels={
                        "enabled": "Plugin enabled",
                        "run_interval_seconds": "Run interval (sec)",
                        "query_limit": "Query limit",
                        "health_check_interval_seconds": "Health interval (sec)",
                        "status_message": "Status message",
                    },
                ),
            )
            return
        response = client.update_plugin(service_type, validated)
        print_output(response, output_format, table_renderer=_plugin_detail_table)
    except (click.BadParameter, PoundCakeClientError) as exc:
        print_error(f"Failed to update plugin {service_type}: {exc}")
        raise click.Abort() from exc


@plugins.command("health")
@click.argument("service_type")
@click.pass_context
def health_cmd(ctx: click.Context, service_type: str) -> None:
    """Show last recorded plugin health."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        response = client.get_plugin_health(service_type)
        print_output(response, output_format, table_renderer=_plugin_detail_table)
    except PoundCakeClientError as exc:
        print_error(f"Plugin health check failed for {service_type}: {exc}")
        raise click.Abort() from exc


@plugins.group("config")
def config_group() -> None:
    """Show or update non-secret plugin configuration."""


@config_group.command("show")
@click.argument("service_type")
@click.pass_context
def config_show(ctx: click.Context, service_type: str) -> None:
    """Show plugin configuration."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        response = client.get_plugin_configuration(service_type)
        print_output(response, output_format, table_renderer=_plugin_configuration_table)
    except PoundCakeClientError as exc:
        print_error(f"Failed to show plugin configuration for {service_type}: {exc}")
        raise click.Abort() from exc


@config_group.command("set")
@click.argument("service_type")
@click.option("--config-json", default=None, help="Configuration JSON object")
@click.option(
    "--config-file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="JSON/YAML file containing the configuration object",
)
@click.option("--output-json", is_flag=True, help="Output raw JSON")
@click.option("--dry-run", is_flag=True, help="Validate and preview the configuration update without saving it")
@click.pass_context
def config_set(
    ctx: click.Context,
    service_type: str,
    config_json: str | None,
    config_file: Path | None,
    output_json: bool,
    dry_run: bool,
) -> None:
    """Persist non-secret plugin configuration."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        config = _resolve_payload(file=config_file, json_value=config_json, label="config")
        if config is None:
            raise click.BadParameter("Provide --config-json or --config-file")
        validated = validate_request_payload(
            client,
            {"config": config},
            ServicePluginConfigurationUpdate,
            "Invalid plugin configuration payload",
        )
        if dry_run:
            rendered_config = validated.get("config") or {}
            current_configuration = client.get_plugin_configuration(service_type).model_dump(mode="json", by_alias=True)
            current_config = require_mapping(current_configuration.get("config") or {}, "plugin configuration")
            print_dry_run_preview(
                ctx,
                command="plugins config set",
                target=service_type,
                payload=validated,
                summary={
                    "config_field_count": len(rendered_config),
                    "config_fields": sorted(rendered_config.keys()),
                },
                impact="Updated adapter connection settings are used by future connection tests and runtime work after save.",
                changes=build_preview_changes(current_config, rendered_config),
            )
            return
        response = client.update_plugin_configuration(service_type, validated["config"])
        if output_json:
            click.echo(json.dumps(response.model_dump(mode="json", by_alias=True), indent=2))
            return
        print_output(response, output_format, table_renderer=_plugin_configuration_table)
    except (click.BadParameter, PoundCakeClientError) as exc:
        print_error(f"Plugin configuration failed for {service_type}: {exc}")
        raise click.Abort() from exc


@plugins.group("credentials")
def credentials_group() -> None:
    """Write plugin credentials through credential-manager."""


@credentials_group.command("set")
@click.argument("service_type")
@click.option("--credential-type", required=True)
@click.option("--credential-key-id", default="default", show_default=True)
@click.option("--payload-json", default=None, help="Credential payload as JSON object")
@click.option(
    "--payload-file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="JSON/YAML file containing the credential payload object",
)
@click.option("--rotate-credential/--no-rotate-credential", default=False, show_default=True)
@click.option("--dry-run", is_flag=True, help="Validate and preview the credential update without saving it")
@click.pass_context
def credentials_set(
    ctx: click.Context,
    service_type: str,
    credential_type: str,
    credential_key_id: str,
    payload_json: str | None,
    payload_file: Path | None,
    rotate_credential: bool,
    dry_run: bool,
) -> None:
    """Write secret plugin credential material."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        credential_payload = _resolve_payload(file=payload_file, json_value=payload_json, label="payload")
        if credential_payload is None:
            raise click.BadParameter("Provide --payload-json or --payload-file")
        request_payload = {
            "credential_type": credential_type,
            "credential_key_id": credential_key_id,
            "credential_payload": credential_payload,
            "rotate_credential": rotate_credential,
        }
        validated = validate_request_payload(
            client,
            request_payload,
            ServicePluginCredentialUpdate,
            "Invalid plugin credential payload",
        )
        if dry_run:
            current_configuration = client.get_plugin_configuration(service_type).model_dump(mode="json", by_alias=True)
            current_key_id = current_configuration.get("credential_key_id") or "default"
            print_dry_run_preview(
                ctx,
                command="plugins credentials set",
                target=service_type,
                payload=validated,
                summary={
                    "credential_type": validated.get("credential_type"),
                    "credential_key_id": validated.get("credential_key_id"),
                    "rotate_credential": bool(validated.get("rotate_credential")),
                    "credential_fields": sorted(
                        (validated.get("credential_payload") or {}).keys()
                    ),
                },
                impact="Saving rotates or updates the adapter credential material used by future authenticated plugin work.",
                changes=build_preview_changes(
                    {
                        "credential_key_id": current_key_id,
                        "rotate_credential": False,
                        "credential_fields": [],
                    },
                    {
                        "credential_key_id": validated.get("credential_key_id"),
                        "rotate_credential": bool(validated.get("rotate_credential")),
                        "credential_fields": sorted(
                            (validated.get("credential_payload") or {}).keys()
                        ),
                    },
                    labels={
                        "credential_key_id": "Credential key ID",
                        "rotate_credential": "Rotate credential",
                        "credential_fields": "Credential fields",
                    },
                ),
                redact=True,
            )
            return
        response = client.update_plugin_credential(service_type, validated)
        print_output(response, output_format, table_renderer=_plugin_configuration_table)
    except (click.BadParameter, PoundCakeClientError) as exc:
        print_error(f"Plugin credential update failed for {service_type}: {exc}")
        raise click.Abort() from exc


@plugins.command("test-connection")
@click.argument("service_type")
@click.pass_context
def test_connection(
    ctx: click.Context,
    service_type: str,
) -> None:
    """Queue a health-check order for an external plugin."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        response = client.test_plugin_connection(service_type)
        print_output(response, output_format, table_renderer=_plugin_action_table)
    except (click.BadParameter, PoundCakeClientError) as exc:
        print_error(f"Plugin connection test failed for {service_type}: {exc}")
        raise click.Abort() from exc


@plugins.group("k8s")
def k8s_group() -> None:
    """Kubernetes-plugin helper surfaces."""


@plugins.group("prometheus")
def prometheus_group() -> None:
    """Prometheus-plugin helper surfaces."""


@prometheus_group.command("reload")
@click.pass_context
def prometheus_reload(ctx: click.Context) -> None:
    """Trigger a Prometheus rule/config reload."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        response = client.reload_prometheus_config()
        print_output(response, output_format, table_renderer=_plugin_action_table)
    except PoundCakeClientError as exc:
        print_error(f"Failed to reload Prometheus rule state: {exc}")
        raise click.Abort() from exc


@k8s_group.command("prometheus-rules")
@click.option("--namespace", default=None)
@click.pass_context
def prometheus_rules(ctx: click.Context, namespace: str | None) -> None:
    """List PrometheusRule CRDs through the Kubernetes plugin."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        response = client.list_prometheus_rules(namespace=namespace)
        print_output(response, output_format, table_renderer=_prometheus_rules_table)
    except PoundCakeClientError as exc:
        print_error(f"Failed to list Prometheus rules: {exc}")
        raise click.Abort() from exc


@k8s_group.command("prometheus-rule")
@click.option("--crd-name", required=True)
@click.option("--namespace", default=None)
@click.pass_context
def prometheus_rule(ctx: click.Context, crd_name: str, namespace: str | None) -> None:
    """Show one PrometheusRule CRD through the Kubernetes plugin."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        response = client.get_prometheus_rule(crd_name=crd_name, namespace=namespace)
        print_output(response, output_format, table_renderer=_prometheus_rule_detail_table)
    except PoundCakeClientError as exc:
        print_error(f"Failed to show PrometheusRule {crd_name}: {exc}")
        raise click.Abort() from exc


@k8s_group.group("rule")
def k8s_rule_group() -> None:
    """Edit one rule inside an existing PrometheusRule CRD."""


@k8s_rule_group.command("show")
@click.option("--crd-name", required=True)
@click.option("--group-name", required=True)
@click.option("--rule-name", required=True)
@click.option("--namespace", default=None)
@click.pass_context
def prometheus_rule_show(
    ctx: click.Context,
    crd_name: str,
    group_name: str,
    rule_name: str,
    namespace: str | None,
) -> None:
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        response = client.get_prometheus_rule_rule(
            crd_name=crd_name,
            group_name=group_name,
            rule_name=rule_name,
            namespace=namespace,
        )
        print_output(response, output_format, table_renderer=_prometheus_rule_record_table)
    except PoundCakeClientError as exc:
        print_error(f"Failed to show Prometheus rule {rule_name}: {exc}")
        raise click.Abort() from exc


def _rule_payload(rule_json: str | None, rule_file: Path | None) -> dict[str, object]:
    payload = _resolve_payload(file=rule_file, json_value=rule_json, label="rule")
    if payload is None:
        raise click.BadParameter("Provide --rule-json or --rule-file")
    return payload


@k8s_rule_group.command("set")
@click.option("--crd-name", required=True)
@click.option("--group-name", required=True)
@click.option("--rule-name", required=True)
@click.option("--rule-json", default=None, help="Rule JSON object")
@click.option(
    "--rule-file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="JSON/YAML file containing the rule object",
)
@click.option("--namespace", default=None)
@click.pass_context
def prometheus_rule_set(
    ctx: click.Context,
    crd_name: str,
    group_name: str,
    rule_name: str,
    rule_json: str | None,
    rule_file: Path | None,
    namespace: str | None,
) -> None:
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        response = client.update_prometheus_rule_rule(
            crd_name=crd_name,
            rule_name=rule_name,
            group_name=group_name,
            rule_data=_rule_payload(rule_json, rule_file),
            namespace=namespace,
        )
        print_output(response, output_format, table_renderer=_prometheus_rule_record_table)
    except (click.BadParameter, PoundCakeClientError) as exc:
        print_error(f"Failed to update Prometheus rule {rule_name}: {exc}")
        raise click.Abort() from exc


@k8s_rule_group.command("add")
@click.option("--crd-name", required=True)
@click.option("--group-name", required=True)
@click.option("--rule-name", required=True)
@click.option("--rule-json", default=None, help="Rule JSON object")
@click.option(
    "--rule-file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="JSON/YAML file containing the rule object",
)
@click.option("--namespace", default=None)
@click.pass_context
def prometheus_rule_add(
    ctx: click.Context,
    crd_name: str,
    group_name: str,
    rule_name: str,
    rule_json: str | None,
    rule_file: Path | None,
    namespace: str | None,
) -> None:
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        response = client.create_prometheus_rule_rule(
            crd_name=crd_name,
            group_name=group_name,
            rule_name=rule_name,
            rule_data=_rule_payload(rule_json, rule_file),
            namespace=namespace,
        )
        print_output(response, output_format, table_renderer=_prometheus_rule_record_table)
    except (click.BadParameter, PoundCakeClientError) as exc:
        print_error(f"Failed to add Prometheus rule {rule_name}: {exc}")
        raise click.Abort() from exc


@plugins.group("genestack-monitoring")
def genestack_monitoring_group() -> None:
    """Genestack Monitoring helper surfaces."""


@genestack_monitoring_group.command("export-alert-updates")
@click.option("--crd-name", required=True)
@click.option("--group-name", required=True)
@click.option("--rule-name", required=True)
@click.option("--namespace", default=None)
@click.pass_context
def export_alert_updates(
    ctx: click.Context,
    crd_name: str,
    group_name: str,
    rule_name: str,
    namespace: str | None,
) -> None:
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        response = client.export_genestack_alert_updates(
            crd_name=crd_name,
            group_name=group_name,
            rule_name=rule_name,
            namespace=namespace,
        )
        print_output(response, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Failed to export Genestack alert updates for {rule_name}: {exc}")
        raise click.Abort() from exc
