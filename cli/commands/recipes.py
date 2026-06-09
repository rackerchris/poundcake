"""Recipe commands for the PoundCake CLI."""

from __future__ import annotations

from shared.types import JSONObject

from pathlib import Path
from typing import Any

import click

from cli.client import PoundCakeClient, PoundCakeClientError
from cli.commands.common import (
    build_preview_changes,
    compact_update_payload,
    get_client,
    get_output_format,
    merge_preview_state,
    print_dry_run_preview,
    read_mapping_file,
    validate_request_payload,
)
from cli.utils import parse_json_object, print_error, print_output, render_sections, to_plain_data
from api.schemas.schemas import RecipeCreate, RecipeUpdate


def _recipe_rows(rows: list[JSONObject]) -> list[JSONObject]:
    return [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "enabled": item.get("enabled"),
            "communications_mode": (item.get("communications") or {}).get("mode"),
            "routes": len((item.get("communications") or {}).get("routes") or []),
            "steps": len(item.get("recipe_ingredients") or []),
            "updated_at": item.get("updated_at"),
        }
        for item in rows
    ]


def _recipe_detail_table(item: JSONObject) -> str:
    communications = item.get("communications") or {}
    return render_sections(
        [
            (
                "Recipe",
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "description": item.get("description"),
                    "enabled": item.get("enabled"),
                    "clear_timeout_sec": item.get("clear_timeout_sec"),
                    "communications_mode": communications.get("mode"),
                    "communications_source": communications.get("effective_source"),
                    "updated_at": item.get("updated_at"),
                },
            ),
            (
                "Communication Routes",
                [
                    {
                        "id": route.get("id"),
                        "label": route.get("label"),
                        "target": route.get("service_type"),
                        "destination": route.get("destination_target"),
                        "enabled": route.get("enabled"),
                        "position": route.get("position"),
                    }
                    for route in communications.get("routes") or []
                ],
            ),
            (
                "Steps",
                [
                    {
                        "step_order": step.get("step_order"),
                        "ingredient_id": step.get("ingredient_id"),
                        "on_success": step.get("on_success"),
                        "run_phase": step.get("run_phase"),
                        "run_condition": step.get("run_condition"),
                        "parallel_group": step.get("parallel_group"),
                        "depth": step.get("depth"),
                    }
                    for step in item.get("recipe_ingredients") or []
                ],
            ),
        ]
    )


def _recipe_status_rows(rows: list[JSONObject]) -> list[JSONObject]:
    return [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "enabled": item.get("enabled"),
            "can_execute": item.get("can_execute"),
            "inactive_ingredient_count": item.get("inactive_ingredient_count"),
            "step_count": item.get("step_count"),
            "communication_route_count": item.get("communication_route_count"),
            "updated_at": item.get("updated_at"),
        }
        for item in rows
    ]


def _recipe_ingredient_status_table(rows: list[JSONObject]) -> str:
    return render_sections(
        [
            (
                "Recipe Step Status",
                [
                    {
                        "id": item.get("id"),
                        "ingredient_id": item.get("ingredient_id"),
                        "step_order": item.get("step_order"),
                        "service_type": item.get("service_type"),
                        "service_exec": item.get("service_exec"),
                        "task_key_template": item.get("task_key_template"),
                        "ingredient_is_active": item.get("ingredient_is_active"),
                        "expected_secs": item.get("expected_secs"),
                        "timeout_secs": item.get("timeout_secs"),
                    }
                    for item in rows
                ],
            )
        ]
    )


def _normalize_recipe_step(step: JSONObject, *, index: int) -> JSONObject:
    ingredient_id = step.get("ingredient_id")
    if ingredient_id is None:
        raise click.BadParameter("Each step must include ingredient_id")
    return {
        "ingredient_id": int(ingredient_id),
        "step_order": index,
        "on_success": step.get("on_success", "continue"),
        "parallel_group": int(step.get("parallel_group", 0)),
        "depth": int(step.get("depth", 0)),
        "service_exec_parameters_override": step.get(
            "service_exec_parameters_override",
            step.get("execution_parameters_override"),
        ),
        "run_phase": step.get("run_phase", "both"),
        "run_condition": step.get("run_condition", "always"),
    }


