"""OCI registry client for Helm chart metadata."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from api.core.config import get_settings
from api.core.http_client import request_with_retry
from api.core.logging import get_logger

logger = get_logger(__name__)

OCI_MANIFEST_ACCEPT = (
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)
OCI_CHART_CONFIG_MEDIA_TYPE = "application/vnd.cncf.helm.config.v1+json"


@dataclass(frozen=True)
class VersionKey:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...]


@dataclass(frozen=True)
class OciRepositoryRef:
    registry: str
    repository: str


@dataclass(frozen=True)
class OciChartRelease:
    chart_version: str
    app_version: str
    created_at: datetime | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_version(value: str | None) -> VersionKey | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    raw = raw[1:] if raw.startswith("v") else raw
    without_build = raw.split("+", 1)[0]
    core, prerelease = without_build.split("-", 1) if "-" in without_build else (without_build, "")
    parts = core.split(".")
    if len(parts) > 3 or any(not part.isdigit() for part in parts):
        return None
    padded = [int(part) for part in parts] + [0] * (3 - len(parts))
    prerelease_parts = tuple(part for part in re.split(r"[.-]", prerelease) if part)
    return VersionKey(padded[0], padded[1], padded[2], prerelease_parts)


def _compare_identifier(left: str, right: str) -> int:
    left_numeric = left.isdigit()
    right_numeric = right.isdigit()
    if left_numeric and right_numeric:
        left_int = int(left)
        right_int = int(right)
        return (left_int > right_int) - (left_int < right_int)
    if left_numeric != right_numeric:
        return -1 if left_numeric else 1
    return (left > right) - (left < right)


def compare_versions(left: str | None, right: str | None) -> int:
    left_key = _parse_version(left)
    right_key = _parse_version(right)
    if left_key is None and right_key is None:
        return 0
    if left_key is None:
        return -1
    if right_key is None:
        return 1
    left_core = (left_key.major, left_key.minor, left_key.patch)
    right_core = (right_key.major, right_key.minor, right_key.patch)
    if left_core != right_core:
        return (left_core > right_core) - (left_core < right_core)
    if not left_key.prerelease and right_key.prerelease:
        return 1
    if left_key.prerelease and not right_key.prerelease:
        return -1
    for left_part, right_part in zip(left_key.prerelease, right_key.prerelease):
        comparison = _compare_identifier(left_part, right_part)
        if comparison:
            return comparison
    return (len(left_key.prerelease) > len(right_key.prerelease)) - (
        len(left_key.prerelease) < len(right_key.prerelease)
    )


def is_prerelease(value: str | None) -> bool:
    key = _parse_version(value)
    return bool(key and key.prerelease)


def _sortable_version(value: str) -> tuple[int, int, int, int, tuple[str, ...]]:
    key = _parse_version(value) or VersionKey(0, 0, 0, ("invalid",))
    return (key.major, key.minor, key.patch, 0 if key.prerelease else 1, key.prerelease)


def _release_sort_key(
    release: OciChartRelease,
) -> tuple[tuple[int, int, int, int, tuple[str, ...]], tuple[int, int, int, int, tuple[str, ...]]]:
    return _sortable_version(release.app_version), _sortable_version(release.chart_version)


def parse_oci_repository(value: str) -> OciRepositoryRef:
    raw = str(value or "").strip()
    if not raw.startswith("oci://"):
        raise ValueError("release update OCI repository must start with oci://")
    stripped = raw.removeprefix("oci://").strip("/")
    registry, _, repository = stripped.partition("/")
    if not registry or not repository:
        raise ValueError("release update OCI repository must include registry and repository")
    return OciRepositoryRef(registry=registry, repository=repository)


def _parse_www_authenticate(value: str | None) -> tuple[str, dict[str, str]]:
    raw = str(value or "").strip()
    if not raw:
        return "", {}
    scheme, _, rest = raw.partition(" ")
    items = {
        key: parsed_value for key, parsed_value in re.findall(r'([A-Za-z0-9_]+)="([^"]*)"', rest)
    }
    return scheme.lower(), items


def _parse_link_next(value: str | None) -> str | None:
    raw = str(value or "")
    for part in raw.split(","):
        match = re.search(r"<([^>]+)>;\s*rel=\"next\"", part.strip())
        if match:
            return match.group(1)
    return None


def _merge_query(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(params)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _parse_registry_datetime(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class OciChartClient:
    """Small OCI registry client for Helm chart metadata."""

    def __init__(
        self,
        *,
        oci_repository: str,
        registry_username: str = "",
        registry_password: str = "",
        registry_token: str = "",
    ) -> None:
        self.oci_repository = oci_repository
        self.ref = parse_oci_repository(oci_repository)
        self.registry_username = registry_username
        self.registry_password = registry_password
        self.registry_token = registry_token
        self._bearer_token: str | None = None

    @property
    def _base_url(self) -> str:
        return f"https://{self.ref.registry}/v2/{self.ref.repository}"

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        retry_auth: bool = True,
    ) -> httpx.Response:
        request_headers = dict(headers or {})
        if self._bearer_token:
            request_headers["Authorization"] = f"Bearer {self._bearer_token}"
        elif self.registry_token:
            request_headers["Authorization"] = f"Bearer {self.registry_token}"

        response = await request_with_retry(
            method,
            url,
            headers=request_headers,
            timeout=15,
            retries=2,
        )
        if response.status_code == 401 and retry_auth:
            token = await self._fetch_bearer_token(response.headers.get("www-authenticate"))
            if token:
                self._bearer_token = token
                return await self._request(method, url, headers=headers, retry_auth=False)
        response.raise_for_status()
        return response

    async def _fetch_bearer_token(self, challenge: str | None) -> str | None:
        scheme, params = _parse_www_authenticate(challenge)
        if scheme != "bearer" or not params.get("realm"):
            return None

        token_headers: dict[str, str] = {}
        username = self.registry_username.strip()
        password = self.registry_password.strip() or self.registry_token.strip()
        if username or password:
            credential = f"{username or 'token'}:{password}".encode("utf-8")
            token_headers["Authorization"] = "Basic " + base64.b64encode(credential).decode("ascii")

        token_url = _merge_query(
            params["realm"],
            {
                key: value
                for key, value in {
                    "service": params.get("service"),
                    "scope": params.get("scope", f"repository:{self.ref.repository}:pull"),
                }.items()
                if value
            },
        )
        response = await request_with_retry(
            "GET",
            token_url,
            headers=token_headers,
            timeout=15,
            retries=2,
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("token") or payload.get("access_token")
        return str(token) if token else None

    async def list_tags(self) -> list[str]:
        tags: list[str] = []
        next_url: str | None = f"{self._base_url}/tags/list?n=100"
        while next_url:
            response = await self._request("GET", next_url)
            payload = response.json()
            tags.extend(str(tag) for tag in payload.get("tags") or [])
            link_next = _parse_link_next(response.headers.get("link"))
            if link_next and link_next.startswith("/"):
                next_url = f"https://{self.ref.registry}{link_next}"
            else:
                next_url = link_next
        return tags

    async def get_chart_release(self, tag: str) -> OciChartRelease | None:
        response = await self._request(
            "GET",
            f"{self._base_url}/manifests/{tag}",
            headers={"Accept": OCI_MANIFEST_ACCEPT},
        )
        manifest = response.json()
        config = manifest.get("config") if isinstance(manifest, dict) else {}
        if not isinstance(config, dict) or not config.get("digest"):
            return None
        config_response = await self._request(
            "GET",
            f"{self._base_url}/blobs/{config['digest']}",
            headers={"Accept": OCI_CHART_CONFIG_MEDIA_TYPE},
        )
        chart = config_response.json()
        chart_version = str(chart.get("version") or tag).strip()
        app_version = str(chart.get("appVersion") or "").strip()
        if not chart_version or not app_version:
            return None
        annotations = manifest.get("annotations") if isinstance(manifest, dict) else {}
        created_at = None
        if isinstance(annotations, dict):
            created_at = _parse_registry_datetime(
                annotations.get("org.opencontainers.image.created")
            )
        return OciChartRelease(
            chart_version=chart_version,
            app_version=app_version,
            created_at=created_at,
        )

    async def fetch_latest_release(self, *, include_prereleases: bool) -> OciChartRelease | None:
        releases: list[OciChartRelease] = []
        for tag in await self.list_tags():
            if _parse_version(tag) is None:
                continue
            try:
                release = await self.get_chart_release(tag)
            except httpx.HTTPError as exc:
                logger.warning(
                    "Failed to inspect OCI chart tag",
                    extra={"tag": tag, "oci_repository": self.oci_repository, "error": str(exc)},
                )
                continue
            if release is None:
                continue
            if (
                _parse_version(release.chart_version) is None
                or _parse_version(release.app_version) is None
            ):
                continue
            if not include_prereleases and (
                is_prerelease(release.chart_version) or is_prerelease(release.app_version)
            ):
                continue
            releases.append(release)
        if not releases:
            return None
        return max(releases, key=_release_sort_key)


def _client_from_settings() -> OciChartClient:
    settings = get_settings()
    return OciChartClient(
        oci_repository=settings.release_update_oci_repository,
        registry_username=settings.release_update_registry_username,
        registry_password=settings.release_update_registry_password,
        registry_token=settings.release_update_registry_token,
    )


async def fetch_latest_chart_info(
    oci_repository: str,
    *,
    include_prereleases: bool,
    username: str = "",
    password: str = "",
    token: str = "",
) -> dict[str, Any] | None:
    client = OciChartClient(
        oci_repository=oci_repository,
        registry_username=username,
        registry_password=password,
        registry_token=token,
    )
    latest = await client.fetch_latest_release(include_prereleases=include_prereleases)
    if latest is None:
        return None
    return {
        "chart_version": latest.chart_version,
        "app_version": latest.app_version,
        "created_at": latest.created_at.isoformat() if latest.created_at else None,
    }
