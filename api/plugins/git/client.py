"""Git repository helper for the external Git service plugin."""

from __future__ import annotations

import importlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from api.core.config import get_settings
from api.core.logging import get_logger
from api.plugins.github.client import GitHubClient, GitHubClientError
from api.types import JSONObject

logger = get_logger(__name__)

GIT_REPOSITORY_AUTH_CREDENTIAL_TYPE = "git_repository_auth"


class GitClientError(RuntimeError):
    """Raised when Git plugin operations cannot complete."""


class GitClient:
    """Perform Git repository reads and writes for order-driven GitOps.

    The credential manager (via ``allow_public_read`` in the credential record)
    is the authoritative policy gate for unauthenticated public reads.  This
    client carries an ``allow_public_read`` attribute set by the adapter at
    credential time; the attribute controls whether public-read paths may be
    used.  Default is ``False`` — adapters must explicitly set it from the
    credential manager.

    Use the ``git`` adapter when the target is a non-GitHub git provider
    (GitLab, Bitbucket, generic git servers) or when GitPython-based
    clone/pull workflows are needed.  Set ``provider`` to the target
    provider type — only ``github`` delegates PR creation back to the
    GitHub REST API; other providers will skip PR creation.

    Prefer the ``github`` adapter when the target repository is on GitHub
    or GitHub Enterprise — it uses the GitHub REST API directly, supports
    provider-specific features (rate limits, custom API bases), and
    avoids the filesystem overhead of cloning.
    """

    def __init__(
        self,
        *,
        repo_url: str | None = None,
        default_branch: str | None = None,
        provider: str | None = None,
        token: str | None = None,
        ssh_key_path: str | None = None,
        user_name: str | None = None,
        user_email: str | None = None,
        work_dir: Path | None = None,
        retries: int | None = None,
    ) -> None:
        settings = get_settings()
        self.repo_url = (repo_url if repo_url is not None else settings.git_repo_url).strip()
        self.default_branch = (
            default_branch if default_branch is not None else settings.git_branch
        ).strip() or "main"
        self.provider = (
            (provider if provider is not None else settings.git_provider).strip().lower()
        )
        self.token = (token or "").strip()
        self.ssh_key_path = (ssh_key_path or "").strip()
        self.user_name = (user_name if user_name is not None else settings.git_user_name).strip()
        self.user_email = (
            user_email if user_email is not None else settings.git_user_email
        ).strip()
        self.retries = int(retries if retries is not None else settings.external_http_retries)
        self.work_dir = work_dir or Path(tempfile.gettempdir()) / "poundcake-git-plugin"
        self.repo_path: Path | None = None
        self.allow_public_read: bool = False

    def with_credentials(self, payload: JSONObject | None) -> "GitClient":
        """Return a client with optional adapter credentials applied."""
        if not payload:
            return self
        token = str(
            payload.get("token") or payload.get("access_token") or payload.get("password") or ""
        ).strip()
        ssh_key_path = str(payload.get("ssh_key_path") or "").strip()
        return GitClient(
            repo_url=str(payload.get("repo_url") or self.repo_url).strip(),
            default_branch=str(payload.get("default_branch") or self.default_branch).strip(),
            provider=str(payload.get("provider") or self.provider).strip(),
            token=token,
            ssh_key_path=ssh_key_path,
            user_name=str(payload.get("user_name") or self.user_name).strip(),
            user_email=str(payload.get("user_email") or self.user_email).strip(),
            work_dir=self.work_dir,
            retries=self.retries,
        )

    def operator_config_schema(self) -> JSONObject:
        return {
            "type": "object",
            "properties": {
                "repo_url": {"type": "string", "title": "Repository URL"},
                "default_branch": {"type": "string", "title": "Default branch"},
                "provider": {"type": "string", "title": "Provider"},
                "user_name": {"type": "string", "title": "Commit user name"},
                "user_email": {"type": "string", "title": "Commit user email"},
            },
            "required": [],
            "additionalProperties": False,
        }

    def default_operator_config(self) -> JSONObject:
        return {
            "repo_url": self.repo_url,
            "default_branch": self.default_branch,
            "provider": self.provider,
            "user_name": self.user_name,
            "user_email": self.user_email,
        }

    def normalize_operator_config(self, config: JSONObject | None) -> JSONObject:
        raw = dict(config or {})
        provider = str(raw.get("provider") or self.provider or "").strip().lower()
        if provider and provider not in {"github", "gitlab", "generic"}:
            raise ValueError("Git provider must be github, gitlab, or generic")
        return {
            "repo_url": str(raw.get("repo_url") or self.repo_url or "").strip(),
            "default_branch": str(
                raw.get("default_branch") or self.default_branch or "main"
            ).strip(),
            "provider": provider,
            "user_name": str(raw.get("user_name") or self.user_name or "").strip(),
            "user_email": str(raw.get("user_email") or self.user_email or "").strip(),
        }

    def with_operator_config(self, config: JSONObject | None) -> "GitClient":
        normalized = self.normalize_operator_config(config)
        return GitClient(
            repo_url=str(normalized["repo_url"]),
            default_branch=str(normalized["default_branch"]),
            provider=str(normalized["provider"]),
            token=self.token,
            ssh_key_path=self.ssh_key_path,
            user_name=str(normalized["user_name"]),
            user_email=str(normalized["user_email"]),
            work_dir=self.work_dir,
            retries=self.retries,
        )

    def safe_transport_details(self) -> JSONObject:
        return {
            "repo_configured": bool(self.repo_url),
            "default_branch": self.default_branch,
            "provider": self.provider or "none",
            "auth_modes": {
                "token": bool(self.token),
                "ssh_key_path": bool(self.ssh_key_path),
            },
        }

    def has_auth_credentials(self) -> bool:
        return bool(self.token or self.ssh_key_path)

    async def health(self) -> JSONObject:
        return {
            "success": True,
            "status": "healthy",
            **self.safe_transport_details(),
            "gitpython_available": self._load_git_module() is not None,
        }

    async def read_file(
        self,
        *,
        repo_url: str | None = None,
        path: str,
        ref: str | None = None,
    ) -> JSONObject:
        self._require_read_access()
        repo_path = await self.clone_or_pull(repo_url=repo_url, ref=ref)
        rel_path = _safe_relative_path(path)
        full_path = repo_path / rel_path
        if not full_path.is_file():
            raise GitClientError(f"Repository file not found: {rel_path}")
        return {
            "success": True,
            "path": rel_path,
            "content": full_path.read_text(encoding="utf-8"),
        }

    async def list_files(
        self,
        *,
        repo_url: str | None = None,
        path: str = "",
        ref: str | None = None,
        recursive: bool = True,
    ) -> JSONObject:
        self._require_read_access()
        repo_path = await self.clone_or_pull(repo_url=repo_url, ref=ref)
        base = repo_path / _safe_relative_path(path, allow_empty=True)
        if not base.exists():
            return {"success": True, "files": []}
        iterator = base.rglob("*") if recursive else base.glob("*")
        files = [
            {"path": item.relative_to(repo_path).as_posix(), "size": item.stat().st_size}
            for item in sorted(iterator)
            if item.is_file()
        ]
        return {"success": True, "files": files}

    async def commit_files(
        self,
        *,
        repo_url: str | None = None,
        base_branch: str | None = None,
        branch: str,
        files: dict[str, str | None],
        message: str,
        push: bool = True,
    ) -> JSONObject:
        if not files:
            raise GitClientError("No Git file changes were provided")
        if not branch.strip():
            raise GitClientError("Git commit requires a target branch")
        repo_path = await self.clone_or_pull(repo_url=repo_url, ref=base_branch)
        git = self._require_git_module()
        repo = git.Repo(repo_path)
        current_ref = str(repo.head.reference)
        branch_name = branch.strip()

        if branch_name in [head.name for head in repo.heads]:
            repo.git.checkout(branch_name)
        else:
            repo.create_head(branch_name)
            repo.git.checkout(branch_name)

        added_paths: list[str] = []
        removed_paths: list[str] = []
        for raw_path, content in sorted(files.items()):
            rel_path = _safe_relative_path(raw_path)
            full_path = repo_path / rel_path
            if content is None:
                if full_path.exists():
                    full_path.unlink()
                if rel_path in repo.git.ls_files(rel_path).splitlines():
                    removed_paths.append(rel_path)
                continue
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            added_paths.append(rel_path)

        if added_paths:
            repo.index.add(added_paths)
        if removed_paths:
            repo.index.remove(removed_paths)
        repo.config_writer().set_value("user", "name", self.user_name).release()
        repo.config_writer().set_value("user", "email", self.user_email).release()

        if not repo.is_dirty(untracked_files=True):
            repo.git.checkout(current_ref)
            return {"success": True, "status": "unchanged", "branch": ""}

        commit = repo.index.commit(message)
        if push:
            credentialed_url = self._credentialed_repo_url(repo_url=repo_url)
            if credentialed_url:
                repo.remotes.origin.set_url(credentialed_url)
            repo.git.push("--set-upstream", "origin", branch_name, env=self._git_env())
        repo.git.checkout(current_ref)
        return {
            "success": True,
            "status": "committed",
            "branch": branch_name,
            "commit_sha": str(commit.hexsha),
            "pushed": bool(push),
            "changed_files": sorted(files),
        }

    async def create_pull_request(
        self,
        *,
        repo_url: str | None = None,
        branch: str,
        base_branch: str | None = None,
        title: str,
        body: str = "",
    ) -> JSONObject:
        provider = (self.provider or "none").strip().lower()
        if provider not in {"github", "gitlab", "generic"}:
            raise GitClientError(f"Unsupported Git pull request provider: {provider}")
        if provider == "github":
            try:
                return await GitHubClient(
                    token=self.token,
                    default_repo=repo_url or self.repo_url,
                    default_branch=base_branch or self.default_branch,
                    retries=self.retries,
                ).create_pull_request(
                    branch=branch, base_branch=base_branch, title=title, body=body
                )
            except GitHubClientError as exc:
                raise GitClientError(str(exc)) from exc
        return {
            "success": True,
            "skipped": True,
            "message": (
                f"Pull request creation is not implemented for provider '{provider}'; "
                "use the github adapter or a provider-specific adapter instead."
            ),
        }

    async def commit_and_pr(
        self,
        *,
        repo_url: str | None = None,
        base_branch: str | None = None,
        branch: str,
        files: dict[str, str | None],
        commit_message: str,
        title: str,
        body: str = "",
        push: bool = True,
    ) -> JSONObject:
        commit = await self.commit_files(
            repo_url=repo_url,
            base_branch=base_branch,
            branch=branch,
            files=files,
            message=commit_message,
            push=push,
        )
        if not commit.get("branch"):
            return {"success": True, "commit": commit, "pull_request": None}
        pr = await self.create_pull_request(
            repo_url=repo_url,
            branch=branch,
            base_branch=base_branch,
            title=title,
            body=body,
        )
        return {"success": True, "commit": commit, "pull_request": pr}

    async def clone_or_pull(
        self,
        *,
        repo_url: str | None = None,
        ref: str | None = None,
    ) -> Path:
        url = (repo_url or self.repo_url).strip()
        if not url:
            raise GitClientError("Git repository URL is not configured")
        git = self._require_git_module()
        branch = (ref or self.default_branch).strip() or self.default_branch
        self.work_dir.mkdir(parents=True, exist_ok=True)
        repo_name = url.rstrip("/").split("/")[-1].replace(".git", "") or "repository"
        self.repo_path = self.work_dir / repo_name
        credentialed_url = self._credentialed_repo_url(repo_url=url)

        if self.repo_path.exists():
            repo = git.Repo(self.repo_path)
            origin = repo.remotes.origin
            if credentialed_url:
                origin.set_url(credentialed_url)
            origin.pull(branch, env=self._git_env())
        else:
            git.Repo.clone_from(
                credentialed_url or url,
                self.repo_path,
                branch=branch,
                env=self._git_env(),
            )
        return self.repo_path

    def cleanup(self) -> None:
        if self.repo_path and self.repo_path.exists():
            shutil.rmtree(self.repo_path)

    def _credentialed_repo_url(self, *, repo_url: str | None = None) -> str:
        url = (repo_url or self.repo_url).strip()
        if self.token and url.startswith("https://") and "github.com" in url:
            return url.replace("https://", f"https://x-access-token:{self.token}@")
        return url

    def _require_read_access(self) -> None:
        if self.has_auth_credentials() or self.allow_public_read:
            return
        raise GitClientError(
            "Git public read requires credential-manager allow_public_read=true "
            "or authenticated repository credentials"
        )

    def _git_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.token and "github.com" in self.repo_url:
            env["GIT_ASKPASS"] = "echo"
            env["GIT_USERNAME"] = "oauth2"
            env["GIT_PASSWORD"] = self.token
        if self.ssh_key_path:
            env["GIT_SSH_COMMAND"] = f"ssh -i {self.ssh_key_path} -o StrictHostKeyChecking=no"
        return env

    def _require_git_module(self) -> Any:
        git = self._load_git_module()
        if git is None:
            raise GitClientError("GitPython is required for git plugin operations")
        return git

    @staticmethod
    def _load_git_module() -> Any | None:
        try:
            return importlib.import_module("git")
        except ImportError as exc:
            logger.error("Git plugin support unavailable", extra={"error": str(exc)})
            return None


def _safe_relative_path(value: str, *, allow_empty: bool = False) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        if allow_empty:
            return ""
        raise GitClientError("Git file path must not be empty")
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise GitClientError(f"Unsafe Git repository path: {value}")
    return path.as_posix()


def get_git_helper() -> GitClient:
    """Return the Git plugin helper."""
    return GitClient()
