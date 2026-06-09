"""Composable Git capability templates."""

from __future__ import annotations

from api.types import JSONObject


def load_git_capability_templates() -> tuple[JSONObject, ...]:
    """Advertise reusable Git read and write capabilities."""
    return (
        {
            "capability_id": "git.repo.read.file",
            "ingredient_ref": {
                "service_exec": "repo_read",
                "destination_target": "git",
                "task_key_template": "git-repo-read",
            },
            "operation": "read_file",
            "mode": "inspection",
            "resource_kinds": ["repository_file"],
            "required_inputs": ["path"],
            "optional_inputs": ["repo", "repo_url", "ref"],
            "defaults": {
                "service_payload": {},
                "service_exec_parameters": {"operation": "read_file"},
                "expected_outcome": {"success": True},
                "expected_secs": 10,
                "timeout": 120,
            },
            "safety_class": "observe_only",
            "requires_evidence": False,
            "priority": 100,
        },
        {
            "capability_id": "git.repo.read.list-files",
            "ingredient_ref": {
                "service_exec": "repo_read",
                "destination_target": "git",
                "task_key_template": "git-repo-read",
            },
            "operation": "list_files",
            "mode": "inspection",
            "resource_kinds": ["repository_tree"],
            "required_inputs": ["path"],
            "optional_inputs": ["repo", "repo_url", "ref", "recursive"],
            "defaults": {
                "service_payload": {"recursive": True},
                "service_exec_parameters": {"operation": "list_files"},
                "expected_outcome": {"success": True},
                "expected_secs": 10,
                "timeout": 120,
            },
            "safety_class": "observe_only",
            "requires_evidence": False,
            "priority": 100,
        },
        {
            "capability_id": "git.repo.write.commit-files",
            "ingredient_ref": {
                "service_exec": "repo_write",
                "destination_target": "git",
                "task_key_template": "git-repo-write",
            },
            "operation": "commit_files",
            "mode": "utility",
            "resource_kinds": ["repository_branch"],
            "required_inputs": ["branch", "files"],
            "optional_inputs": ["repo", "repo_url", "ref", "message", "commit_message", "push"],
            "defaults": {
                "service_payload": {"push": True},
                "service_exec_parameters": {"operation": "commit_files"},
                "expected_outcome": {"success": True},
                "expected_secs": 30,
                "timeout": 300,
            },
            "safety_class": "operator_guidance",
            "requires_evidence": False,
            "priority": 100,
        },
        {
            "capability_id": "git.repo.write.commit-and-pr",
            "ingredient_ref": {
                "service_exec": "repo_write",
                "destination_target": "git",
                "task_key_template": "git-repo-write",
            },
            "operation": "commit_and_pr",
            "mode": "utility",
            "resource_kinds": ["repository_branch", "pull_request"],
            "required_inputs": ["branch", "files", "title"],
            "optional_inputs": [
                "repo",
                "repo_url",
                "ref",
                "message",
                "commit_message",
                "body",
                "base_branch",
                "push",
            ],
            "defaults": {
                "service_payload": {"push": True},
                "service_exec_parameters": {"operation": "commit_and_pr"},
                "expected_outcome": {"success": True},
                "expected_secs": 30,
                "timeout": 300,
            },
            "safety_class": "operator_guidance",
            "requires_evidence": False,
            "priority": 100,
        },
    )
