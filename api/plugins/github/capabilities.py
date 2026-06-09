"""Composable GitHub capability templates."""

from __future__ import annotations

from api.types import JSONObject


def load_github_capability_templates() -> tuple[JSONObject, ...]:
    """Advertise reusable GitHub read and write capabilities."""
    return (
        {
            "capability_id": "github.repo.read.genestack-source-rule",
            "ingredient_ref": {
                "service_exec": "repo_read",
                "destination_target": "github",
                "task_key_template": "github-repo-read",
            },
            "operation": "read_file",
            "mode": "inspection",
            "resource_kinds": ["alert_source", "rule_file"],
            "trigger_match": {"phase": "evidence"},
            "required_inputs": ["repo", "ref", "path"],
            "optional_inputs": [],
            "defaults": {
                "service_payload": {
                    "repo": "rackerlabs/genestack-monitoring",
                    "ref": "main",
                    "path": "",
                },
                "service_exec_parameters": {
                    "operation": "read_file",
                    "managed_role": "gather_source_rule_evidence",
                    "evidence_family": "alert_source",
                },
                "expected_outcome": {"success": True},
                "expected_secs": 5,
                "timeout": 60,
                "role": "gather_source_rule_evidence",
            },
            "safety_class": "observe_only",
            "requires_evidence": False,
            "priority": 350,
        },
        {
            "capability_id": "github.repo.read.list-files",
            "ingredient_ref": {
                "service_exec": "repo_read",
                "destination_target": "github",
                "task_key_template": "github-repo-read",
            },
            "operation": "list_files",
            "mode": "inspection",
            "resource_kinds": ["repository_tree"],
            "required_inputs": ["repo", "ref", "path"],
            "optional_inputs": ["recursive"],
            "defaults": {
                "service_payload": {
                    "repo": "rackerlabs/genestack-monitoring",
                    "ref": "main",
                    "path": "",
                    "recursive": True,
                },
                "service_exec_parameters": {"operation": "list_files"},
                "expected_outcome": {"success": True},
                "expected_secs": 5,
                "timeout": 60,
            },
            "safety_class": "observe_only",
            "requires_evidence": False,
            "priority": 100,
        },
        {
            "capability_id": "github.repo.write.commit-and-pr",
            "ingredient_ref": {
                "service_exec": "repo_write",
                "destination_target": "github",
                "task_key_template": "github-repo-write",
            },
            "operation": "commit_and_pr",
            "mode": "utility",
            "resource_kinds": ["repository_branch", "pull_request"],
            "required_inputs": ["repo", "branch", "files", "message", "title"],
            "optional_inputs": ["base_branch", "body", "commit_message"],
            "defaults": {
                "service_payload": {},
                "service_exec_parameters": {"operation": "commit_and_pr"},
                "expected_outcome": {"success": True},
                "expected_secs": 15,
                "timeout": 180,
            },
            "safety_class": "operator_guidance",
            "requires_evidence": False,
            "priority": 100,
        },
    )
