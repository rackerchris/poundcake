"""Unit tests for the GitHub service plugin helper and adapter."""

from __future__ import annotations

import base64
import io
import zipfile

import httpx
import pytest

from api.core.config import get_settings
from api.plugins.github.adapter import GitHubExecutionAdapter
from api.plugins.github.client import GitHubClient, GitHubClientError, parse_github_repo
from api.plugins.github.plugin import get_plugin
from api.plugins.manifest import validate_service_plugin
from api.plugins.types import ExecutionContext


def _response(status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", "https://x"))


def _text_response(status_code: int, text: str) -> httpx.Response:
    return httpx.Response(status_code, text=text, request=httpx.Request("GET", "https://x"))


def _zip_response(paths: list[str]) -> httpx.Response:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as archive:
        for path in paths:
            archive.writestr(path, "")
    return httpx.Response(
        200,
        content=buffer.getvalue(),
        request=httpx.Request("GET", "https://x"),
    )


def test_github_plugin_manifest_is_community_tier() -> None:
    plugin = validate_service_plugin(get_plugin(), directory_name="github")

    assert plugin.service_type == "github"
    assert plugin.plugin_tier == "community"
    assert plugin.plugin_log_key is None


def test_parse_github_repo_accepts_owner_repo_and_urls() -> None:
    assert parse_github_repo("rackerlabs/genestack-monitoring").owner == "rackerlabs"
    assert parse_github_repo("https://github.com/rackerlabs/genestack-monitoring.git").repo == (
        "genestack-monitoring"
    )
    assert parse_github_repo("git@github.com:rackerlabs/genestack-monitoring.git").repo == (
        "genestack-monitoring"
    )


def test_github_adapter_declares_optional_ecosystem_credentials() -> None:
    client = GitHubClient(token="", default_repo="rackerlabs/genestack-monitoring")

    assert GitHubExecutionAdapter(helper=client).credential_requirements() == [
        {
            "credential_type": "github_token",
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


@pytest.mark.asyncio
async def test_github_client_reads_public_repo_file_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_settings.cache_clear()
    content = base64.b64encode(b"groups: []\n").decode("ascii")
    calls: list[dict[str, object]] = []

    async def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        calls.append({"method": method, "url": url, **kwargs})
        return _response(
            200,
            {
                "type": "file",
                "encoding": "base64",
                "content": content,
                "sha": "abc123",
            },
        )

    monkeypatch.setattr("api.plugins.github.client.request_with_retry", fake_request)
    client = GitHubClient(
        token="read-token",
        default_repo="rackerlabs/genestack-monitoring",
    )

    result = await client.read_file(path="alerts/test.yaml", ref="main")

    assert result["success"] is True
    assert result["content"] == "groups: []\n"
    assert calls[0]["headers"]["Authorization"] == "Bearer read-token"  # type: ignore[index]
    assert calls[0]["verify"] is True


@pytest.mark.asyncio
async def test_github_client_reads_public_repo_file_through_raw_url_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_settings.cache_clear()
    calls: list[dict[str, object]] = []

    async def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        calls.append({"method": method, "url": url, **kwargs})
        return _text_response(200, "groups: []\n")

    monkeypatch.setattr("api.plugins.github.client.request_with_retry", fake_request)
    client = GitHubClient(token="", default_repo="rackerlabs/genestack-monitoring")
    client.allow_public_read = True

    result = await client.read_file(path="alerts/test.yaml", ref="main")

    assert result["success"] is True
    assert result["content"] == "groups: []\n"
    assert calls == [
        {
            "method": "GET",
            "url": "https://raw.githubusercontent.com/rackerlabs/genestack-monitoring/main/alerts/test.yaml",
            "verify": True,
            "timeout": client.timeout_seconds,
            "retries": client.retries,
        }
    ]


@pytest.mark.asyncio
async def test_github_client_lists_public_repo_files_through_archive_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_settings.cache_clear()
    calls: list[dict[str, object]] = []

    async def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        calls.append({"method": method, "url": url, **kwargs})
        return _zip_response(
            [
                "genestack-monitoring-main/alerts/demo.yaml",
                "genestack-monitoring-main/alerts/nested/child.yml",
                "genestack-monitoring-main/docs/readme.md",
            ]
        )

    monkeypatch.setattr("api.plugins.github.client.request_with_retry", fake_request)
    client = GitHubClient(token="", default_repo="rackerlabs/genestack-monitoring")
    client.allow_public_read = True

    result = await client.list_files(path="alerts", ref="main")

    assert result["success"] is True
    assert result["files"] == [
        {"path": "alerts/demo.yaml", "sha": None, "size": None},
        {"path": "alerts/nested/child.yml", "sha": None, "size": None},
    ]
    assert calls[0]["url"] == "https://codeload.github.com/rackerlabs/genestack-monitoring/zip/main"


@pytest.mark.asyncio
async def test_github_client_rejects_public_repo_read_without_token_or_policy() -> None:
    client = GitHubClient(token="", default_repo="rackerlabs/genestack-monitoring")

    with pytest.raises(GitHubClientError, match="allow_public_read=true"):
        await client.read_file(path="alerts/test.yaml", ref="main")


@pytest.mark.asyncio
async def test_github_client_rejects_write_without_token() -> None:
    client = GitHubClient(token="", default_repo="rackerlabs/genestack-monitoring")
    with pytest.raises(GitHubClientError, match="token is required"):
        await client.commit_files(branch="test", files={"x.txt": "x"}, message="test")


def test_github_adapter_reports_safe_transport_metadata() -> None:
    client = GitHubClient(
        token="secret-token",
        api_base_url="https://github.example.test/api/v3",
        default_repo="example/repo",
    )

    health = GitHubExecutionAdapter(helper=client).health_check()

    assert health.details == {
        "url": "https://github.example.test/api/v3",
        "verify_ssl": True,
        "auth_mode": "bearer",
        "secure_transport": True,
        "authenticated": True,
    }
    assert "secret-token" not in str(health.details)


def test_github_adapter_rejects_auth_over_insecure_remote_transport() -> None:
    client = GitHubClient(
        token="secret-token",
        api_base_url="http://github.example.test/api/v3",
        default_repo="example/repo",
    )

    error = GitHubExecutionAdapter(helper=client).validate(
        ExecutionContext(
            service_type="github",
            service_exec="health_check",
            req_id="unit-test",
        )
    )

    assert error == "GitHub authentication requires HTTPS or an in-cluster service URL"


class _FakeGitHubHelper:
    token = "token"

    def validate_transport_security(self) -> str | None:
        return None

    def safe_transport_details(self) -> dict[str, object]:
        return {
            "url": "https://api.github.com",
            "verify_ssl": True,
            "auth_mode": "bearer",
            "secure_transport": True,
        }

    async def read_file(self, **_kwargs: object) -> dict[str, object]:
        return {"success": True, "content": "hello"}

    async def health(self) -> dict[str, object]:
        return {"success": True, "status": "healthy"}


@pytest.mark.asyncio
async def test_github_adapter_maps_helper_result_to_execution_result() -> None:
    adapter = GitHubExecutionAdapter(helper=_FakeGitHubHelper())  # type: ignore[arg-type]
    ctx = ExecutionContext(
        service_type="github",
        service_exec="repo_read",
        req_id="unit-test",
        service_payload={"path": "README.md"},
        service_exec_parameters={
            "operation": "read_file",
            "allowed_operations": ["read_file", "list_files"],
        },
    )
    result = await adapter.dispatch(ctx)

    assert result.status == "succeeded"
    assert result.result == {"success": True, "content": "hello"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_token"),
    [
        ({"token": "secret-token"}, "secret-token"),
        ({"access_token": "secret-token"}, "secret-token"),
        ({"api_key": "secret-token"}, "secret-token"),
    ],
)
async def test_github_adapter_accepts_token_aliases_for_write_credentials(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, str],
    expected_token: str,
) -> None:
    adapter = GitHubExecutionAdapter(helper=GitHubClient(default_repo="example/repo"))

    async def load_credential(**_kwargs: object):
        from api.services.credential_manager import AdapterCredentialResult

        return AdapterCredentialResult(payload=payload, allow_public_read=False)

    monkeypatch.setattr(
        "api.plugins.github.adapter.read_adapter_credential_with_policy", load_credential
    )

    helper = await adapter._helper_with_credentials("commit_files")

    assert helper.token == expected_token


@pytest.mark.asyncio
async def test_github_adapter_rejects_write_without_any_supported_token_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = GitHubExecutionAdapter(helper=GitHubClient(default_repo="example/repo"))

    async def load_credential(**_kwargs: object):
        from api.services.credential_manager import AdapterCredentialResult

        return AdapterCredentialResult(payload={"username": "bot"}, allow_public_read=False)

    monkeypatch.setattr(
        "api.plugins.github.adapter.read_adapter_credential_with_policy", load_credential
    )

    with pytest.raises(GitHubClientError, match="github_token"):
        await adapter._helper_with_credentials("commit_files")
