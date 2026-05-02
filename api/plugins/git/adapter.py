"""Git execution adapter."""

from __future__ import annotations

from uuid import uuid4

from api.plugins.base import ExecutionAdapter
from api.services.credential_manager import (
    ServicePluginCredentialError,
    read_adapter_credential_with_policy,
)
from api.plugins.git.client import (
    GIT_REPOSITORY_AUTH_CREDENTIAL_TYPE,
    GitClient,
    GitClientError,
)
from api.plugins.types import ExecutionContext, ExecutionResult, PluginHealthResult
from api.types import JSONObject

GIT_SERVICE_EXECS = {"health_check", "repo_read", "repo_write"}
GIT_READ_OPERATIONS = {"read_file", "list_files"}
GIT_WRITE_OPERATIONS = {"commit_files", "create_pull_request", "commit_and_pr"}


class GitExecutionAdapter(ExecutionAdapter):
    """Expose portable Git operations through the order execution boundary.

    Use the ``git`` adapter for provider-agnostic git servers (GitLab,
    Bitbucket, generic git) or when clone-based workflows are needed.
    Prefer the ``github`` adapter when targeting GitHub or GitHub
    Enterprise — the github adapter uses the GitHub REST API directly,
    and PR creation on non-GitHub providers is delegated to domain-
    specific adapters rather than the github adapter.
    """

    service_type = "git"

    def __init__(self, helper: GitClient | None = None) -> None:
        self.helper = helper or GitClient()

    def credential_requirements(self) -> list[JSONObject]:
        return [
            {
                "credential_type": GIT_REPOSITORY_AUTH_CREDENTIAL_TYPE,
                "credential_key_id": "default",
                "required": False,
                "usage": (
                    "Git repository token or SSH key path. Required for write operations. "
                    "For unauthenticated reads, the credential manager controls whether "
                    "public read access is permitted via the allow_public_read flag."
                ),
            }
        ]

    def operator_config_schema(self) -> JSONObject:
        return self.helper.operator_config_schema()

    def default_operator_config(self) -> JSONObject:
        return self.helper.default_operator_config()

    def normalize_operator_config(self, config: JSONObject | None) -> JSONObject:
        return self.helper.normalize_operator_config(config)

    def with_operator_config(self, config: JSONObject | None) -> "GitExecutionAdapter":
        return GitExecutionAdapter(self.helper.with_operator_config(config))

    def validate_credential_payload(self, credential_type: str, payload: JSONObject) -> str | None:
        if credential_type != GIT_REPOSITORY_AUTH_CREDENTIAL_TYPE:
            return "Unsupported Git credential type"
        token = str(
            payload.get("token") or payload.get("access_token") or payload.get("password") or ""
        ).strip()
        ssh_key_path = str(payload.get("ssh_key_path") or "").strip()
        if token or ssh_key_path:
            return None
        return "Git credential requires token/access_token/password or ssh_key_path"

    def validate(self, ctx: ExecutionContext) -> str | None:
        service_exec = (ctx.service_exec or "").strip().lower()
        if service_exec not in GIT_SERVICE_EXECS:
            return f"Unsupported git service_exec: {ctx.service_exec}"
        operation = _operation(ctx)
        if service_exec == "health_check":
            return None
        if service_exec == "repo_read" and operation not in GIT_READ_OPERATIONS:
            return "git repo_read operation must be one of: list_files, read_file"
        if service_exec == "repo_write" and operation not in GIT_WRITE_OPERATIONS:
            return (
                "git repo_write operation must be one of: "
                "commit_and_pr, commit_files, create_pull_request"
            )
        payload = ctx.service_payload or {}
        if operation == "read_file" and not str(payload.get("path") or "").strip():
            return "git read_file requires service_payload.path"
        if operation in {"commit_files", "commit_and_pr"}:
            files = payload.get("files")
            if not isinstance(files, dict) or not files:
                return "git commit operations require non-empty service_payload.files"
            if not str(payload.get("branch") or "").strip():
                return "git commit operations require service_payload.branch"
        if operation in {"create_pull_request", "commit_and_pr"}:
            if not str(payload.get("title") or "").strip():
                return "git pull request operations require service_payload.title"
            if operation == "create_pull_request" and not str(payload.get("branch") or "").strip():
                return "git create_pull_request requires service_payload.branch"
        return None

    def health_check(self) -> PluginHealthResult:
        return PluginHealthResult(
            service_type=self.service_type,
            status="healthy",
            message="Git plugin configured",
            details={
                **self.helper.safe_transport_details(),
                "credential_type": GIT_REPOSITORY_AUTH_CREDENTIAL_TYPE,
            },
        )

    async def test_connection(self, *, credential_key_id: str = "default") -> PluginHealthResult:
        """Verify Git client is operational and report transport configuration."""
        try:
            helper = await self._helper_with_credentials("health_check")
            health_result = await helper.health()
            is_healthy = health_result.get("status") == "healthy" and health_result.get(
                "gitpython_available"
            )
            return PluginHealthResult(
                service_type=self.service_type,
                status="healthy" if is_healthy else "failed",
                message=("Git client is configured" if is_healthy else "Git health check failed"),
                error_code=None if is_healthy else "git_health_check_failed",
                details={
                    "mode": "git-client",
                    "gitpython_available": health_result.get("gitpython_available"),
                    "provider": self.helper.provider or "none",
                    "repo_configured": health_result.get("repo_configured"),
                    **self.helper.safe_transport_details(),
                    **health_result,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return PluginHealthResult(
                service_type=self.service_type,
                status="failed",
                message="Git client health check failed",
                error_code="git_health_check_failed",
                details={"error": str(exc)},
            )

    async def dispatch(self, ctx: ExecutionContext) -> ExecutionResult:
        service_exec = (ctx.service_exec or "").strip().lower()
        operation = "health_check" if service_exec == "health_check" else _operation(ctx)
        service_exec_id = f"git:{operation}:{uuid4()}"
        try:
            helper = await self._helper_with_credentials(operation)
            result = await self._execute(helper, operation, ctx.service_payload or {})
            return ExecutionResult(
                service_type=self.service_type,
                status="succeeded",
                service_exec_id=service_exec_id,
                result=result,
                raw=result,
            )
        except GitClientError as exc:
            outcome: JSONObject = {"success": False, "status": "failed", "message": str(exc)}
            return ExecutionResult(
                service_type=self.service_type,
                status="failed",
                service_exec_id=service_exec_id,
                service_exec_error=str(exc),
                result=outcome,
                raw=outcome,
                retryable=False,
            )
        except Exception as exc:  # noqa: BLE001
            outcome = {"success": False, "status": "errored", "message": str(exc)}
            return ExecutionResult(
                service_type=self.service_type,
                status="errored",
                service_exec_id=service_exec_id,
                service_exec_error=str(exc),
                result=outcome,
                raw=outcome,
                retryable=False,
            )

    async def poll(self, ctx: ExecutionContext, service_exec_id: str) -> ExecutionResult:
        # All git operations complete synchronously during dispatch.
        # Return a successful replay of the dispatch result since no async state
        # exists to observe. This is a read-only confirmation, not a mutation.
        message = "Git executions complete during dispatch; no pollable state exists"
        return ExecutionResult(
            service_type=self.service_type,
            status="succeeded",
            service_exec_id=service_exec_id,
            service_exec_error=message,
            result={"success": True, "status": "succeeded", "message": message},
            raw={"success": True, "status": "succeeded", "message": message},
            retryable=False,
        )

    async def _helper_with_credentials(self, service_exec: str) -> GitClient:
        requires_credentials = service_exec in GIT_WRITE_OPERATIONS
        try:
            result = await read_adapter_credential_with_policy(
                service_type=self.service_type,
                credential_type=GIT_REPOSITORY_AUTH_CREDENTIAL_TYPE,
                credential_key_id="default",
            )
        except ServicePluginCredentialError as exc:
            if requires_credentials:
                raise GitClientError(
                    "git repo_write requires adapter credential "
                    f"{GIT_REPOSITORY_AUTH_CREDENTIAL_TYPE}: {exc}"
                ) from exc
            result = None
        if requires_credentials and result is None:
            raise GitClientError(
                "git repo_write requires adapter credential "
                f"{GIT_REPOSITORY_AUTH_CREDENTIAL_TYPE}"
            )
        credential = result.payload if result else None
        allow_public_read = result.allow_public_read if result else False
        helper = self.helper.with_credentials(credential)
        helper.allow_public_read = allow_public_read
        if requires_credentials and not helper.has_auth_credentials():
            raise GitClientError(
                "git repo_write requires git_repository_auth credential with token or ssh_key_path"
            )
        return helper

    async def _execute(
        self, helper: GitClient, service_exec: str, payload: JSONObject
    ) -> JSONObject:
        if service_exec == "health_check":
            return await helper.health()
        if service_exec == "read_file":
            return await helper.read_file(
                repo_url=_optional_str(payload.get("repo_url") or payload.get("repo")),
                path=str(payload.get("path") or ""),
                ref=_optional_str(payload.get("ref")),
            )
        if service_exec == "list_files":
            return await helper.list_files(
                repo_url=_optional_str(payload.get("repo_url") or payload.get("repo")),
                path=str(payload.get("path") or ""),
                ref=_optional_str(payload.get("ref")),
                recursive=bool(payload.get("recursive", True)),
            )
        if service_exec == "commit_files":
            return await helper.commit_files(
                repo_url=_optional_str(payload.get("repo_url") or payload.get("repo")),
                base_branch=_optional_str(payload.get("base_branch")),
                branch=str(payload.get("branch") or ""),
                files=_file_map(payload.get("files")),
                message=str(payload.get("message") or "PoundCake Git update"),
                push=bool(payload.get("push", True)),
            )
        if service_exec == "create_pull_request":
            return await helper.create_pull_request(
                repo_url=_optional_str(payload.get("repo_url") or payload.get("repo")),
                branch=str(payload.get("branch") or ""),
                base_branch=_optional_str(payload.get("base_branch")),
                title=str(payload.get("title") or ""),
                body=str(payload.get("body") or ""),
            )
        if service_exec == "commit_and_pr":
            return await helper.commit_and_pr(
                repo_url=_optional_str(payload.get("repo_url") or payload.get("repo")),
                base_branch=_optional_str(payload.get("base_branch")),
                branch=str(payload.get("branch") or ""),
                files=_file_map(payload.get("files")),
                commit_message=str(payload.get("commit_message") or "PoundCake Git update"),
                title=str(payload.get("title") or ""),
                body=str(payload.get("body") or ""),
                push=bool(payload.get("push", True)),
            )
        raise GitClientError(f"Unknown Git receipt operation: {service_exec}")


def _operation(ctx: ExecutionContext) -> str:
    params = ctx.service_exec_parameters if isinstance(ctx.service_exec_parameters, dict) else {}
    return str(params.get("operation") or "").strip().lower()


def _optional_str(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _file_map(value: object) -> dict[str, str | None]:
    if not isinstance(value, dict):
        return {}
    return {str(key): (None if item is None else str(item)) for key, item in value.items()}
