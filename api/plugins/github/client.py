"""Shared GitHub helper used by the GitHub adapter and bootstrap plugins."""

from __future__ import annotations

import base64
import io
import os
import time
import zipfile
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from api.core.config import get_settings
from api.core.http_client import request_with_retry
from api.plugins.transport import PluginHttpTransportConfig, merge_plugin_request_kwargs
from api.types import JSONObject


class GitHubClientError(RuntimeError):
    """Raised when a GitHub helper operation fails."""


@dataclass(frozen=True, slots=True)
class GitHubRepoRef:
    owner: str
    repo: str


def parse_github_repo(value: str) -> GitHubRepoRef:
    """Parse owner/repo, HTTPS, or SSH GitHub repo references."""
    raw = str(value or "").strip()
    if not raw:
        raise GitHubClientError("GitHub repository is required")
    if raw.startswith("git@github.com:"):
        path = raw.split(":", 1)[1]
    else:
        parsed = urlparse(raw if "://" in raw else f"https://github.com/{raw}")
        if parsed.netloc and parsed.netloc.lower() != "github.com":
            raise GitHubClientError("Only github.com repositories are supported")
        path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        raise GitHubClientError("GitHub repository must be owner/repo")
    return GitHubRepoRef(owner=parts[0], repo=parts[1])