def _normalize_route(route: JSONObject, *, index: int) -> JSONObject:
    label = route.get("label")
    service_type = route.get("service_type") or route.get("execution_target")
    if not label or not service_type:
        raise click.BadParameter("Each route must include label and service_type")
    normalized = {
        "label": label,
        "service_type": service_type,
        "destination_target": route.get("destination_target", ""),
        "provider_config": route.get("provider_config", {}),
        "enabled": bool(route.get("enabled", True)),
        "position": index,
    }
    if route.get("id") is not None:
        normalized["id"] = route["id"]
    return normalized


def _ensure_file_is_exclusive(file: Path | None, inline_values: list[Any]) -> None:
    if file is None:
        return
    if any(value not in (None, "", (), []) for value in inline_values):
        raise click.BadParameter("--file cannot be combined with inline recipe options")


def _validate_non_recipe_steps(client: PoundCakeClient, steps: list[JSONObject]) -> None:
    ingredient_ids = sorted({int(step["ingredient_id"]) for step in steps})
    for ingredient_id in ingredient_ids:
        ingredient = client.get_ingredient(ingredient_id)
        if getattr(ingredient, "ingredient_purpose", None) == "comms":
            raise click.BadParameter(
                f"Ingredient {ingredient_id} is a managed communication action and cannot be used as a recipe step"
            )


def _build_recipe_payload(
    client: PoundCakeClient,
    *,
    file: Path | None,
    name: str | None,
    description: str | None,
    enabled: bool | None,
    clear_timeout_sec: int | None,
    communications_mode: str | None,
    step_json: tuple[str, ...],
    route_json: tuple[str, ...],
    creating: bool,
) -> JSONObject:
    _ensure_file_is_exclusive(
        file,
        [
            name,
            description,
            enabled,
            clear_timeout_sec,
            communications_mode,
            step_json,
            route_json,
        ],
    )
    if file is not None:
        payload = read_mapping_file(file, "recipe file")
        step_items = payload.get("recipe_ingredients") or []
        if not isinstance(step_items, list):
            raise click.BadParameter("recipe file recipe_ingredients must be a list")
        normalized_steps = [
            _normalize_recipe_step(item, index=index + 1) for index, item in enumerate(step_items)
        ]
        if normalized_steps:
            _validate_non_recipe_steps(client, normalized_steps)
            payload["recipe_ingredients"] = normalized_steps
        communications = payload.get("communications")
        if isinstance(communications, dict):
            routes = communications.get("routes") or []
            if not isinstance(routes, list):
                raise click.BadParameter("recipe file communications.routes must be a list")
            payload["communications"] = {
                "mode": communications.get("mode", "inherit"),
                "routes": [
                    _normalize_route(item, index=index + 1) for index, item in enumerate(routes)
                ],
            }
        return payload

    parsed_steps = [
        _normalize_recipe_step(parse_json_object(raw, "step-json") or {}, index=index + 1)
        for index, raw in enumerate(step_json)
    ]
    parsed_routes = [
        _normalize_route(parse_json_object(raw, "route-json") or {}, index=index + 1)
        for index, raw in enumerate(route_json)
    ]
    if parsed_steps:
        _validate_non_recipe_steps(client, parsed_steps)
    resolved_mode = communications_mode
    if resolved_mode is None and parsed_routes:
        resolved_mode = "local"
    if resolved_mode is None and creating:
        resolved_mode = "inherit"

    payload = compact_update_payload(
        {
            "name": name,
            "description": description,
            "enabled": enabled,
            "clear_timeout_sec": clear_timeout_sec,
        }
    )
    if creating and "enabled" not in payload:
        payload["enabled"] = True
    if parsed_steps:
        payload["recipe_ingredients"] = parsed_steps
    elif creating:
        raise click.BadParameter("At least one --step-json is required when creating a recipe")

    if resolved_mode is not None or parsed_routes:
        payload["communications"] = {
            "mode": resolved_mode or "local",
            "routes": parsed_routes if (resolved_mode or "local") == "local" else [],
        }
    return payload


