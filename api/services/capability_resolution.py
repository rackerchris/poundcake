"""Shared helpers for resolving advertised capabilities to active ingredients."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.models import Ingredient
from api.plugins.capability_matrix import PROVIDER_SELECTION_PRECEDENCE
from api.types import JSONObject


@dataclass(frozen=True, slots=True)
class ResolvedCapabilityIngredient:
    """One normalized capability bound to an active immutable ingredient."""

    capability_id: str
    service_type: str
    mode: str
    operation: str
    defaults: JSONObject
    priority: int | None
    ingredient: Ingredient


def capability_sort_key(capability: JSONObject) -> tuple[int, int, str]:
    """Return the stable precedence key for one normalized capability."""
    return (
        PROVIDER_SELECTION_PRECEDENCE.get(
            str(capability.get("service_type") or "").strip().lower(),
            99,
        ),
        -(int(capability.get("priority") or 0)),
        str(capability.get("capability_id") or "").strip().lower(),
    )


def communication_capability_matches(
    capability: JSONObject,
    *,
    service_type: str | None = None,
    destination_target: str | None = None,
) -> bool:
    """Return True when a capability can back a communication route."""
    if capability.get("enabled") is False:
        return False
    if str(capability.get("mode") or "").strip().lower() != "communication":
        return False
    if str(capability.get("operation") or "").strip().lower() != "open":
        return False
    normalized_service_type = str(capability.get("service_type") or "").strip().lower()
    if service_type and normalized_service_type != service_type.strip().lower():
        return False
    ingredient_ref = capability.get("ingredient_ref")
    if not isinstance(ingredient_ref, dict):
        return False
    normalized_target = str(ingredient_ref.get("destination_target") or "").strip()
    if destination_target is not None and normalized_target != destination_target.strip():
        return False
    return True


def select_communication_capability(
    capabilities: list[JSONObject],
    *,
    service_type: str | None = None,
    destination_target: str | None = None,
) -> JSONObject | None:
    """Choose the best enabled communication capability for a route."""
    matches = [
        capability
        for capability in capabilities
        if communication_capability_matches(
            capability,
            service_type=service_type,
            destination_target=destination_target,
        )
    ]
    if not matches:
        return None
    return sorted(matches, key=capability_sort_key)[0]


def communication_routes_from_capability_catalog(
    capabilities: list[JSONObject],
) -> list[JSONObject]:
    """Translate communication capabilities into available route contracts."""
    routes: list[JSONObject] = []
    for position, capability in enumerate(
        sorted(
            [
                capability
                for capability in capabilities
                if communication_capability_matches(capability)
            ],
            key=capability_sort_key,
        ),
        start=1,
    ):
        ingredient_ref = capability.get("ingredient_ref") or {}
        service_type = str(capability.get("service_type") or "").strip().lower()
        destination_target = str(ingredient_ref.get("destination_target") or "").strip()
        title = service_type.replace("_", " ").title()
        label = f"{title} - {destination_target}" if destination_target else title
        routes.append(
            {
                "id": str(capability.get("capability_id") or "").strip().lower(),
                "label": label,
                "service_type": service_type,
                "destination_target": destination_target,
                "provider_config": {},
                "enabled": True,
                "position": position,
            }
        )
    return routes


async def resolve_active_capability_ingredient(
    db: AsyncSession,
    *,
    capability: JSONObject,
) -> ResolvedCapabilityIngredient | None:
    """Resolve one normalized capability to its active immutable ingredient row."""
    ingredient_ref = capability.get("ingredient_ref")
    if not isinstance(ingredient_ref, dict):
        return None
    service_type = str(capability.get("service_type") or "").strip().lower()
    service_exec = str(ingredient_ref.get("service_exec") or "").strip().lower()
    task_key_template = str(ingredient_ref.get("task_key_template") or "").strip()
    destination_target = str(ingredient_ref.get("destination_target") or "").strip()
    operation = str(capability.get("operation") or "").strip().lower()
    if not service_type or not service_exec or not task_key_template or not operation:
        return None
    result = await db.execute(
        select(Ingredient).where(
            Ingredient.service_type == service_type,
            Ingredient.service_exec == service_exec,
            Ingredient.task_key_template == task_key_template,
            Ingredient.destination_target == destination_target,
            Ingredient.is_active.is_(True),
            Ingredient.deleted.is_(False),
        )
    )
    ingredient = result.scalars().first()
    if ingredient is None:
        return None
    params = ingredient.service_exec_parameters or {}
    raw_allowed = params.get("allowed_operations")
    allowed_operations = (
        {str(item).strip().lower() for item in raw_allowed if str(item).strip()}
        if isinstance(raw_allowed, list)
        else set()
    )
    if operation not in allowed_operations:
        return None
    return ResolvedCapabilityIngredient(
        capability_id=str(capability.get("capability_id") or "").strip().lower(),
        service_type=service_type,
        mode=str(capability.get("mode") or "").strip().lower(),
        operation=operation,
        defaults=dict(capability.get("defaults") or {}),
        priority=(
            int(capability.get("priority") or 0) if capability.get("priority") is not None else None
        ),
        ingredient=ingredient,
    )