class GitHubClient:
    """Small GitHub REST client for plugin-owned helper calls.

    The credential manager (via ``allow_public_read`` in the credential record)
    is the authoritative policy gate for unauthenticated public reads.  This
    client no longer carries its own public-read gate — it delegates that
    decision to the credential manager.

    Use the ``github`` adapter when the target repository is on GitHub or
    GitHub Enterprise — it uses the GitHub REST API directly and supports
    provider-specific features such as pull requests, rate-limit metadata,
    and custom API bases.

    Use the ``git`` adapter for provider-agnostic operations (GitLab,
    Bitbucket, generic git servers) when the provider is set to something
    other than ``github``.  The git adapter clones via GitPython and does
    not depend on git-specific APIs.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        api_base_url: str | None = None,
        default_repo: str | None = None,
        default_branch: str | None = None,
        verify_ssl: bool | None = None,
        timeout_seconds: int | None = None,
        retries: int | None = None,
    ) -> None:
        settings = get_settings()
        self.token = (token or "").strip()
        api_base_url_value = (
            api_base_url
            or os.getenv("POUNDCAKE_GITHUB_API_BASE_URL", "").strip()
            or "https://api.github.com"
        ).rstrip("/")
        resolved_verify_ssl = (
            verify_ssl
            if verify_ssl is not None
            else os.getenv("POUNDCAKE_GITHUB_VERIFY_SSL", "true").strip().lower()
            not in {
                "0",
                "false",
                "no",
                "off",
            }
        )
        self.transport = PluginHttpTransportConfig(
            service_label="GitHub",
            base_url=api_base_url_value,
            verify_ssl=bool(resolved_verify_ssl),
            bearer_token=self.token,
            timeout_seconds=float(timeout_seconds or settings.httpx_timeout_seconds),
        )
        self.api_base_url = self.transport.base_url
        self.default_repo = (
            default_repo or os.getenv("POUNDCAKE_GITHUB_REPO", "").strip() or settings.git_repo_url
        )
        self.default_branch = (
            default_branch
            or os.getenv("POUNDCAKE_GITHUB_BRANCH", "").strip()
            or settings.git_branch
            or "main"
        )
        self.timeout_seconds = timeout_seconds or settings.httpx_timeout_seconds
        self.retries = retries if retries is not None else settings.external_http_retries
        self.allow_public_read: bool = False
        self._public_archive_cache: dict[tuple[str, str, str], list[str]] = {}

    def with_credentials(self, payload: JSONObject | None) -> "GitHubClient":
        token = ""
        if payload:
            token = str(
                payload.get("token") or payload.get("access_token") or payload.get("api_key") or ""
            ).strip()
        return GitHubClient(
            token=token,
            api_base_url=self.api_base_url,
            default_repo=self.default_repo,
            default_branch=self.default_branch,
            verify_ssl=self.transport.verify_ssl,
            timeout_seconds=self.timeout_seconds,
            retries=self.retries,
        )

    def operator_config_schema(self) -> JSONObject:
        return {
            "type": "object",
            "properties": {
                "api_base_url": {
                    "type": "string",
                    "title": "GitHub API URL",
                    "format": "uri",
                },
                "default_repo": {"type": "string", "title": "Default repository"},
                "default_branch": {"type": "string", "title": "Default branch"},
                "verify_ssl": {"type": "boolean", "title": "Verify SSL"},
            },
            "required": ["api_base_url"],
            "additionalProperties": False,
        }

    def default_operator_config(self) -> JSONObject:
        return {
            "api_base_url": self.api_base_url,
            "default_repo": self.default_repo,
            "default_branch": self.default_branch,
            "verify_ssl": self.transport.verify_ssl,
        }

    def normalize_operator_config(self, config: JSONObject | None) -> JSONObject:
        raw = dict(config or {})
        api_base_url = str(raw.get("api_base_url") or self.api_base_url).strip().rstrip("/")
        if not api_base_url:
            raise ValueError("GitHub API URL is required")
        if not (api_base_url.startswith("http://") or api_base_url.startswith("https://")):
            raise ValueError("GitHub API URL must start with http:// or https://")
        return {
            "api_base_url": api_base_url,
            "default_repo": str(raw.get("default_repo") or self.default_repo or "").strip(),
            "default_branch": str(
                raw.get("default_branch") or self.default_branch or "main"
            ).strip(),
            "verify_ssl": bool(raw.get("verify_ssl", self.transport.verify_ssl)),
        }

    def with_operator_config(self, config: JSONObject | None) -> "GitHubClient":
        normalized = self.normalize_operator_config(config)
        return GitHubClient(
            token=self.token,
            api_base_url=str(normalized["api_base_url"]),
            default_repo=str(normalized["default_repo"]),
            default_branch=str(normalized["default_branch"]),
            verify_ssl=bool(normalized["verify_ssl"]),
            timeout_seconds=self.timeout_seconds,
            retries=self.retries,
        )

    def repo_ref(self, repo: str | None = None) -> GitHubRepoRef:
        return parse_github_repo(repo or self.default_repo)

    def require_token(self) -> None:
        if not self.token:
            raise GitHubClientError("GitHub token is required for write operations")

    def validate_transport_security(self) -> str | None:
        return self.transport.validate_security()

    def safe_transport_details(self) -> JSONObject:
        return self.transport.safe_details()

    async def health(self) -> JSONObject:
        start = time.time()
        response = await self._request("GET", "/rate_limit", auth_optional=True)
        return {
            "success": True,
            "status": "healthy",
            "latency_ms": int((time.time() - start) * 1000),
            "rate": response.get("rate") if isinstance(response, dict) else None,
            "authenticated": bool(self.token),
        }

    async def read_file(
        self,
        *,
        repo: str | None = None,
        path: str,
        ref: str | None = None,
    ) -> JSONObject:
        self._require_read_access()
        repo_ref = self.repo_ref(repo)
        clean_path = _clean_path(path)
        target_ref = ref or self.default_branch
        if self._can_read_public_raw():
            text = await self._read_public_raw_file(
                repo_ref=repo_ref,
                path=clean_path,
                ref=target_ref,
            )
            return {
                "success": True,
                "repo": f"{repo_ref.owner}/{repo_ref.repo}",
                "path": clean_path,
                "ref": target_ref,
                "sha": None,
                "content": text,
            }
        data = await self._request(
            "GET",
            f"/repos/{repo_ref.owner}/{repo_ref.repo}/contents/{clean_path}",
            params={"ref": target_ref},
            auth_optional=True,
        )
        if not isinstance(data, dict) or data.get("type") != "file":
            raise GitHubClientError(f"GitHub path is not a file: {path}")
        encoding = str(data.get("encoding") or "").lower()
        content = str(data.get("content") or "")
        if encoding != "base64":
            raise GitHubClientError(f"Unsupported GitHub content encoding: {encoding}")
        text = base64.b64decode(content.replace("\n", "")).decode("utf-8")
        return {
            "success": True,
            "repo": f"{repo_ref.owner}/{repo_ref.repo}",
            "path": clean_path,
            "ref": target_ref,
            "sha": data.get("sha"),
            "content": text,
        }

    async def list_files(
        self,
        *,
        repo: str | None = None,
        path: str = "",
        ref: str | None = None,
        recursive: bool = True,
    ) -> JSONObject:
        self._require_read_access()
        repo_ref = self.repo_ref(repo)
        clean_path = _clean_path(path)
        target_ref = ref or self.default_branch
        if self._can_read_public_raw():
            files = await self._list_public_archive_files(
                repo_ref=repo_ref,
                path=clean_path,
                ref=target_ref,
                recursive=recursive,
            )
            return {
                "success": True,
                "repo": f"{repo_ref.owner}/{repo_ref.repo}",
                "path": clean_path,
                "ref": target_ref,
                "files": files,
            }
        files = await self._list_files(
            repo_ref=repo_ref,
            path=clean_path,
            ref=target_ref,
            recursive=recursive,
        )
        return {
            "success": True,
            "repo": f"{repo_ref.owner}/{repo_ref.repo}",
            "path": clean_path,
            "ref": target_ref,
            "files": files,
        }

    async def commit_files(
        self,
        *,
        repo: str | None = None,
        base_branch: str | None = None,
        branch: str,
        files: dict[str, str],
        message: str,
    ) -> JSONObject:
        self.require_token()
        if not files:
            raise GitHubClientError("At least one file is required")
        repo_ref = self.repo_ref(repo)
        base = base_branch or self.default_branch
        base_ref = await self._request(
            "GET", f"/repos/{repo_ref.owner}/{repo_ref.repo}/git/ref/heads/{base}"
        )
        base_sha = _nested_str(base_ref, "object", "sha")
        if not base_sha:
            raise GitHubClientError(f"Could not resolve base branch {base}")
        try:
            await self._request(
                "POST",
                f"/repos/{repo_ref.owner}/{repo_ref.repo}/git/refs",
                json={"ref": f"refs/heads/{branch}", "sha": base_sha},
            )
        except GitHubClientError as exc:
            if "422" not in str(exc):
                raise

        entries: list[JSONObject] = []
        for path, content in sorted(files.items()):
            blob = await self._request(
                "POST",
                f"/repos/{repo_ref.owner}/{repo_ref.repo}/git/blobs",
                json={"content": content, "encoding": "utf-8"},
            )
            entries.append(
                {
                    "path": _clean_path(path),
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob.get("sha") if isinstance(blob, dict) else None,
                }
            )
        tree = await self._request(
            "POST",
            f"/repos/{repo_ref.owner}/{repo_ref.repo}/git/trees",
            json={"base_tree": base_sha, "tree": entries},
        )
        commit = await self._request(
            "POST",
            f"/repos/{repo_ref.owner}/{repo_ref.repo}/git/commits",
            json={
                "message": message,
                "tree": tree.get("sha") if isinstance(tree, dict) else None,
                "parents": [base_sha],
            },
        )
        commit_sha = commit.get("sha") if isinstance(commit, dict) else None
        await self._request(
            "PATCH",
            f"/repos/{repo_ref.owner}/{repo_ref.repo}/git/refs/heads/{branch}",
            json={"sha": commit_sha, "force": False},
        )
        return {
            "success": True,
            "repo": f"{repo_ref.owner}/{repo_ref.repo}",
            "base_branch": base,
            "branch": branch,
            "commit_sha": commit_sha,
            "files": sorted(_clean_path(path) for path in files),
        }

    async def create_pull_request(
        self,
        *,
        repo: str | None = None,
        branch: str,
        base_branch: str | None = None,
        title: str,
        body: str = "",
    ) -> JSONObject:
        self.require_token()
        repo_ref = self.repo_ref(repo)
        pr = await self._request(
            "POST",
            f"/repos/{repo_ref.owner}/{repo_ref.repo}/pulls",
            json={
                "title": title,
                "body": body,
                "head": branch,
                "base": base_branch or self.default_branch,
            },
        )
        if not isinstance(pr, dict):
            raise GitHubClientError("GitHub pull request response was not an object")
        return {
            "success": True,
            "repo": f"{repo_ref.owner}/{repo_ref.repo}",
            "number": pr.get("number"),
            "url": pr.get("html_url"),
            "state": pr.get("state"),
            "raw": pr,
        }

    async def commit_and_pr(
        self,
        *,
        repo: str | None = None,
        base_branch: str | None = None,
        branch: str,
        files: dict[str, str],
        commit_message: str,
        title: str,
        body: str = "",
    ) -> JSONObject:
        commit = await self.commit_files(
            repo=repo,
            base_branch=base_branch,
            branch=branch,
            files=files,
            message=commit_message,
        )
        pr = await self.create_pull_request(
            repo=repo,
            branch=branch,
            base_branch=base_branch,
            title=title,
            body=body,
        )
        return {"success": True, "commit": commit, "pull_request": pr}

    async def _list_files(
        self,
        *,
        repo_ref: GitHubRepoRef,
        path: str,
        ref: str,
        recursive: bool,
    ) -> list[JSONObject]:
        data = await self._request(
            "GET",
            f"/repos/{repo_ref.owner}/{repo_ref.repo}/contents/{path}",
            params={"ref": ref},
            auth_optional=True,
        )
        items = data if isinstance(data, list) else [data]
        files: list[JSONObject] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "")
            item_path = str(item.get("path") or "")
            if item_type == "file":
                files.append({"path": item_path, "sha": item.get("sha"), "size": item.get("size")})
            elif item_type == "dir" and recursive:
                files.extend(
                    await self._list_files(
                        repo_ref=repo_ref,
                        path=item_path,
                        ref=ref,
                        recursive=True,
                    )
                )
        return files

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: JSONObject | None = None,
        json: JSONObject | None = None,
        auth_optional: bool = False,
    ) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        elif not auth_optional:
            self.require_token()
        transport_error = self.validate_transport_security()
        if transport_error:
            raise GitHubClientError(transport_error)
        request_kwargs = merge_plugin_request_kwargs(
            self.transport,
            {
                "headers": headers,
                "params": params,
                "json": json,
                "timeout": self.timeout_seconds,
                "retries": self.retries,
            },
        )
        response = await request_with_retry(
            method,
            f"{self.api_base_url}{path}",
            **request_kwargs,
        )
        if response.status_code >= 400:
            raise GitHubClientError(
                f"GitHub {method} {path} returned HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
        if response.status_code == 204:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise GitHubClientError("GitHub response was not JSON") from exc

    def _can_read_public_raw(self) -> bool:
        return (
            not self.token
            and self.allow_public_read
            and self.api_base_url == "https://api.github.com"
        )

    def _require_read_access(self) -> None:
        if self.token or self.allow_public_read:
            return
        raise GitHubClientError(
            "GitHub public read requires credential-manager allow_public_read=true "
            "or an authenticated token"
        )

    async def _read_public_raw_file(
        self,
        *,
        repo_ref: GitHubRepoRef,
        path: str,
        ref: str,
    ) -> str:
        transport_error = self.validate_transport_security()
        if transport_error:
            raise GitHubClientError(transport_error)
        response = await request_with_retry(
            "GET",
            f"https://raw.githubusercontent.com/{repo_ref.owner}/{repo_ref.repo}/{ref}/{path}",
            verify=self.transport.verify_ssl,
            timeout=self.timeout_seconds,
            retries=self.retries,
        )
        if response.status_code >= 400:
            raise GitHubClientError(
                f"GitHub raw GET {path} returned HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
        return response.text

    async def _list_public_archive_files(
        self,
        *,
        repo_ref: GitHubRepoRef,
        path: str,
        ref: str,
        recursive: bool,
    ) -> list[JSONObject]:
        transport_error = self.validate_transport_security()
        if transport_error:
            raise GitHubClientError(transport_error)
        cache_key = (repo_ref.owner, repo_ref.repo, ref)
        paths = self._public_archive_cache.get(cache_key)
        if paths is None:
            response = await request_with_retry(
                "GET",
                f"https://codeload.github.com/{repo_ref.owner}/{repo_ref.repo}/zip/{ref}",
                verify=self.transport.verify_ssl,
                timeout=self.timeout_seconds,
                retries=self.retries,
            )
            if response.status_code >= 400:
                raise GitHubClientError(
                    f"GitHub archive GET {ref} returned HTTP {response.status_code}: "
                    f"{response.text[:500]}"
                )
            try:
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    paths = sorted(
                        _strip_archive_root(name)
                        for name in archive.namelist()
                        if name and not name.endswith("/")
                    )
            except zipfile.BadZipFile as exc:
                raise GitHubClientError("GitHub archive response was not a zip file") from exc
            paths = [item for item in paths if item]
            self._public_archive_cache[cache_key] = paths

        prefix = f"{path}/" if path else ""
        files: list[JSONObject] = []
        for item_path in paths:
            if prefix and not item_path.startswith(prefix):
                continue
            remainder = item_path[len(prefix) :] if prefix else item_path
            if not recursive and "/" in remainder:
                continue
            files.append({"path": item_path, "sha": None, "size": None})
        return files


def _clean_path(path: str) -> str:
    return str(path or "").strip().strip("/")


def _strip_archive_root(path: str) -> str:
    parts = str(path or "").split("/", 1)
    return parts[1].strip("/") if len(parts) == 2 else ""


def _nested_str(payload: Any, *keys: str) -> str:
    current = payload
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return str(current or "")


def get_github_helper() -> GitHubClient:
    """Factory used by the plugin helper registry."""
    return GitHubClient()