@click.group(name="recipes")
def recipes() -> None:
    """Manage recipes."""


@recipes.command("list")
@click.option("--name", default=None)
@click.option("--enabled", "enabled_filter", flag_value=True, default=None)
@click.option("--disabled", "enabled_filter", flag_value=False)
@click.option("--limit", type=int, default=500, show_default=True)
@click.option("--offset", type=int, default=0, show_default=True)
@click.pass_context
def list_recipes(
    ctx: click.Context,
    name: str | None,
    enabled_filter: bool | None,
    limit: int,
    offset: int,
) -> None:
    """List recipes."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = client.list_recipes(name=name, enabled=enabled_filter, limit=limit, offset=offset)
        if output_format == "table":
            print_output(_recipe_rows(to_plain_data(payload)), output_format)
            return
        print_output(payload, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Failed to list recipes: {exc}")
        raise click.Abort() from exc


@recipes.command("show")
@click.argument("recipe_id", type=int)
@click.pass_context
def show_recipe(ctx: click.Context, recipe_id: int) -> None:
    """Show a recipe."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = client.get_recipe(recipe_id)
        print_output(payload, output_format, table_renderer=_recipe_detail_table)
    except PoundCakeClientError as exc:
        print_error(f"Failed to show recipe: {exc}")
        raise click.Abort() from exc


