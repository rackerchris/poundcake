#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""StackStorm API client and action management service."""

import time
import os
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from api.core.config import get_settings
from api.core.logging import get_logger
from api.core.httpx_utils import silence_httpx
from api.core.http_client import request_with_retry
from api.services.credential_manager import (
    ServicePluginCredentialError,
    read_adapter_credential_payload,
)
from api.types import JSONObject

logger = get_logger(__name__)

STACKSTORM_SERVICE_TYPE = "stackstorm"
STACKSTORM_API_KEY_CREDENTIAL_TYPE = "stackstorm_api_key"


class StackStormExecutionResponse(BaseModel):
    """Plugin-private normalized StackStorm execution document."""

    id: str
    action: str | None = None
    status: str
    parent: str | None = None
    task_key: str | None = None
    start_timestamp: Any = None
    end_timestamp: Any = None
    result: Any = None


def _truthy_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


async def _load_stackstorm_credential(credential_key_id: str = "default") -> JSONObject:
    try:
        credential = await read_adapter_credential_payload(
            service_type=STACKSTORM_SERVICE_TYPE,
            credential_type=STACKSTORM_API_KEY_CREDENTIAL_TYPE,
            credential_key_id=credential_key_id,
        )
    except ServicePluginCredentialError as exc:
        raise StackStormError("StackStorm adapter credential is not available") from exc
    if credential is None:
        raise StackStormError("StackStorm adapter credential is not available")
    return credential


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    return str(value)


def _normalize_task_key(payload: JSONObject) -> str | None:
    for key in ("task_key", "task_id", "name"):
        value = _string_or_none(payload.get(key))
        if value:
            return value

    context = payload.get("context")
    if isinstance(context, dict):
        orquesta = context.get("orquesta")
        if isinstance(orquesta, dict):
            value = _string_or_none(orquesta.get("task_id"))
            if value:
                return value
    return None


def _normalize_execution_record(payload: JSONObject) -> StackStormExecutionResponse:
    normalized = {
        "id": _string_or_none(payload.get("id") or payload.get("execution_id")) or "",
        "action": _string_or_none(payload.get("action") or payload.get("action_ref")),
        "status": _string_or_none(payload.get("status")) or "unknown",
        "parent": _string_or_none(payload.get("parent")),
        "task_key": _normalize_task_key(payload),
        "start_timestamp": payload.get("start_timestamp"),
        "end_timestamp": payload.get("end_timestamp"),
        "result": payload.get("result"),
    }
    try:
        return StackStormExecutionResponse.model_validate(normalized)
    except ValidationError as exc:
        raise StackStormError(f"Invalid StackStorm execution payload: {exc}") from exc


class StackStormError(Exception):
    """Exception raised for StackStorm API errors."""

    pass


