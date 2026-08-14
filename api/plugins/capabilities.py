"""Plugin capability contract helpers."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence

from api.types import JSONObject

CAPABILITY_MODES = {
    "action",
    "workflow",
    "inspection",
    "communication",
    "content_sync",
    "utility",
}
CAPABILITY_SAFETY_CLASSES = {
    "observe_only",
    "operator_guidance",
    "safe_restart",
    "bounded_scale",
    "destructive",
}
ALLOWED_CAPABILITY_OVERRIDE_FIELDS = frozenset(
    {
        "priority",
        "defaults",
        "workflow_ref",
        "action_ref",
    }
)


class ServicePluginCapabilityError(ValueError):
    """Raised when a plugin capability declaration is malformed."""


def validate_capability_template(
    template: object,
    *,
    service_type: str,
    ingredient_templates: Sequence[JSONObject],
    label: str,
) -> JSONObject:
    """Validate and normalize one plugin capability template."""
    if not isinstance(template, dict):
        raise ServicePluginCapabilityError(f"{label} must be an object")

    capability_id = str(template.get("capability_id") or "").strip().lower()
    if not capability_id:
        raise ServicePluginCapabilityError(f"{label}.capability_id must not be empty")
    mode = str(template.get("mode") or "").strip().lower()
    if mode not in CAPABILITY_MODES:
        raise ServicePluginCapabilityError(
            f"{label}.mode must be one of {sorted(CAPABILITY_MODES)}"
        )

    ingredient_ref = template.get("ingredient_ref")
    if not isinstance(ingredient_ref, dict):
        raise ServicePluginCapabilityError(f"{label}.ingredient_ref must be an object")
    service_exec = str(ingredient_ref.get("service_exec") or "").strip().lower()
    task_key_template = str(ingredient_ref.get("task_key_template") or "").strip()
    destination_target = str(ingredient_ref.get("destination_target") or "").strip()
    if not service_exec:
        raise ServicePluginCapabilityError(f"{label}.ingredient_ref.service_exec must not be empty")
    if not task_key_template:
        raise ServicePluginCapabilityError(
            f"{label}.ingredient_ref.task_key_template must not be empty"
        )

    ingredient_template = _resolve_ingredient_template(
        ingredient_templates,
        service_type=service_type,
        service_exec=service_exec,
        task_key_template=task_key_template,
        destination_target=destination_target,
    )
    if ingredient_template is None:
        raise ServicePluginCapabilityError(
            f"{label}.ingredient_ref must resolve to an immutable ingredient template"
        )

    operation = str(template.get("operation") or "").strip()
    allowed_operations = _allowed_operations(ingredient_template)
    if not operation:
        raise ServicePluginCapabilityError(f"{label}.operation must not be empty")
    if operation not in allowed_operations:
        raise ServicePluginCapabilityError(
            f"{label}.operation must be one of advertised ingredient operations: "
            + ", ".join(sorted(allowed_operations))
        )

    safety_class = str(template.get("safety_class") or "").strip().lower()
    if safety_class and safety_class not in CAPABILITY_SAFETY_CLASSES:
        raise ServicePluginCapabilityError(
            f"{label}.safety_class must be one of {sorted(CAPABILITY_SAFETY_CLASSES)}"
        )

    defaults = template.get("defaults")
    if defaults is not None and not isinstance(defaults, dict):
        raise ServicePluginCapabilityError(f"{label}.defaults must be an object")

    trigger_match = template.get("trigger_match")
    if trigger_match is not None and not isinstance(trigger_match, dict):
        raise ServicePluginCapabilityError(f"{label}.trigger_match must be an object")

    return {
        **copy.deepcopy(template),
        "capability_id": capability_id,
        "service_type": service_type,
        "mode": mode,
        "operation": operation,
        "safety_class": safety_class or "observe_only",
        "ingredient_ref": {
            "service_type": service_type,
            "service_exec": service_exec,
            "task_key_template": task_key_template,
            "destination_target": destination_target,
        },
        "resource_kinds": _normalized_string_list(template.get("resource_kinds")),
        "required_inputs": _normalized_string_list(template.get("required_inputs")),
        "optional_inputs": _normalized_string_list(template.get("optional_inputs")),
        "trigger_match": copy.deepcopy(trigger_match or {}),
        "defaults": copy.deepcopy(defaults or {}),
        "requires_evidence": bool(template.get("requires_evidence", True)),
        "priority": _optional_int(template.get("priority")),
    }


def apply_operator_capability_overrides(
    template: JSONObject,
    *,
    operator_config: Mapping[str, object] | None = None,
) -> JSONObject:
    """Apply bounded operator overrides to a normalized capability template."""
    config = operator_config if isinstance(operator_config, Mapping) else {}
    enabled_map = config.get("capabilities_enabled")
    override_map = config.get("capability_overrides")
    enabled_by_operator = enabled_map if isinstance(enabled_map, Mapping) else {}
    overrides = override_map if isinstance(override_map, Mapping) else {}

    capability_id = str(template.get("capability_id") or "").strip().lower()
    result = copy.deepcopy(template)
    result["enabled"] = True
    result["disabled_reason"] = None

    if capability_id in enabled_by_operator and not bool(enabled_by_operator[capability_id]):
        result["enabled"] = False
        result["disabled_reason"] = "operator_disabled"

    raw_override = overrides.get(capability_id)
    if not isinstance(raw_override, Mapping):
        return result
    sanitized_override = {
        str(key): value
        for key, value in raw_override.items()
        if str(key) in ALLOWED_CAPABILITY_OVERRIDE_FIELDS
    }

    priority = _optional_int(sanitized_override.get("priority"))
    if priority is not None:
        result["priority"] = priority

    defaults = result.setdefault("defaults", {})
    if not isinstance(defaults, dict):
        defaults = {}
        result["defaults"] = defaults
    override_defaults = sanitized_override.get("defaults")
    if isinstance(override_defaults, Mapping):
        defaults.update(copy.deepcopy(dict(override_defaults)))

    service_payload = defaults.get("service_payload")
    if not isinstance(service_payload, dict):
        service_payload = {}
        defaults["service_payload"] = service_payload
    workflow_ref = str(sanitized_override.get("workflow_ref") or "").strip()
    if workflow_ref:
        service_payload["workflow_ref"] = workflow_ref
    action_ref = str(sanitized_override.get("action_ref") or "").strip()
    if action_ref:
        service_payload["action_ref"] = action_ref

    return result


def _resolve_ingredient_template(
    ingredient_templates: Sequence[JSONObject],
    *,
    service_type: str,
    service_exec: str,
    task_key_template: str,
    destination_target: str,
) -> JSONObject | None:
    normalized_service_type = service_type.strip().lower()
    normalized_service_exec = service_exec.strip().lower()
    for template in ingredient_templates:
        if str(template.get("service_type") or "").strip().lower() != normalized_service_type:
            continue
        if str(template.get("service_exec") or "").strip().lower() != normalized_service_exec:
            continue
        if str(template.get("task_key_template") or "").strip() != task_key_template:
            continue
        if (
            destination_target
            and str(template.get("destination_target") or "").strip() != destination_target
        ):
            continue
        return template
    return None


def _allowed_operations(template: JSONObject) -> set[str]:
    params = template.get("service_exec_parameters")
    if not isinstance(params, dict):
        return set()
    raw_allowed = params.get("allowed_operations")
    if not isinstance(raw_allowed, list):
        return set()
    return {str(item).strip() for item in raw_allowed if str(item).strip()}


def _normalized_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _optional_int(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