@recipes.command("show-by-name")
@click.argument("recipe_name")
@click.pass_context
def show_recipe_by_name(ctx: click.Context, recipe_name: str) -> None:
    """Show a recipe by name."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = client.get_recipe_by_name(recipe_name)
        print_output(payload, output_format, table_renderer=_recipe_detail_table)
    except PoundCakeClientError as exc:
        print_error(f"Failed to show recipe by name: {exc}")
        raise click.Abort() from exc


@recipes.command("status")
@click.option("--name", default=None)
@click.option("--enabled", "enabled_filter", flag_value=True, default=None)
@click.option("--disabled", "enabled_filter", flag_value=False)
@click.option("--limit", type=int, default=500, show_default=True)
@click.option("--offset", type=int, default=0, show_default=True)
@click.pass_context
def recipe_status(
    ctx: click.Context,
    name: str | None,
    enabled_filter: bool | None,
    limit: int,
    offset: int,
) -> None:
    """List reader-safe recipe status rows."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = client.list_recipe_statuses(
            name=name,
            enabled=enabled_filter,
            limit=limit,
            offset=offset,
        )
        if output_format == "table":
            print_output(_recipe_status_rows(to_plain_data(payload)), output_format)
            return
        print_output(payload, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Failed to list recipe status: {exc}")
        raise click.Abort() from exc


@recipes.command("status-show")
@click.argument("recipe_id", type=int)
@click.pass_context
def show_recipe_status(ctx: click.Context, recipe_id: int) -> None:
    """Show one reader-safe recipe status row."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = client.get_recipe_status(recipe_id)
        print_output(payload, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Failed to show recipe status: {exc}")
        raise click.Abort() from exc


@recipes.command("ingredient-status")
@click.argument("recipe_id", type=int)
@click.pass_context
def recipe_ingredient_status(ctx: click.Context, recipe_id: int) -> None:
    """Show reader-safe recipe step status rows."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = client.get_recipe_ingredient_status(recipe_id)
        print_output(payload, output_format, table_renderer=_recipe_ingredient_status_table)
    except PoundCakeClientError as exc:
        print_error(f"Failed to show recipe ingredient status: {exc}")
        raise click.Abort() from exc


@recipes.command("create")
@click.option(
    "--file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="JSON or YAML recipe payload",
)
@click.option("--name", default=None)
@click.option("--description", default=None)
@click.option("--enabled", "enabled_value", flag_value=True, default=None)
@click.option("--disabled", "enabled_value", flag_value=False)
@click.option("--clear-timeout-sec", type=int, default=None)
@click.option("--communications-mode", type=click.Choice(["inherit", "local"]), default=None)
@click.option("--step-json", multiple=True, help="JSON object describing one recipe step")
@click.option("--route-json", multiple=True, help="JSON object describing one communication route")
@click.option("--dry-run", is_flag=True, help="Validate and preview the recipe payload without saving it")
@click.pass_context
def create_recipe(
    ctx: click.Context,
    file: Path | None,
    name: str | None,
    description: str | None,
    enabled_value: bool | None,
    clear_timeout_sec: int | None,
    communications_mode: str | None,
    step_json: tuple[str, ...],
    route_json: tuple[str, ...],
    dry_run: bool,
) -> None:
    """Create a recipe."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = _build_recipe_payload(
            client,
            file=file,
            name=name,
            description=description,
            enabled=enabled_value,
            clear_timeout_sec=clear_timeout_sec,
            communications_mode=communications_mode,
            step_json=step_json,
            route_json=route_json,
            creating=True,
        )
        validated = validate_request_payload(
            client,
            payload,
            RecipeCreate,
            "Invalid create recipe payload",
        )
        if dry_run:
            communications = validated.get("communications") or {}
            routes = communications.get("routes") or []
            next_recipe = {
                "name": validated.get("name"),
                "enabled": bool(validated.get("enabled")),
                "communications_mode": communications.get("mode") or "inherit",
                "enabled_route_count": sum(1 for route in routes if route.get("enabled")),
                "step_count": len(validated.get("recipe_ingredients") or []),
            }
            print_dry_run_preview(
                ctx,
                command="recipes create",
                target=str(validated.get("name") or "new recipe"),
                payload=validated,
                summary={
                    "enabled": bool(validated.get("enabled")),
                    "step_count": len(validated.get("recipe_ingredients") or []),
                    "communications_mode": communications.get("mode") or "inherit",
                    "enabled_route_count": sum(1 for route in routes if route.get("enabled")),
                },
                impact="If this recipe is enabled, new matching work can use these steps and communication routes immediately after save.",
                changes=build_preview_changes({}, next_recipe, labels={
                    "name": "Recipe",
                    "enabled": "Enabled",
                    "communications_mode": "Communications",
                    "enabled_route_count": "Enabled routes",
                    "step_count": "Steps",
                }),
            )
            return
        response = client.create_recipe(validated)
        print_output(response, output_format, table_renderer=_recipe_detail_table)
    except (click.BadParameter, PoundCakeClientError) as exc:
        print_error(f"Failed to create recipe: {exc}")
        raise click.Abort() from exc


@recipes.command("update")
@click.argument("recipe_id", type=int)
@click.option(
    "--file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="JSON or YAML recipe payload",
)
@click.option("--name", default=None)
@click.option("--description", default=None)
@click.option("--enabled", "enabled_value", flag_value=True, default=None)
@click.option("--disabled", "enabled_value", flag_value=False)
@click.option("--clear-timeout-sec", type=int, default=None)
@click.option("--communications-mode", type=click.Choice(["inherit", "local"]), default=None)
@click.option("--step-json", multiple=True, help="JSON object describing one recipe step")
@click.option("--route-json", multiple=True, help="JSON object describing one communication route")
@click.option("--dry-run", is_flag=True, help="Validate and preview the recipe update without saving it")
@click.pass_context
def update_recipe(
    ctx: click.Context,
    recipe_id: int,
    file: Path | None,
    name: str | None,
    description: str | None,
    enabled_value: bool | None,
    clear_timeout_sec: int | None,
    communications_mode: str | None,
    step_json: tuple[str, ...],
    route_json: tuple[str, ...],
    dry_run: bool,
) -> None:
    """Update a recipe."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        payload = _build_recipe_payload(
            client,
            file=file,
            name=name,
            description=description,
            enabled=enabled_value,
            clear_timeout_sec=clear_timeout_sec,
            communications_mode=communications_mode,
            step_json=step_json,
            route_json=route_json,
            creating=False,
        )
        if not payload:
            raise click.BadParameter("No update fields provided")
        validated = validate_request_payload(
            client,
            payload,
            RecipeUpdate,
            "Invalid update recipe payload",
        )
        if dry_run:
            current_recipe = client.get_recipe(recipe_id).model_dump(mode="json", by_alias=True)
            next_recipe = merge_preview_state(current_recipe, validated)
            communications = validated.get("communications") or {}
            routes = communications.get("routes") or []
            print_dry_run_preview(
                ctx,
                command="recipes update",
                target=f"recipe {recipe_id}",
                payload=validated,
                summary={
                    "updated_fields": sorted(validated.keys()),
                    "step_count": len(validated.get("recipe_ingredients") or []),
                    "communications_mode": communications.get("mode") or "-",
                    "enabled_route_count": sum(1 for route in routes if route.get("enabled")),
                },
                impact="Any updated recipe fields will affect future matching work immediately after save.",
                changes=build_preview_changes(
                    {
                        "name": current_recipe.get("name"),
                        "enabled": current_recipe.get("enabled"),
                        "clear_timeout_sec": current_recipe.get("clear_timeout_sec"),
                        "communications_mode": (current_recipe.get("communications") or {}).get("mode"),
                        "enabled_route_count": sum(
                            1 for route in (current_recipe.get("communications") or {}).get("routes", [])
                            if isinstance(route, dict) and route.get("enabled")
                        ),
                        "step_count": len(current_recipe.get("recipe_ingredients") or []),
                    },
                    {
                        "name": next_recipe.get("name"),
                        "enabled": next_recipe.get("enabled"),
                        "clear_timeout_sec": next_recipe.get("clear_timeout_sec"),
                        "communications_mode": (next_recipe.get("communications") or {}).get("mode"),
                        "enabled_route_count": sum(
                            1 for route in (next_recipe.get("communications") or {}).get("routes", [])
                            if isinstance(route, dict) and route.get("enabled")
                        ),
                        "step_count": len(next_recipe.get("recipe_ingredients") or []),
                    },
                    labels={
                        "name": "Recipe",
                        "enabled": "Enabled",
                        "clear_timeout_sec": "Resolve wait (sec)",
                        "communications_mode": "Communications",
                        "enabled_route_count": "Enabled routes",
                        "step_count": "Steps",
                    },
                ),
            )
            return
        response = client.update_recipe(recipe_id, validated)
        print_output(response, output_format, table_renderer=_recipe_detail_table)
    except (click.BadParameter, PoundCakeClientError) as exc:
        print_error(f"Failed to update recipe: {exc}")
        raise click.Abort() from exc


@recipes.command("delete")
@click.argument("recipe_id", type=int)
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def delete_recipe(ctx: click.Context, recipe_id: int, yes: bool) -> None:
    """Delete a recipe."""
    client = get_client(ctx)
    output_format = get_output_format(ctx)
    try:
        recipe = client.get_recipe(recipe_id)
        if not yes:
            click.confirm(f"Delete recipe '{recipe.name or recipe_id}'?", abort=True)
        response = client.delete_recipe(recipe_id)
        print_output(response, output_format)
    except PoundCakeClientError as exc:
        print_error(f"Failed to delete recipe: {exc}")
        raise click.Abort() from exc