class StackStormClient:
    """Client for interacting with StackStorm API."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        verify_ssl: bool | None = None,
        credential_payload: JSONObject | None = None,
        credential_key_id: str = "default",
    ) -> None:
        """Initialize the StackStorm client."""
        settings = get_settings()
        self.base_url = (
            base_url or os.getenv("POUNDCAKE_STACKSTORM_URL", "http://stackstorm-api:9101")
        ).rstrip("/")
        self.verify_ssl = (
            verify_ssl
            if verify_ssl is not None
            else _truthy_env(os.getenv("POUNDCAKE_STACKSTORM_VERIFY_SSL"))
        )
        self._explicit_config = base_url is not None or verify_ssl is not None
        self._credential_payload = credential_payload
        self._credential_key_id = credential_key_id.strip() or "default"
        self.retries = settings.external_http_retries

    async def _get_headers(self) -> dict[str, str]:
        """Get request headers from adapter credentials."""
        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }

        credential = self._credential_payload or await _load_stackstorm_credential(
            self._credential_key_id
        )
        api_key = str(credential.get("api_key") or credential.get("st2_api_key") or "").strip()
        auth_token = str(credential.get("auth_token") or "").strip()
        if api_key:
            headers["St2-Api-Key"] = api_key
        elif auth_token:
            headers["X-Auth-Token"] = auth_token
        else:
            raise StackStormError("StackStorm credential must include api_key or auth_token")

        return headers

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        retries = kwargs.pop("retries", self.retries)
        return await request_with_retry(
            method,
            f"{self.base_url}{path}",
            verify=self.verify_ssl,
            retries=retries,
            **kwargs,
        )

    async def execute_action(
        self,
        action_ref: str,
        req_id: str,
        parameters: JSONObject | None = None,
        timeout: int = 300,
        action_is_workflow: bool = False,
    ) -> JSONObject:
        """Execute a StackStorm action.

        Args:
            action_ref: The action reference (pack.action_name)
            parameters: Parameters to pass to the action
            timeout: Request timeout in seconds
            req_id: Original request id from dishes.req_id
            action_is_workflow: Whether the referenced action is a StackStorm workflow.

        Returns:
            The execution result from StackStorm
        """
        payload = {
            "action": action_ref,
            "parameters": parameters or {},
        }
        if action_is_workflow:
            payload["action_is_workflow"] = True

        headers = await self._get_headers()

        start_time = time.time()
        try:
            logger.info(
                "Executing StackStorm action",
                extra={"req_id": req_id, "action_ref": action_ref, "method": "POST"},
            )

            response = await self._request(
                "POST",
                "/v1/executions",
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            latency_ms = int((time.time() - start_time) * 1000)

            if response.status_code == 201:
                result: JSONObject = response.json()
                logger.info(
                    "Action execution started successfully",
                    extra={
                        "req_id": req_id,
                        "action_ref": action_ref,
                        "method": "POST",
                        "status_code": response.status_code,
                        "latency_ms": latency_ms,
                        "execution_id": result.get("id"),
                        "status": result.get("status"),
                    },
                )
                return result

            error_msg = f"StackStorm API error: {response.status_code} - {response.text}"
            logger.error(
                "StackStorm API error",
                extra={
                    "req_id": req_id,
                    "action_ref": action_ref,
                    "method": "POST",
                    "status_code": response.status_code,
                    "latency_ms": latency_ms,
                    "error": response.text,
                },
            )
            raise StackStormError(error_msg)

        except httpx.TimeoutException as e:
            latency_ms = int((time.time() - start_time) * 1000)
            logger.error(
                "StackStorm request timed out",
                extra={
                    "req_id": req_id,
                    "action_ref": action_ref,
                    "method": "POST",
                    "timeout": timeout,
                    "latency_ms": latency_ms,
                    "error": str(e),
                },
                exc_info=True,
            )
            raise StackStormError(f"StackStorm request timed out after {timeout}s") from e

        except httpx.RequestError as e:
            latency_ms = int((time.time() - start_time) * 1000)
            logger.error(
                "StackStorm request failed",
                extra={
                    "req_id": req_id,
                    "action_ref": action_ref,
                    "method": "POST",
                    "latency_ms": latency_ms,
                    "error": str(e),
                },
                exc_info=True,
            )
            raise StackStormError(f"StackStorm request failed: {e}") from e

    async def get_execution(self, execution_id: str) -> StackStormExecutionResponse:
        """Get the status of a StackStorm execution.

        Args:
            execution_id: The execution ID to check

        Returns:
            The execution details
        """
        headers = await self._get_headers()

        response = await self._request(
            "GET",
            f"/v1/executions/{execution_id}",
            headers=headers,
            timeout=30,
        )

        if response.status_code == 200:
            result: JSONObject = response.json()
            return _normalize_execution_record(result)
        raise StackStormError(f"Failed to get execution {execution_id}: {response.status_code}")

    async def cancel_execution(self, execution_id: str, *, status: str = "canceled") -> bool:
        """Cancel a StackStorm execution."""
        if status != "canceled":
            raise StackStormError(f"Unsupported StackStorm cancellation status: {status}")
        headers = await self._get_headers()

        response = await self._request(
            "DELETE",
            f"/v1/executions/{execution_id}",
            headers=headers,
            timeout=30,
        )

        if response.status_code in (200, 202, 204):
            return True
        raise StackStormError(f"Failed to cancel execution {execution_id}: {response.status_code}")

    async def health_check(self, req_id: str | None = None) -> bool:
        """Check if StackStorm API is accessible."""
        headers = await self._get_headers()
        if req_id:
            headers["X-Request-ID"] = req_id

        start_time = time.time()
        with silence_httpx():
            try:
                response = await self._request(
                    "GET",
                    "/v1/actions",
                    headers=headers,
                    params={"limit": 1},
                    timeout=10,
                    retries=0,
                )
                # 200 => authenticated and healthy
                return response.status_code == 200
            except Exception as e:
                latency_ms = int((time.time() - start_time) * 1000)
                logger.warning(
                    "StackStorm health check failed",
                    extra=(
                        {
                            "req_id": req_id,
                            "method": "GET",
                            "latency_ms": latency_ms,
                            "error": str(e),
                        }
                        if req_id
                        else {"method": "GET", "error": str(e)}
                    ),
                )
                return False


class StackStormActionManager:
    """Manager for PoundCake-owned StackStorm action metadata."""

    def __init__(self, client: StackStormClient | None = None) -> None:
        """Initialize with a StackStorm client."""
        self._client = client or StackStormClient()
        self._retries = self._client.retries

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        retries = kwargs.pop("retries", self._retries)
        return await request_with_retry(
            method,
            f"{self._client.base_url}{path}",
            verify=self._client.verify_ssl,
            retries=retries,
            **kwargs,
        )

    async def get_action(self, action_ref: str) -> JSONObject | None:
        """Get details of a specific action.

        Args:
            action_ref: Action reference (pack.action)

        Returns:
            Action definition or None
        """
        headers = await self._client._get_headers()

        response = await self._request(
            "GET",
            f"/v1/actions/{action_ref}",
            headers=headers,
            timeout=30,
        )

        if response.status_code == 200:
            result: JSONObject = response.json()
            return result
        return None

    async def update_action(
        self,
        action_ref: str,
        action_data: JSONObject,
    ) -> JSONObject | None:
        """Update a StackStorm action definition.

        Args:
            action_ref: Action reference (pack.action)
            action_data: Updated action definition

        Returns:
            Updated action definition or None if failed
        """
        headers = await self._client._get_headers()

        response = await self._request(
            "PUT",
            f"/v1/actions/{action_ref}",
            headers=headers,
            json=action_data,
            timeout=30,
        )

        if response.status_code == 200:
            result: JSONObject = response.json()
            logger.info(
                "StackStorm action updated successfully",
                extra={"action_ref": action_ref},
            )
            return result
        else:
            logger.error(
                "Failed to update StackStorm action",
                extra={
                    "action_ref": action_ref,
                    "status_code": response.status_code,
                    "error": response.text,
                },
            )
            return None

    async def create_action(
        self,
        action_data: JSONObject,
    ) -> JSONObject | None:
        """Create a new StackStorm action.

        Args:
            action_data: Action definition

        Returns:
            Created action definition or None if failed
        """
        headers = await self._client._get_headers()

        response = await self._request(
            "POST",
            "/v1/actions",
            headers=headers,
            json=action_data,
            timeout=30,
        )

        if response.status_code == 201:
            result: JSONObject = response.json()
            logger.info(
                "StackStorm action created successfully",
                extra={"action_ref": result.get("ref")},
            )
            return result
        else:
            logger.error(
                "Failed to create StackStorm action",
                extra={"status_code": response.status_code, "error": response.text},
            )
            return None

    async def sync_action_definitions(self, actions: list[JSONObject]) -> JSONObject:
        """Create or update PoundCake-owned StackStorm action metadata."""
        created = 0
        updated = 0
        unchanged = 0
        refs: list[str] = []
        for action in actions:
            pack = str(action.get("pack") or "").strip()
            name = str(action.get("name") or "").strip()
            if not pack or not name:
                raise StackStormError("StackStorm content action requires pack and name")
            action_ref = f"{pack}.{name}"
            refs.append(action_ref)
            existing = await self.get_action(action_ref)
            if existing is None:
                created_payload = await self.create_action(action)
                if created_payload is None:
                    raise StackStormError(f"Failed to create StackStorm action {action_ref}")
                created += 1
                continue
            if _action_definition_matches(existing, action):
                unchanged += 1
                continue
            updated_payload = await self.update_action(action_ref, action)
            if updated_payload is None:
                raise StackStormError(f"Failed to update StackStorm action {action_ref}")
            updated += 1
        return {
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "processed": len(actions),
            "action_refs": refs,
        }


def _action_definition_matches(existing: JSONObject, desired: JSONObject) -> bool:
    for key in ("name", "pack", "runner_type", "entry_point", "enabled", "parameters"):
        if existing.get(key) != desired.get(key):
            return False
    return True


# Global instances
_client: StackStormClient | None = None
_action_manager: StackStormActionManager | None = None


def get_stackstorm_client() -> StackStormClient:
    """Get the global StackStorm client."""
    global _client
    if _client is None:
        _client = StackStormClient()
    return _client


def get_action_manager() -> StackStormActionManager:
    """Get the global StackStorm action manager."""
    global _action_manager
    if _action_manager is None:
        _action_manager = StackStormActionManager(get_stackstorm_client())
    return _action_manager
