"""GitHub execution adapter."""

from __future__ import annotations

from uuid import uuid4

from api.plugins.base import ExecutionAdapter
from api.plugins.github.client import GitHubClient, GitHubClientError
from api.plugins.types import ExecutionContext, ExecutionResult, PluginHealthResult
from api.services.credential_manager import read_adapter_credential_with_policy
from api.types import JSONObject

GITHUB_SERVICE_EXECS = {
    "health_check",
    "repo_read",
    "repo_write",
}

GITHUB_READ_OPERATIONS = {"read_file", "list_files"}
GITHUB_WRITE_OPERATIONS = {"commit_files", "create_pull_request", "commit_and_pr"}
GITHUB_CREDENTIAL_TYPE = "github_token"
SERVICE_PAYLOAD_OBJECT_ERROR = "service_payload must be an object when provided"


class GitHubExecutionAdapter(ExecutionAdapter):
    """Expose GitHub helper operations through order execution."""

    service_type = "github"

    def __init__(self, helper: GitHubClient | None = None) -> None:
        self.helper = helper or GitHubClient()

    def credential_requirements(self) -> list[JSONObject]:
        return [
            {
                "credential_type": GITHUB_CREDENTIAL_TYPE,
                "credential_key_id": "default",
                "required": False,
                "usage": (
                    "GitHub API token. Required for write operations. For read "
                    "operations, the credential manager controls whether public "
                    "read endpoints (raw.githubusercontent.com) are permitted via "
                    "the allow_public_read flag. Default is false — adapters must "
                    "not bypass this flag."
                ),
            }
        ]

    def operator_config_schema(self) -> JSONObject:
        return self.helper.operator_config_schema()

    def default_operator_config(self) -> JSONObject:
        return self.helper.default_operator_config()

    def normalize_operator_config(self, config: JSONObject | None) -> JSONObject:
        return self.helper.normalize_operator_config(config)

    def with_operator_config(self, config: JSONObject | None) -> "GitHubExecutionAdapter":
        return GitHubExecutionAdapter(self.helper.with_operator_config(config))

    def validate_credential_payload(self, credential_type: str, payload: JSONObject) -> str | None:
        if credential_type != GITHUB_CREDENTIAL_TYPE:
            return "Unsupported GitHub credential type"
        token = str(
            payload.get("token") or payload.get("access_token") or payload.get("api_key") or ""
        ).strip()
        if token:
            return None
        return "GitHub credential requires token, access_token, or api_key"

    def validate(self, ctx: ExecutionContext) -> str | None:
        service_exec = (ctx.service_exec or "").strip().lower()
        if service_exec not in GITHUB_SERVICE_EXECS:
            return f"Unsupported github service_exec: {ctx.service_exec}"
        if ctx.service_payload is not None and not isinstance(ctx.service_payload, dict):
            return SERVICE_PAYLOAD_OBJECT_ERROR
        transport_error = self.helper.validate_transport_security()
        if transport_error:
            return transport_error
        operation = _operation(ctx)
        if service_exec == "repo_read" and operation not in GITHUB_READ_OPERATIONS:
            return "github repo_read operation must be one of: list_files, read_file"
        if service_exec == "repo_write" and operation not in GITHUB_WRITE_OPERATIONS:
            return (
                "github repo_write operation must be one of: "
                "commit_and_pr, commit_files, create_pull_request"
            )
        payload = _payload(ctx)
        if operation == "read_file" and not str(payload.get("path") or "").strip():
            return "github read_file requires service_payload.path"
        if operation in {"commit_files", "commit_and_pr"}:
            files = payload.get("files")
            if not isinstance(files, dict) or not files:
                return "github commit operations require non-empty service_payload.files"
            if not str(payload.get("branch") or "").strip():
                return "github commit operations require service_payload.branch"
        if operation in {"create_pull_request", "commit_and_pr"}:
            if not str(payload.get("title") or "").strip():
                return "github pull request operations require service_payload.title"
            if operation == "create_pull_request" and not str(payload.get("branch") or "").strip():
                return "github create_pull_request requires service_payload.branch"
        return None

    def health_check(self) -> PluginHealthResult:
        return PluginHealthResult(
            service_type=self.service_type,
            status="healthy",
            message="GitHub plugin configured",
            details={
                **self.helper.safe_transport_details(),
                "authenticated": bool(self.helper.token),
            },
        )

    async def test_connection(self, *, credential_key_id: str = "default") -> PluginHealthResult:
        """Verify GitHub API accessibility using credentials from the credential manager."""
        try:
            helper = await self._helper_with_credentials(credential_key_id)
            health_result = await helper.health()
            is_healthy = health_result.get("status") == "healthy"
            return PluginHealthResult(
                service_type=self.service_type,
                status="healthy" if is_healthy else "failed",
                message=(
                    "GitHub API reachable" if is_healthy else "GitHub API connection test failed"
                ),
                latency_ms=health_result.get("latency_ms"),
                error_code=None if is_healthy else "github_connection_test_failed",
                details={
                    "mode": "github-api",
                    "authenticated": health_result.get("authenticated"),
                    "rate": health_result.get("rate"),
                    **self.helper.safe_transport_details(),
                    **health_result,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return PluginHealthResult(
                service_type=self.service_type,
                status="failed",
                message="GitHub API connection test failed",
                error_code="github_connection_test_failed",
                details={"error": str(exc)},
            )

    async def dispatch(self, ctx: ExecutionContext) -> ExecutionResult:
        service_exec = (ctx.service_exec or "").strip().lower()
        operation = "health_check" if service_exec == "health_check" else _operation(ctx)
        service_exec_id = f"github:{operation}:{uuid4()}"
        if ctx.service_payload is not None and not isinstance(ctx.service_payload, dict):
            return _payload_contract_error(
                service_type=self.service_type,
                service_exec_id=service_exec_id,
                message=SERVICE_PAYLOAD_OBJECT_ERROR,
            )
        try:
            helper = await self._helper_with_credentials(operation)
            payload = _payload(ctx)
            result = await self._execute(helper, operation, payload)
            return ExecutionResult(
                service_type=self.service_type,
                status="succeeded",
                service_exec_id=service_exec_id,
                result=result,
                raw=result,
            )
        except GitHubClientError as exc:
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
        # All github operations complete synchronously during dispatch.
        # Return a successful replay of the dispatch result since no async state
        # exists to observe. This is a read-only confirmation, not a mutation.
        message = "GitHub executions complete during dispatch; no pollable state exists"
        return ExecutionResult(
            service_type=self.service_type,
            status="succeeded",
            service_exec_id=service_exec_id,
            service_exec_error=message,
            result={"success": True, "status": "succeeded", "message": message},
            raw={"success": True, "status": "succeeded", "message": message},
            retryable=False,
        )

    async def _helper_with_credentials(self, service_exec: str) -> GitHubClient:
        requires_credentials = service_exec in GITHUB_WRITE_OPERATIONS
        try:
            result = await read_adapter_credential_with_policy(
                service_type=self.service_type,
                credential_type=GITHUB_CREDENTIAL_TYPE,
                credential_key_id="default",
            )
        except Exception as exc:
            if requires_credentials:
                raise GitHubClientError(
                    f"github repo_write requires adapter credential {GITHUB_CREDENTIAL_TYPE}: {exc}"
                ) from exc
            result = None
        if requires_credentials and (result is None or not _credential_token(result.payload)):
            raise GitHubClientError(
                f"github repo_write requires adapter credential {GITHUB_CREDENTIAL_TYPE}"
            )
        helper = (
            self.helper.with_credentials(result.payload if result else None)
            if hasattr(self.helper, "with_credentials")
            else self.helper
        )
        helper.allow_public_read = result.allow_public_read if result else False
        return helper

    async def _execute(
        self,
        helper: GitHubClient,
        service_exec: str,
        payload: JSONObject,
    ) -> JSONObject:
        if service_exec == "health_check":
            return await helper.health()
        if service_exec == "read_file":
            return await helper.read_file(
                repo=_optional_str(payload.get("repo")),
                path=str(payload.get("path") or ""),
                ref=_optional_str(payload.get("ref")),
            )
        if service_exec == "list_files":
            return await helper.list_files(
                repo=_optional_str(payload.get("repo")),
                path=str(payload.get("path") or ""),
                ref=_optional_str(payload.get("ref")),
                recursive=bool(payload.get("recursive", True)),
            )
        if service_exec == "commit_files":
            return await helper.commit_files(
                repo=_optional_str(payload.get("repo")),
                base_branch=_optional_str(payload.get("base_branch")),
                branch=str(payload.get("branch") or ""),
                files=_string_file_map(payload.get("files")),
                message=str(payload.get("message") or "PoundCake GitHub update"),
            )
        if service_exec == "create_pull_request":
            return await helper.create_pull_request(
                repo=_optional_str(payload.get("repo")),
                branch=str(payload.get("branch") or ""),
                base_branch=_optional_str(payload.get("base_branch")),
                title=str(payload.get("title") or ""),
                body=str(payload.get("body") or ""),
            )
        if service_exec == "commit_and_pr":
            return await helper.commit_and_pr(
                repo=_optional_str(payload.get("repo")),
                base_branch=_optional_str(payload.get("base_branch")),
                branch=str(payload.get("branch") or ""),
                files=_string_file_map(payload.get("files")),
                commit_message=str(payload.get("commit_message") or "PoundCake GitHub update"),
                title=str(payload.get("title") or ""),
                body=str(payload.get("body") or ""),
            )
        raise GitHubClientError(f"Unknown GitHub receipt operation: {service_exec}")


def _optional_str(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _credential_token(payload: JSONObject) -> str:
    return str(
        payload.get("token") or payload.get("access_token") or payload.get("api_key") or ""
    ).strip()


def _operation(ctx: ExecutionContext) -> str:
    params = ctx.service_exec_parameters if isinstance(ctx.service_exec_parameters, dict) else {}
    return str(params.get("operation") or "").strip().lower()


def _payload(ctx: ExecutionContext) -> JSONObject:
    return {} if ctx.service_payload is None else ctx.service_payload


def _payload_contract_error(
    *, service_type: str, service_exec_id: str, message: str
) -> ExecutionResult:
    outcome: JSONObject = {"success": False, "status": "errored", "message": message}
    return ExecutionResult(
        service_type=service_type,
        status="errored",
        service_exec_id=service_exec_id,
        service_exec_error=message,
        result=outcome,
        raw=outcome,
        retryable=False,
    )


def _string_file_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}
