"""Unit tests for the Git service plugin."""

from __future__ import annotations

from copy import deepcopy

import pytest

from api.plugins.contract import (
    ServicePluginContractError,
    validate_service_payload_for_operation,
)
from api.plugins.git.adapter import GitExecutionAdapter
from api.plugins.git.client import GitClient, GitClientError
from api.plugins.git.plugin import get_plugin
from api.plugins.git.templates import GIT_INGREDIENT_TEMPLATES
from api.plugins.manifest import validate_service_plugin
from api.plugins.types import ExecutionContext


def _git_template(service_exec: str) -> dict[str, object]:
    return next(
        template
        for template in GIT_INGREDIENT_TEMPLATES
        if template["service_exec"] == service_exec
    )


def test_git_plugin_manifest_is_external_and_valid() -> None:
    plugin = validate_service_plugin(get_plugin(), directory_name="git")

    assert plugin.service_type == "git"
    assert plugin.plugin_tier == "community"
    assert "repo.write" in plugin.helper_capabilities


@pytest.mark.parametrize(
    ("service_exec", "operation", "payload"),
    [
        ("repo_read", "read_file", {}),
        ("repo_read", "read_file", {"path": ""}),
        ("repo_write", "commit_files", {"files": {"README.md": "contents"}}),
        ("repo_write", "commit_files", {"branch": "codex/test"}),
        ("repo_write", "create_pull_request", {"branch": "codex/test"}),
        ("repo_write", "create_pull_request", {"title": "Test PR"}),
        (
            "repo_write",
            "commit_and_pr",
            {"branch": "codex/test", "files": {"README.md": "contents"}},
        ),
        (
            "repo_write",
            "commit_and_pr",
            {"branch": "codex/test", "title": "Test PR"},
        ),
    ],
)
def test_git_operation_payload_contract_rejects_missing_required_fields(
    service_exec: str,
    operation: str,
    payload: dict[str, object],
) -> None:
    template = _git_template(service_exec)
    parameters = deepcopy(template["service_exec_parameters"])
    parameters["operation"] = operation

    with pytest.raises(ServicePluginContractError):
        validate_service_payload_for_operation(
            payload,
            template["payload_schema"],
            parameters,
        )


def test_git_adapter_declares_optional_repository_credentials() -> None:
    assert GitExecutionAdapter(
        helper=GitClient(repo_url="https://example.test/repo.git")
    ).credential_requirements() == [
        {
            "credential_type": "git_repository_auth",
            "credential_key_id": "default",
            "required": False,
            "usage": (
                "Git repository token or SSH key path. Required for write operations. "
                "For unauthenticated reads, the credential manager controls whether "
                "public read access is permitted via the allow_public_read flag."
            ),
        }
    ]


def test_git_adapter_validates_write_contract() -> None:
    adapter = GitExecutionAdapter(helper=GitClient(repo_url="https://example.test/repo.git"))

    error = adapter.validate(
        ExecutionContext(
            service_type="git",
            service_exec="repo_write",
            req_id="unit-test",
            service_payload={"files": {"x.txt": "x"}},
            service_exec_parameters={"operation": "commit_files"},
        )
    )

    assert error == "git commit operations require service_payload.branch"


def test_git_adapter_rejects_non_object_service_payload() -> None:
    adapter = GitExecutionAdapter(helper=GitClient(repo_url="https://example.test/repo.git"))
    ctx = ExecutionContext.model_construct(
        service_type="git",
        service_exec="repo_read",
        req_id="unit-test",
        service_payload=["not", "an", "object"],
        service_exec_parameters={"operation": "read_file"},
        context={},
    )

    assert adapter.validate(ctx) == "service_payload must be an object when provided"


class _FakeGitHelper:
    def safe_transport_details(self) -> dict[str, object]:
        return {"repo_configured": True, "default_branch": "main", "provider": "none"}

    def with_credentials(self, _payload: object) -> "_FakeGitHelper":
        return self

    async def read_file(self, **_kwargs: object) -> dict[str, object]:
        return {"success": True, "content": "hello"}

    async def commit_files(self, **kwargs: object) -> dict[str, object]:
        return {
            "success": True,
            "status": "committed",
            "branch": kwargs["branch"],
            "changed_files": sorted((kwargs["files"] or {}).keys()),  # type: ignore[union-attr]
        }

    async def health(self) -> dict[str, object]:
        return {"success": True, "status": "healthy"}


