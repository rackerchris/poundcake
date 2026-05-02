"""Unit tests for service registry API surface."""

from __future__ import annotations

import pytest

from api.models.models import Ingredient
from api.schemas.schemas import IngredientTemplateRegistration
from api.services.ingredient_registry import ingredient_contract_from_row


def _ingredient() -> Ingredient:
    return Ingredient(
        id=11,
        service_type="dummy",
        service_exec="positive_result",
        destination_target="dummy",
        task_key_template="dummy-positive-result",
        service_payload_template={"message": "template"},
        payload_schema={
            "type": "object",
            "properties": {"message": {"type": "string", "minLength": 1}},
            "required": ["message"],
            "additionalProperties": False,
        },
        service_exec_parameters=None,
        default_expected_secs=1,
        default_timeout=30,
        service_exec_expected_outcome_default={"success": True},
        ingredient_purpose="utility",
        is_active=True,
        is_blocking=True,
        retry_count=0,
        retry_delay=0,
        on_failure="stop",
        deleted=False,
    )


def test_registration_contract_rejects_control_plane_fields() -> None:
    payload_data = ingredient_contract_from_row(_ingredient())
    payload_data["is_active"] = False
    with pytest.raises(ValueError):
        IngredientTemplateRegistration.model_validate(payload_data)


def test_public_service_registry_write_routes_are_removed() -> None:
    from api.main import app

    route_keys = {
        (method, getattr(route, "path", ""))
        for route in app.routes
        for method in (getattr(route, "methods", set()) or set())
    }
    assert ("POST", "/api/v1/service-registry/ingredients") not in route_keys
    assert ("POST", "/api/v1/service-registry/ingredients/bulk") not in route_keys
    assert (
        "PATCH",
        "/api/v1/service-registry/ingredients/{ingredient_id}/retire",
    ) not in route_keys
    assert ("POST", "/api/v1/internal/service-registry/ingredients/bulk") in route_keys
