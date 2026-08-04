#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""Prometheus API client and rule management service.

Ported from poundcake/src/poundcake/prometheus.py.
Note: CRD manager and Git integration can be added as separate modules.
"""

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from api.core.config import get_settings
from api.core.http_client import request_with_retry
from api.core.httpx_utils import silence_httpx
from api.core.logging import get_logger
from api.plugins.state import (
    PLUGIN_RUN_STATE_DEGRADED,
    PLUGIN_RUN_STATE_FAILED,
    PLUGIN_RUN_STATE_HEALTHY,
)
from api.plugins.transport import (
    PluginHttpTransportConfig,
    http_operator_config_schema,
    merge_plugin_request_kwargs,
    normalize_http_operator_config,
)
from api.types import JSONObject

logger = get_logger(__name__)
SYSTEM_REQ_ID = "SYSTEM-PROM"
PROMETHEUS_LABEL_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _optional_string(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _prometheus_label_matcher(name: str, value: object) -> str:
    label_name = str(name or "").strip()
    if not PROMETHEUS_LABEL_NAME_RE.match(label_name):
        raise ValueError(f"Invalid Prometheus label name: {label_name}")
    label_value = str(value or "").strip()
    return f"{label_name}={json.dumps(label_value)}"


def _alert_evidence_query(*, alert_name: str, labels: JSONObject | None = None) -> str:
    normalized_alert_name = str(alert_name or "").strip()
    if not normalized_alert_name:
        raise ValueError("Prometheus alert evidence requires alert_name")
    matchers = [_prometheus_label_matcher("alertname", normalized_alert_name)]
    for key, value in sorted((labels or {}).items()):
        if key == "alertname" or value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            matchers.append(_prometheus_label_matcher(str(key), value))
    return f"ALERTS{{{','.join(matchers)}}}"


def _prometheus_unhealthy_status(status_code: int) -> str:
    if status_code in {401, 403}:
        return PLUGIN_RUN_STATE_FAILED
    return PLUGIN_RUN_STATE_DEGRADED


class PrometheusClient:
    """Client for interacting with Prometheus API."""

    def __init__(self, transport: PluginHttpTransportConfig | None = None) -> None:
        """Initialize the Prometheus client."""
        if transport is not None:
            self.retries = get_settings().external_http_retries
            self.transport = transport
            return
        settings = get_settings()
        self.retries = settings.external_http_retries
        self.transport = PluginHttpTransportConfig(
            service_label="Prometheus",
            base_url=settings.prometheus_url.rstrip("/"),
            verify_ssl=settings.prometheus_verify_ssl,
            timeout_seconds=30.0,
        )

    def with_credentials(self, payload: JSONObject | None) -> "PrometheusClient":
        return PrometheusClient(self.transport.with_credentials(payload))

    def operator_config_schema(self) -> JSONObject:
        return http_operator_config_schema(service_label="Prometheus")

    def default_operator_config(self) -> JSONObject:
        return {
            "url": self.transport.base_url,
            "verify_ssl": self.transport.verify_ssl,
            "timeout_seconds": self.transport.timeout_seconds,
        }

    def normalize_operator_config(self, config: JSONObject | None) -> JSONObject:
        return normalize_http_operator_config(
            config,
            default_url=self.transport.base_url,
            default_verify_ssl=self.transport.verify_ssl,
            default_timeout_seconds=self.transport.timeout_seconds,
            service_label="Prometheus",
        )

    def with_operator_config(self, config: JSONObject | None) -> "PrometheusClient":
        return PrometheusClient(
            self.transport.with_operator_config(self.normalize_operator_config(config))
        )

    async def _request(self, method: str, path_or_url: str, **kwargs):
        url = path_or_url if path_or_url.startswith("http") else f"{self.base_url}{path_or_url}"
        kwargs = merge_plugin_request_kwargs(self.transport, kwargs)
        retries = kwargs.pop("retries", self.retries)
        return await request_with_retry(
            method,
            url,
            retries=retries,
            **kwargs,
        )

    @property
    def base_url(self) -> str:
        return self.transport.base_url

    @property
    def verify_ssl(self) -> bool:
        return self.transport.verify_ssl

    @property
    def auth_mode(self) -> str:
        return self.transport.auth_mode

    @property
    def secure_transport(self) -> bool:
        return self.transport.secure_transport

    def validate_transport_security(self) -> str | None:
        return self.transport.validate_security()

    async def get_rules(self) -> list[JSONObject]:
        """Fetch all alert rules from Prometheus.

        Returns:
            List of alert rule groups with their rules
        """
        try:
            start_time = time.time()
            response = await self._request(
                "GET",
                "/api/v1/rules",
                params={"type": "alert"},
                timeout=30,
            )
            latency_ms = int((time.time() - start_time) * 1000)

            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    groups = data.get("data", {}).get("groups", [])
                    return self._flatten_rules(groups)
                else:
                    logger.error(
                        "Prometheus API returned error",
                        extra={
                            "req_id": SYSTEM_REQ_ID,
                            "method": "GET",
                            "status_code": response.status_code,
                            "latency_ms": latency_ms,
                            "error": data.get("error"),
                        },
                    )
                    return []
            else:
                logger.error(
                    "Failed to fetch Prometheus rules",
                    extra={
                        "req_id": SYSTEM_REQ_ID,
                        "method": "GET",
                        "status_code": response.status_code,
                        "latency_ms": latency_ms,
                    },
                )
                return []
        except Exception as e:
            logger.error(
                "Error fetching Prometheus rules",
                extra={
                    "req_id": SYSTEM_REQ_ID,
                    "method": "GET",
                    "error": str(e),
                },
            )
            return []

    def _flatten_rules(self, groups: list[JSONObject]) -> list[JSONObject]:
        """Flatten rule groups into a list of individual rules."""
        rules = []
        for group in groups:
            group_name = group.get("name", "")
            group_file = _optional_string(group.get("file"))
            group_interval = _optional_string(group.get("interval"))

            for rule in group.get("rules", []):
                if rule.get("type") == "alerting":
                    rules.append(
                        {
                            "group": group_name,
                            "file": group_file,
                            "interval": group_interval,
                            "name": rule.get("name", ""),
                            "query": rule.get("query", ""),
                            "duration": _optional_string(rule.get("duration")),
                            "labels": rule.get("labels", {}),
                            "annotations": rule.get("annotations", {}),
                            "state": _optional_string(rule.get("state")),
                            "health": _optional_string(rule.get("health")),
                        }
                    )
        return rules

    async def get_rule_groups(self) -> list[JSONObject]:
        """Get all rule groups with their full structure."""
        try:
            start_time = time.time()
            response = await self._request("GET", "/api/v1/rules", timeout=30)
            latency_ms = int((time.time() - start_time) * 1000)

            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    return data.get("data", {}).get("groups", [])
                else:
                    logger.error(
                        "Prometheus API returned error",
                        extra={
                            "req_id": SYSTEM_REQ_ID,
                            "method": "GET",
                            "status_code": response.status_code,
                            "latency_ms": latency_ms,
                            "error": data.get("error"),
                        },
                    )
                    return []
            else:
                logger.error(
                    "Failed to fetch Prometheus rule groups",
                    extra={
                        "req_id": SYSTEM_REQ_ID,
                        "method": "GET",
                        "status_code": response.status_code,
                        "latency_ms": latency_ms,
                    },
                )
                return []
        except Exception as e:
            logger.error(
                "Error fetching Prometheus rule groups",
                extra={
                    "req_id": SYSTEM_REQ_ID,
                    "method": "GET",
                    "error": str(e),
                },
            )
            return []

    async def get_alerts(self) -> list[JSONObject] | None:
        """Fetch currently active alerts from Prometheus."""
        try:
            start_time = time.time()
            response = await self._request("GET", "/api/v1/alerts", timeout=30)
            latency_ms = int((time.time() - start_time) * 1000)

            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    alerts = data.get("data", {}).get("alerts", [])
                    return alerts if isinstance(alerts, list) else []
                logger.error(
                    "Prometheus alerts API returned error",
                    extra={
                        "req_id": SYSTEM_REQ_ID,
                        "method": "GET",
                        "status_code": response.status_code,
                        "latency_ms": latency_ms,
                        "error": data.get("error"),
                    },
                )
                return None

            logger.error(
                "Failed to fetch Prometheus alerts",
                extra={
                    "req_id": SYSTEM_REQ_ID,
                    "method": "GET",
                    "status_code": response.status_code,
                    "latency_ms": latency_ms,
                },
            )
            return None
        except Exception as e:
            logger.error(
                "Error fetching Prometheus alerts",
                extra={"req_id": SYSTEM_REQ_ID, "method": "GET", "error": str(e)},
            )
            return None

    async def health_check(self) -> JSONObject:
        """Check if Prometheus is reachable."""
        try:
            with silence_httpx():
                start_time = time.time()
                response = await self._request("GET", "/-/healthy", timeout=10, retries=0)
                latency_ms = int((time.time() - start_time) * 1000)
                return {
                    "status": (
                        PLUGIN_RUN_STATE_HEALTHY
                        if response.status_code == 200
                        else _prometheus_unhealthy_status(response.status_code)
                    ),
                    "url": self.base_url,
                    "status_code": response.status_code,
                    "latency_ms": latency_ms,
                }
        except Exception as e:
            return {
                "status": PLUGIN_RUN_STATE_DEGRADED,
                "url": self.base_url,
                "error": str(e),
            }

    async def reload_config(self) -> JSONObject:
        """Reload Prometheus configuration.

        Note: Requires Prometheus to be started with --web.enable-lifecycle flag.
        """
        settings = get_settings()

        if not settings.prometheus_reload_enabled:
            return {
                "status": "disabled",
                "message": "Prometheus reload is not enabled in settings",
            }

        try:
            reload_url = (
                settings.prometheus_reload_url
                if settings.prometheus_reload_url
                else f"{self.base_url}/-/reload"
            )

            start_time = time.time()
            response = await self._request("POST", reload_url, timeout=30)
            latency_ms = int((time.time() - start_time) * 1000)

            if response.status_code == 200:
                logger.info(
                    "Prometheus configuration reloaded successfully",
                    extra={
                        "req_id": SYSTEM_REQ_ID,
                        "method": "POST",
                        "status_code": response.status_code,
                        "latency_ms": latency_ms,
                    },
                )
                return {
                    "status": "success",
                    "message": "Prometheus configuration reloaded",
                }
            else:
                logger.error(
                    "Failed to reload Prometheus",
                    extra={
                        "req_id": SYSTEM_REQ_ID,
                        "method": "POST",
                        "status_code": response.status_code,
                        "latency_ms": latency_ms,
                        "error": response.text,
                    },
                )
                return {
                    "status": "error",
                    "message": f"Failed to reload: {response.status_code}",
                    "detail": response.text,
                }
        except Exception as e:
            logger.error(
                "Error reloading Prometheus",
                extra={
                    "req_id": SYSTEM_REQ_ID,
                    "method": "POST",
                    "error": str(e),
                },
            )
            return {
                "status": "error",
                "message": str(e),
            }

    async def query(self, query: str, *, time_value: str | None = None) -> JSONObject:
        """Run an instant PromQL query."""
        params: JSONObject = {"query": query}
        if time_value:
            params["time"] = time_value
        return await self._prometheus_api_get("/api/v1/query", params=params, timeout=30)

    async def range_query(
        self,
        query: str,
        *,
        start: str | None = None,
        end: str | None = None,
        step: str | int | None = None,
    ) -> JSONObject:
        """Run a range PromQL query."""
        now = datetime.now(timezone.utc)
        resolved_end = end or str(int(now.timestamp()))
        resolved_start = start or str(int((now - timedelta(hours=1)).timestamp()))
        resolved_step = str(step or "60s")
        return await self._prometheus_api_get(
            "/api/v1/query_range",
            params={
                "query": query,
                "start": resolved_start,
                "end": resolved_end,
                "step": resolved_step,
            },
            timeout=30,
        )

    async def alert_evidence(
        self,
        *,
        alert_name: str,
        labels: JSONObject | None = None,
        lookback_seconds: int = 3600,
        step_seconds: int = 60,
    ) -> JSONObject:
        """Collect current and recent Prometheus evidence for one alert."""
        query = _alert_evidence_query(alert_name=alert_name, labels=labels)
        now = datetime.now(timezone.utc)
        end = str(int(now.timestamp()))
        start = str(int((now - timedelta(seconds=max(60, lookback_seconds))).timestamp()))
        step = f"{max(15, step_seconds)}s"
        current = await self.query(query)
        trend = await self.range_query(query, start=start, end=end, step=step)
        return {
            "alert_name": alert_name,
            "query": query,
            "labels": labels or {},
            "current": current,
            "trend": trend,
            "lookback_seconds": lookback_seconds,
            "step_seconds": step_seconds,
        }

    async def _prometheus_api_get(
        self,
        path: str,
        *,
        params: JSONObject,
        timeout: int,
    ) -> JSONObject:
        try:
            start_time = time.time()
            response = await self._request("GET", path, params=params, timeout=timeout)
            latency_ms = int((time.time() - start_time) * 1000)
            payload = response.json() if hasattr(response, "json") else {}
            if response.status_code == 200 and payload.get("status") == "success":
                return {
                    "success": True,
                    "status": "succeeded",
                    "data": payload.get("data"),
                    "latency_ms": latency_ms,
                }
            return {
                "success": False,
                "status": "failed",
                "status_code": response.status_code,
                "message": payload.get("error") or getattr(response, "text", ""),
                "error_type": payload.get("errorType"),
                "latency_ms": latency_ms,
            }
        except Exception as e:
            logger.error(
                "Error querying Prometheus",
                extra={"req_id": SYSTEM_REQ_ID, "method": "GET", "path": path, "error": str(e)},
            )
            return {"success": False, "status": "errored", "message": str(e)}

    async def get_metric_names(self) -> list[str]:
        """Fetch all available metric names from Prometheus."""
        try:
            start_time = time.time()
            response = await self._request(
                "GET",
                "/api/v1/label/__name__/values",
                timeout=30,
            )
            latency_ms = int((time.time() - start_time) * 1000)

            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    return data.get("data", [])
                else:
                    logger.error(
                        "Prometheus API returned error",
                        extra={
                            "req_id": SYSTEM_REQ_ID,
                            "method": "GET",
                            "status_code": response.status_code,
                            "latency_ms": latency_ms,
                            "error": data.get("error"),
                        },
                    )
                    return []
            else:
                logger.error(
                    "Failed to fetch metric names",
                    extra={
                        "req_id": SYSTEM_REQ_ID,
                        "method": "GET",
                        "status_code": response.status_code,
                        "latency_ms": latency_ms,
                    },
                )
                return []
        except Exception as e:
            logger.error(
                "Error fetching metric names",
                extra={"req_id": SYSTEM_REQ_ID, "method": "GET", "error": str(e)},
            )
            return []

    async def get_label_names(self, metric: str | None = None) -> list[str]:
        """Fetch all available label names from Prometheus.

        Args:
            metric: Optional metric name to get labels for a specific metric
        """
        try:
            if metric:
                start_time = time.time()
                response = await self._request(
                    "GET",
                    "/api/v1/label/__name__/values",
                    params={"match[]": metric},
                    timeout=30,
                )
                latency_ms = int((time.time() - start_time) * 1000)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") == "success":
                        labels = data.get("data", [])
                        return [label for label in labels if label != "__name__"]
            else:
                start_time = time.time()
                response = await self._request("GET", "/api/v1/labels", timeout=30)
                latency_ms = int((time.time() - start_time) * 1000)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") == "success":
                        labels = data.get("data", [])
                        return [label for label in labels if label != "__name__"]

            logger.error(
                "Failed to fetch label names",
                extra={
                    "req_id": SYSTEM_REQ_ID,
                    "method": "GET",
                    "status_code": response.status_code,
                    "latency_ms": latency_ms,
                },
            )
            return []
        except Exception as e:
            logger.error(
                "Error fetching label names",
                extra={"req_id": SYSTEM_REQ_ID, "method": "GET", "error": str(e)},
            )
            return []

    async def get_label_values(
        self,
        label_name: str,
        metric: str | None = None,
    ) -> list[str]:
        """Fetch all available values for a specific label.

        Args:
            label_name: The label name to get values for
            metric: Optional metric name to filter values
        """
        try:
            if metric:
                start_time = time.time()
                response = await self._request(
                    "GET",
                    "/api/v1/series",
                    params={"match[]": metric},
                    timeout=30,
                )
                latency_ms = int((time.time() - start_time) * 1000)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") == "success":
                        values = set()
                        for series in data.get("data", []):
                            if label_name in series:
                                values.add(series[label_name])
                        return sorted(list(values))
            else:
                start_time = time.time()
                response = await self._request(
                    "GET",
                    f"/api/v1/label/{label_name}/values",
                    timeout=30,
                )
                latency_ms = int((time.time() - start_time) * 1000)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") == "success":
                        return data.get("data", [])

            logger.error(
                "Failed to fetch label values",
                extra={
                    "req_id": SYSTEM_REQ_ID,
                    "method": "GET",
                    "status_code": response.status_code,
                    "latency_ms": latency_ms,
                    "label_name": label_name,
                },
            )
            return []
        except Exception as e:
            logger.error(
                "Error fetching label values",
                extra={
                    "req_id": SYSTEM_REQ_ID,
                    "method": "GET",
                    "label_name": label_name,
                    "error": str(e),
                },
            )
            return []


# Global client instance
_prometheus_client: PrometheusClient | None = None


def get_prometheus_client() -> PrometheusClient:
    """Get the global Prometheus client instance."""
    global _prometheus_client
    if _prometheus_client is None:
        _prometheus_client = PrometheusClient()
    return _prometheus_client