@pytest.mark.asyncio
async def test_git_adapter_maps_read_result_to_execution_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = GitExecutionAdapter(helper=_FakeGitHelper())  # type: ignore[arg-type]

    async def fake_helper(_service_exec: str) -> _FakeGitHelper:
        return adapter.helper  # type: ignore[return-value]

    monkeypatch.setattr(adapter, "_helper_with_credentials", fake_helper)
    ctx = ExecutionContext(
        service_type="git",
        service_exec="repo_read",
        req_id="unit-test",
        service_payload={"path": "README.md"},
        service_exec_parameters={"operation": "read_file"},
    )

    result = await adapter.dispatch(ctx)

    assert result.status == "succeeded"
    assert result.result == {"success": True, "content": "hello"}


@pytest.mark.asyncio
async def test_git_adapter_maps_write_result_to_execution_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = GitExecutionAdapter(helper=_FakeGitHelper())  # type: ignore[arg-type]

    async def fake_helper(_service_exec: str) -> _FakeGitHelper:
        return adapter.helper  # type: ignore[return-value]

    monkeypatch.setattr(adapter, "_helper_with_credentials", fake_helper)
    ctx = ExecutionContext(
        service_type="git",
        service_exec="repo_write",
        req_id="unit-test",
        service_payload={"branch": "poundcake/test", "files": {"x.txt": "x"}},
        service_exec_parameters={"operation": "commit_files"},
    )

    result = await adapter.dispatch(ctx)

    assert result.status == "succeeded"
    assert result.result == {
        "success": True,
        "status": "committed",
        "branch": "poundcake/test",
        "changed_files": ["x.txt"],
    }


@pytest.mark.asyncio
async def test_git_adapter_failed_helper_maps_to_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingHelper(_FakeGitHelper):
        async def read_file(self, **_kwargs: object) -> dict[str, object]:
            raise GitClientError("boom")

    adapter = GitExecutionAdapter(helper=FailingHelper())  # type: ignore[arg-type]

    async def fake_helper(_service_exec: str) -> FailingHelper:
        return adapter.helper  # type: ignore[return-value]

    monkeypatch.setattr(adapter, "_helper_with_credentials", fake_helper)
    ctx = ExecutionContext(
        service_type="git",
        service_exec="repo_read",
        req_id="unit-test",
        service_payload={"path": "README.md"},
        service_exec_parameters={"operation": "read_file"},
    )

    result = await adapter.dispatch(ctx)

    assert result.status == "failed"
    assert result.service_exec_error == "boom"


@pytest.mark.asyncio
async def test_git_adapter_requires_adapter_credentials_for_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = GitExecutionAdapter(helper=GitClient(repo_url="https://example.test/repo.git"))

    async def missing_credential(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        "api.plugins.git.adapter.read_adapter_credential_with_policy", missing_credential
    )
    ctx = ExecutionContext(
        service_type="git",
        service_exec="repo_write",
        req_id="unit-test",
        service_payload={"branch": "poundcake/test", "files": {"x.txt": "x"}},
        service_exec_parameters={"operation": "commit_files"},
    )

    result = await adapter.dispatch(ctx)

    assert result.status == "failed"
    assert result.service_exec_error == (
        "git repo_write requires adapter credential git_repository_auth"
    )


def test_git_create_pr_generates_helpful_message_for_non_github_provider() -> None:
    class StubGitClient:
        provider = "gitlab"
        token = "token"
        repo_url = "https://example.test/repo.git"
        default_branch = "main"
        retries = 1

        allow_public_read: bool = False

        async def create_pull_request(
            self,
            *,
            repo_url: str | None = None,
            branch: str,
            base_branch: str | None = None,
            title: str,
            body: str = "",
        ) -> dict[str, object]:
            return {
                "success": True,
                "skipped": True,
                "message": (
                    "Pull request creation is not implemented for provider 'gitlab'; "
                    "use the github adapter or a provider-specific adapter instead."
                ),
            }

    import asyncio

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            StubGitClient().create_pull_request(
                branch="test",
                base_branch="main",
                title="Test PR",
            )
        )
        assert result["skipped"] is True
        assert "gitlab" in result["message"]
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_git_client_rejects_public_repo_read_without_auth_or_policy() -> None:
    client = GitClient(repo_url="https://example.test/repo.git")

    with pytest.raises(GitClientError, match="allow_public_read=true"):
        await client.read_file(path="README.md")
