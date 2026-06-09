"""Scheduled task commands for the PoundCake CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
    parse_json_value,
    print_error,
    print_output,
    render_sections,
    require_mapping,
    to_plain_data,
)
from api.schemas.schemas import ScheduledTaskCreate, ScheduledTaskUpdate


def _operation_metadata(task: dict[str, Any]) -> tuple[str | None, str | None]:
    task_parameters = task.get("task_parameters")
    if not isinstance(task_parameters, dict):
        return None, None
    operation = str(task_parameters.get("operation") or "").strip()
    metadata_by_operation = task_parameters.get("operation_metadata")
    if not operation or not isinstance(metadata_by_operation, dict):
        return None, None
    metadata = metadata_by_operation.get(operation)
    if not isinstance(metadata, dict):
        return None, None
    label = str(metadata.get("label") or "").strip() or None
    description = str(metadata.get("description") or "").strip() or None
    return label, description


def _task_identity(task: dict[str, Any]) -> str:
    service_type = str(task.get("service_type") or "").strip()
    service_exec = str(task.get("service_exec") or "").strip()
    if service_type and service_exec:
        return f"{service_type}/{service_exec}"
    return service_type or service_exec or "-"


def _task_row(task: dict[str, Any]) -> dict[str, Any]:
    operation_label, _ = _operation_metadata(task)
    return {
        "id": task.get("id"),
        "task_key": task.get("task_key"),
        "task_type": task.get("task_type"),
        "identity": _task_identity(task),
        "operation": operation_label or "-",
        "status": task.get("status"),
        "enabled": task.get("is_enabled"),
        "run_interval_seconds": task.get("run_interval_seconds"),
    }


def _task_detail_table(task: dict[str, Any]) -> str:
    operation_label, operation_description = _operation_metadata(task)
    sections: list[tuple[str, Any]] = [
        (
            "Task",
            {
                "id": task.get("id"),
                "task_key": task.get("task_key"),
                "task_type": task.get("task_type"),
                "identity": _task_identity(task),
                "service_type": task.get("service_type"),
                "service_exec": task.get("service_exec"),
                "source": task.get("source"),
                "status": task.get("status"),
                "enabled": task.get("is_enabled"),
                "run_interval_seconds": task.get("run_interval_seconds"),
                "next_run_at": task.get("next_run_at"),
                "priority": task.get("priority"),
                "timeout_seconds": task.get("timeout_seconds"),
                "created_at": task.get("created_at"),
                "updated_at": task.get("updated_at"),
            },
        ),
        (
            "Execution",
            {
                "operation_label": operation_label,
                "operation_description": operation_description,
                "last_status": task.get("last_status"),
                "last_message": task.get("last_message"),
                "last_order_id": task.get("last_order_id"),
                "last_order_req_id": task.get("last_order_req_id"),
                "last_started_at": task.get("last_started_at"),
                "last_completed_at": task.get("last_completed_at"),
                "consecutive_failures": task.get("consecutive_failures"),
                "run_now_label": task.get("run_now_label"),
                "run_now_description": task.get("run_now_description"),
            },
        ),
    ]
    if task.get("task_payload") is not None:
        sections.append(("Task Payload", task.get("task_payload")))
    if task.get("task_parameters") is not None:
        sections.append(("Task Parameters", task.get("task_parameters")))
    if "expected_outcome" in task:
        sections.append(("Expected Outcome", task.get("expected_outcome")))
    return render_sections(sections)


def _parse_json_option(value: str | None, label: str) -> Any:
    if value is None:
        return None
    return parse_json_value(value, label)


def _load_task_payload_file(path: Path, label: str) -> dict[str, Any]:
    return require_mapping(load_data_file(path), label)


def _resolve_task_payload(file: Path | None, payload_json: str | None, label: str) -> dict[str, Any] | None:
    if file is not None and payload_json is not None:
        raise click.BadParameter(f"--{label}-file cannot be combined with --{label}-json")
    if file is not None:
        return _load_task_payload_file(file, f"{label} file")
    if payload_json is not None:
        data = parse_json_value(payload_json, f"{label}-json")
        if not isinstance(data, dict):
            raise click.BadParameter(f"{label}-json must decode to a JSON object")
        return data
    return None


def _build_create_payload(
    *,
    file: Path | None,
    payload_json: str | None,
    task_key: str | None,
    task_type: str | None,
    service_type: str | None,
    service_exec: str | None,
    source: str | None,
    is_enabled: bool | None,
    run_interval_seconds: int | None,
    next_run_at: str | None,
    priority: int | None,
    timeout_seconds: int | None,
    task_payload_json: str | None,
    task_payload_file: Path | None,
    task_parameters_json: str | None,
    task_parameters_file: Path | None,
    expected_outcome_json: str | None,
) -> dict[str, Any]:
    if file is not None and any(
        value not in (None, False)
        for value in (
            payload_json,
            task_key,
            task_type,
            service_type,
            service_exec,
            source,
            is_enabled,
            run_interval_seconds,
            next_run_at,
            priority,
            timeout_seconds,
            task_payload_json,
            task_payload_file,
            task_parameters_json,
            task_parameters_file,
            expected_outcome_json,
        )
    ):
        raise click.BadParameter("--file cannot be combined with inline task options")
    if file is not None:
        return _load_task_payload_file(file, "scheduled task file")
    if payload_json is not None:
        payload = _parse_json_option(payload_json, "payload-json")
        if not isinstance(payload, dict):
            raise click.BadParameter("payload-json must decode to a JSON object")
        return payload

    payload = compact_update_payload(
        {
            "task_key": task_key,
            "task_type": task_type,
            "service_type": service_type,
            "service_exec": service_exec,
            "source": source,
            "is_enabled": is_enabled,
            "run_interval_seconds": run_interval_seconds,
            "next_run_at": next_run_at,
            "priority": priority,
            "timeout_seconds": timeout_seconds,
            "task_payload": _resolve_task_payload(task_payload_file, task_payload_json, "task-payload"),
            "task_parameters": _resolve_task_payload(
                task_parameters_file,
                task_parameters_json,
                "task-parameters",
            ),
            "expected_outcome": _parse_json_option(expected_outcome_json, "expected-outcome-json"),
        }
    )
    if "task_key" not in payload or "task_type" not in payload:
        raise click.BadParameter(
            "Provide --file, --payload-json, or the required inline fields --task-key and --task-type"
        )
    return payload


def _build_update_payload(
    *,
    file: Path | None,
    payload_json: str | None,
    is_enabled: bool | None,
    run_interval_seconds: int | None,
    next_run_at: str | None,
    priority: int | None,
    timeout_seconds: int | None,
    task_payload_json: str | None,
    task_payload_file: Path | None,
    task_parameters_json: str | None,
    task_parameters_file: Path | None,
    expected_outcome_json: str | None,
) -> dict[str, Any]:
    if file is not None and any(
        value not in (None, False)
        for value in (
            payload_json,
            is_enabled,
            run_interval_seconds,
            next_run_at,
            priority,
            timeout_seconds,
            task_payload_json,
            task_payload_file,
            task_parameters_json,
            task_parameters_file,
            expected_outcome_json,
        )
    ):
        raise click.BadParameter("--file cannot be combined with inline update options")
    if file is not None:
        return _load_task_payload_file(file, "scheduled task update file")
    if payload_json is not None:
        payload = _parse_json_option(payload_json, "payload-json")
        if not isinstance(payload, dict):
            raise click.BadParameter("payload-json must decode to a JSON object")
        return payload
    payload = compact_update_payload(
        {
            "is_enabled": is_enabled,
            "run_interval_seconds": run_interval_seconds,
            "next_run_at": next_run_at,
            "priority": priority,
            "timeout_seconds": timeout_seconds,
            "task_payload": _resolve_task_payload(task_payload_file, task_payload_json, "task-payload"),
            "task_parameters": _resolve_task_payload(
                task_parameters_file,
                task_parameters_json,
                "task-parameters",
            ),
            "expected_outcome": _parse_json_option(expected_outcome_json, "expected-outcome-json"),
        }
    )
    if not payload:
        raise click.BadParameter("Provide --file, --payload-json, or at least one update option")
    return payload


@click.group(name="scheduled-tasks")
def scheduled_tasks() -> None:
    """Manage scheduled task contracts and operator controls."""


@scheduled_tasks.command("list")
@click.option("--task-type", default=None)
@click.option("--service-type", default=None)
@click.pass_context
def list_tasks(ctx: click.Context, task_type: str | None, service_type: str | None) -> None:
    """List scheduled tasks."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        tasks = client.list_scheduled_tasks(task_type=task_type, service_type=service_type)
        plain = to_plain_data(tasks)
        if output_format == "table":
            print_output([_task_row(task) for task in plain], output_format)
            return
        print_output(tasks, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Failed to list scheduled tasks: {exc}")
        raise click.Abort() from exc


@scheduled_tasks.command("status")
@click.option("--task-type", default=None)
@click.option("--service-type", default=None)
@click.pass_context
def list_task_statuses(ctx: click.Context, task_type: str | None, service_type: str | None) -> None:
    """List redacted scheduled task statuses."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        tasks = client.list_scheduled_task_statuses(task_type=task_type, service_type=service_type)
        plain = to_plain_data(tasks)
        if output_format == "table":
            print_output([_task_row(task) for task in plain], output_format)
            return
        print_output(tasks, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Failed to list scheduled task statuses: {exc}")
        raise click.Abort() from exc


@scheduled_tasks.command("show")
@click.argument("task_id", type=int)
@click.pass_context
def show_task(ctx: click.Context, task_id: int) -> None:
    """Show one scheduled task."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        task = client.get_scheduled_task(task_id)
        print_output(task, output_format, table_renderer=_task_detail_table)
    except PoundCakeClientError as exc:
        print_error(f"Failed to show scheduled task {task_id}: {exc}")
        raise click.Abort() from exc


@scheduled_tasks.command("create")
@click.option("--file", type=click.Path(exists=True, path_type=Path), default=None)
@click.option("--payload-json", default=None, help="Full scheduled task payload as JSON object")
@click.option("--task-key", default=None)
@click.option("--task-type", default=None)
@click.option("--service-type", default=None)
@click.option("--service-exec", default=None)
@click.option("--source", default=None)
@click.option("--enabled/--disabled", "is_enabled", default=None)
@click.option("--run-interval-seconds", type=int, default=None)
@click.option("--next-run-at", default=None)
@click.option("--priority", type=int, default=None)
@click.option("--timeout-seconds", type=int, default=None)
@click.option("--task-payload-json", default=None)
@click.option("--task-payload-file", type=click.Path(exists=True, path_type=Path), default=None)
@click.option("--task-parameters-json", default=None)
@click.option(
    "--task-parameters-file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
)
@click.option("--expected-outcome-json", default=None)
@click.option("--dry-run", is_flag=True, help="Validate and preview the scheduled task without saving it")
@click.pass_context
def create_task(
    ctx: click.Context,
    file: Path | None,
    payload_json: str | None,
    task_key: str | None,
    task_type: str | None,
    service_type: str | None,
    service_exec: str | None,
    source: str | None,
    is_enabled: bool | None,
    run_interval_seconds: int | None,
    next_run_at: str | None,
    priority: int | None,
    timeout_seconds: int | None,
    task_payload_json: str | None,
    task_payload_file: Path | None,
    task_parameters_json: str | None,
    task_parameters_file: Path | None,
    expected_outcome_json: str | None,
    dry_run: bool,
) -> None:
    """Create a scheduled task."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = _build_create_payload(
            file=file,
            payload_json=payload_json,
            task_key=task_key,
            task_type=task_type,
            service_type=service_type,
            service_exec=service_exec,
            source=source,
            is_enabled=is_enabled,
            run_interval_seconds=run_interval_seconds,
            next_run_at=next_run_at,
            priority=priority,
            timeout_seconds=timeout_seconds,
            task_payload_json=task_payload_json,
            task_payload_file=task_payload_file,
            task_parameters_json=task_parameters_json,
            task_parameters_file=task_parameters_file,
            expected_outcome_json=expected_outcome_json,
        )
        validated = validate_request_payload(
            client,
            payload,
            ScheduledTaskCreate,
            "Invalid scheduled task create payload",
        )
        if dry_run:
            next_task = {
                "task_type": validated.get("task_type"),
                "service_type": validated.get("service_type"),
                "enabled": bool(validated.get("is_enabled")),
                "run_interval_seconds": validated.get("run_interval_seconds"),
            }
            print_dry_run_preview(
                ctx,
                command="scheduled-tasks create",
                target=str(validated.get("task_key") or "new scheduled task"),
                payload=validated,
                summary={
                    "task_type": validated.get("task_type"),
                    "service_type": validated.get("service_type"),
                    "enabled": bool(validated.get("is_enabled")),
                    "run_interval_seconds": validated.get("run_interval_seconds"),
                },
                impact="Saving creates a new recurring task definition that Dishwasher can schedule immediately.",
                changes=build_preview_changes({}, next_task, labels={
                    "task_type": "Task type",
                    "service_type": "Service type",
                    "enabled": "Enabled",
                    "run_interval_seconds": "Run interval (sec)",
                }),
            )
            return
        response = client.create_scheduled_task(validated)
        print_output(response, output_format, table_renderer=_task_detail_table)
    except (click.BadParameter, PoundCakeClientError) as exc:
        print_error(f"Failed to create scheduled task: {exc}")
        raise click.Abort() from exc


@scheduled_tasks.command("update")
@click.argument("task_id", type=int)
@click.option("--file", type=click.Path(exists=True, path_type=Path), default=None)
@click.option("--payload-json", default=None, help="Full scheduled task update payload as JSON object")
@click.option("--enabled/--disabled", "is_enabled", default=None)
@click.option("--run-interval-seconds", type=int, default=None)
@click.option("--next-run-at", default=None)
@click.option("--priority", type=int, default=None)
@click.option("--timeout-seconds", type=int, default=None)
@click.option("--task-payload-json", default=None)
@click.option("--task-payload-file", type=click.Path(exists=True, path_type=Path), default=None)
@click.option("--task-parameters-json", default=None)
@click.option(
    "--task-parameters-file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
)
@click.option("--expected-outcome-json", default=None)
@click.option("--dry-run", is_flag=True, help="Validate and preview the scheduled task update without saving it")
@click.pass_context
def update_task(
    ctx: click.Context,
    task_id: int,
    file: Path | None,
    payload_json: str | None,
    is_enabled: bool | None,
    run_interval_seconds: int | None,
    next_run_at: str | None,
    priority: int | None,
    timeout_seconds: int | None,
    task_payload_json: str | None,
    task_payload_file: Path | None,
    task_parameters_json: str | None,
    task_parameters_file: Path | None,
    expected_outcome_json: str | None,
    dry_run: bool,
) -> None:
    """Update a scheduled task."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = _build_update_payload(
            file=file,
            payload_json=payload_json,
            is_enabled=is_enabled,
            run_interval_seconds=run_interval_seconds,
            next_run_at=next_run_at,
            priority=priority,
            timeout_seconds=timeout_seconds,
            task_payload_json=task_payload_json,
            task_payload_file=task_payload_file,
            task_parameters_json=task_parameters_json,
            task_parameters_file=task_parameters_file,
            expected_outcome_json=expected_outcome_json,
        )
        validated = validate_request_payload(
            client,
            payload,
            ScheduledTaskUpdate,
            "Invalid scheduled task update payload",
        )
        if dry_run:
            current_task = client.get_scheduled_task(task_id).model_dump(mode="json", by_alias=True)
            next_task = merge_preview_state(current_task, validated)
            print_dry_run_preview(
                ctx,
                command="scheduled-tasks update",
                target=f"task {task_id}",
                payload=validated,
                summary={
                    "updated_fields": sorted(validated.keys()),
                    "enabled": validated.get("is_enabled", "-"),
                    "run_interval_seconds": validated.get("run_interval_seconds", "-"),
                },
                impact="Saving changes updates the recurring task cadence or payload used by future runs.",
                changes=build_preview_changes(
                    {
                        "is_enabled": current_task.get("is_enabled"),
                        "run_interval_seconds": current_task.get("run_interval_seconds"),
                        "priority": current_task.get("priority"),
                        "timeout_seconds": current_task.get("timeout_seconds"),
                    },
                    {
                        "is_enabled": next_task.get("is_enabled"),
                        "run_interval_seconds": next_task.get("run_interval_seconds"),
                        "priority": next_task.get("priority"),
                        "timeout_seconds": next_task.get("timeout_seconds"),
                    },
                    labels={
                        "is_enabled": "Enabled",
                        "run_interval_seconds": "Run interval (sec)",
                        "priority": "Priority",
                        "timeout_seconds": "Timeout (sec)",
                    },
                ),
            )
            return
        response = client.update_scheduled_task(task_id, validated)
        print_output(response, output_format, table_renderer=_task_detail_table)
    except (click.BadParameter, PoundCakeClientError) as exc:
        print_error(f"Failed to update scheduled task {task_id}: {exc}")
        raise click.Abort() from exc


@scheduled_tasks.command("delete")
@click.argument("task_id", type=int)
@click.pass_context
def delete_task(ctx: click.Context, task_id: int) -> None:
    """Disable a scheduled task."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        response = client.delete_scheduled_task(task_id)
        print_output(response, output_format, table_renderer=_task_detail_table)
    except PoundCakeClientError as exc:
        print_error(f"Failed to delete scheduled task {task_id}: {exc}")
        raise click.Abort() from exc


@scheduled_tasks.command("run-now")
@click.argument("task_id", type=int)
@click.pass_context
def run_now(ctx: click.Context, task_id: int) -> None:
    """Request an immediate run for a plugin-manifest scheduled task."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        response = client.run_scheduled_task_now(task_id)
        print_output(response, output_format, table_renderer=_task_detail_table)
    except PoundCakeClientError as exc:
        print_error(f"Failed to request run-now for scheduled task {task_id}: {exc}")
        raise click.Abort() from exc
