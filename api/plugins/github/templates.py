"""GitHub plugin templates."""

from __future__ import annotations

from api.plugins.contract import health_check_operation_parameters
from api.types import JSONObject


def _schema(properties: JSONObject, required: list[str] | None = None) -> JSONObject:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


_REPO_PROPS: JSONObject = {
    "repo": {"type": "string", "minLength": 1},
    "ref": {"type": "string", "minLength": 1},
    "base_branch": {"type": "string", "minLength": 1},
}

_READ_PROPS: JSONObject = {
    **_REPO_PROPS,
    "path": {"type": "string", "minLength": 1},
    "recursive": {"type": "boolean"},
}
_WRITE_PROPS: JSONObject = {
    **_REPO_PROPS,
    "branch": {"type": "string", "minLength": 1},
    "files": {
        "type": "object",
        "additionalProperties": {"type": "string"},
        "minProperties": 1,
    },
    "message": {"type": "string", "minLength": 1},
    "title": {"type": "string", "minLength": 1},
    "body": {"type": "string"},
    "commit_message": {"type": "string", "minLength": 1},
}
_READ_FILE_SCHEMA = _schema(_READ_PROPS, required=["path"])
_LIST_FILES_SCHEMA = _schema(
    {**_REPO_PROPS, "path": {"type": "string"}, "recursive": {"type": "boolean"}}
)
_COMMIT_FILES_SCHEMA = _schema(_WRITE_PROPS, required=["branch", "files"])
_CREATE_PULL_REQUEST_SCHEMA = _schema(_WRITE_PROPS, required=["branch", "title"])
_COMMIT_AND_PR_SCHEMA = _schema(_WRITE_PROPS, required=["branch", "files", "title"])


GITHUB_INGREDIENT_TEMPLATES: tuple[JSONObject, ...] = (
    {
        "service_type": "github",
        "service_exec": "health_check",
        "destination_target": "github",
        "task_key_template": "github-health-check",
        "payload_schema": _schema({}),
        "service_payload_template": {},
        "service_exec_parameters": health_check_operation_parameters(),
        "default_expected_secs": 5,
        "default_timeout": 30,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "plugin_health",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "continue",
    },
    {
        "service_type": "github",
        "service_exec": "repo_read",
        "destination_target": "github",
        "task_key_template": "github-repo-read",
        "payload_schema": _schema(
            {**_REPO_PROPS, "path": {"type": "string"}, "recursive": {"type": "boolean"}}
        ),
        "service_payload_template": {"path": "", "recursive": True},
        "service_exec_parameters": {
            "operation": "read_file",
            "allowed_operations": ["read_file", "list_files"],
            "operation_metadata": {
                "read_file": {
                    "label": "Read file",
                    "description": "Read one file.",
                    "payload_schema": _READ_FILE_SCHEMA,
                },
                "list_files": {
                    "label": "List files",
                    "description": "List repository files.",
                    "payload_schema": _LIST_FILES_SCHEMA,
                },
            },
        },
        "default_expected_secs": 5,
        "default_timeout": 60,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "utility",
        "is_blocking": True,
        "retry_count": 1,
        "retry_delay": 2,
        "on_failure": "stop",
    },
    {
        "service_type": "github",
        "service_exec": "repo_write",
        "destination_target": "github",
        "task_key_template": "github-repo-write",
        "payload_schema": _schema(
            {
                **_REPO_PROPS,
                "branch": {"type": "string", "minLength": 1},
                "files": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "minProperties": 1,
                },
                "message": {"type": "string", "minLength": 1},
                "title": {"type": "string", "minLength": 1},
                "body": {"type": "string"},
                "commit_message": {"type": "string", "minLength": 1},
            }
        ),
        "service_payload_template": {},
        "service_exec_parameters": {
            "operation": "commit_files",
            "allowed_operations": ["commit_files", "create_pull_request", "commit_and_pr"],
            "operation_metadata": {
                "commit_files": {
                    "label": "Commit files",
                    "description": "Commit file changes.",
                    "payload_schema": _COMMIT_FILES_SCHEMA,
                },
                "create_pull_request": {
                    "label": "Create pull request",
                    "description": "Open a pull request for an existing branch.",
                    "payload_schema": _CREATE_PULL_REQUEST_SCHEMA,
                },
                "commit_and_pr": {
                    "label": "Commit and PR",
                    "description": "Commit files and open a pull request.",
                    "payload_schema": _COMMIT_AND_PR_SCHEMA,
                },
            },
        },
        "default_expected_secs": 15,
        "default_timeout": 180,
        "service_exec_expected_outcome_default": {"success": True},
        "ingredient_purpose": "utility",
        "is_blocking": True,
        "retry_count": 0,
        "retry_delay": 0,
        "on_failure": "stop",
    },
)


GITHUB_RECIPE_TEMPLATES: tuple[JSONObject, ...] = (
    {
        "name": "plugin-health-check:github",
        "description": "Scheduled health check for the GitHub service plugin.",
        "enabled": True,
        "recipe_ingredients": [
            {
                "service_type": "github",
                "service_exec": "health_check",
                "destination_target": "github",
                "task_key_template": "github-health-check",
                "step_order": 1,
                "service_payload": {},
                "service_exec_expected_secs": 5,
                "service_exec_timeout": 30,
                "service_exec_expected_outcome": {"success": True},
                "run_phase": "firing",
                "run_condition": "always",
            }
        ],
    },
)


GITHUB_SCHEDULED_TASKS: tuple[JSONObject, ...] = (
    {
        "task_key": "plugin-health-check:github",
        "task_type": "plugin_health_check",
        "service_type": "github",
        "service_exec": "health_check",
        "source": "plugin_manifest",
        "is_enabled": True,
        "run_interval_seconds": 60,
        "priority": 20,
        "timeout_seconds": 30,
        "task_payload": {},
        "task_parameters": health_check_operation_parameters(),
        "expected_outcome": {"success": True},
    },
)
