#!/usr/bin/env python3
#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""Prep Chef: Polls for dispatchable orders and triggers unified order dispatch."""

import os
import time

from api.core.logging import setup_logging, get_logger
from api.core.config import get_settings
import kitchen.service_helpers as service_helpers

# Initialize logging with standardized configuration
setup_logging()
logger = get_logger(__name__)

POUNDCAKE_API_URL = os.getenv("POUNDCAKE_API_URL", "http://poundcake:8080").rstrip("/")
API_URL = f"{POUNDCAKE_API_URL}/api/v1"
PREP_INTERVAL = int(os.getenv("PREP_INTERVAL", "5"))

# System request ID for prep chef operations
SYSTEM_REQ_ID = "SYSTEM-PREP-CHEF"
POLLER_RETRIES = get_settings().poller_http_retries
POLL_LIMIT = int(os.getenv("PREP_CHEF_LIMIT", "10"))


def prep_loop() -> None:
    """Main prep chef loop - polls for new orders and triggers cooking."""
    service_helpers.wait_for_api(API_URL, SYSTEM_REQ_ID, logger, delay_sec=PREP_INTERVAL)
    logger.info(
        "Starting prep chef",
        extra={"req_id": SYSTEM_REQ_ID, "api_url": API_URL, "poll_interval": PREP_INTERVAL},
    )
    api_unavailable_since: float | None = None

    while True:
        runtime_config = service_helpers.get_worker_runtime_config(
            api_base_url=API_URL,
            service_type="prep-chef",
            req_id=SYSTEM_REQ_ID,
            default_interval=PREP_INTERVAL,
            default_query_limit=POLL_LIMIT,
            logger=logger,
        )
        loop_interval = int(runtime_config["run_interval_seconds"])
        loop_limit = int(runtime_config["query_limit"])
        if not runtime_config["enabled"]:
            logger.info(
                "Prep chef paused by internal plugin configuration",
                extra={"req_id": SYSTEM_REQ_ID, "poll_interval": loop_interval},
            )
            time.sleep(loop_interval)
            continue
        try:
            start_time = time.time()
            resp = service_helpers.request_control_plane_sync(
                "GET",
                f"{API_URL}/orders",
                params={"processing_status": "new", "limit": loop_limit},
                req_id=SYSTEM_REQ_ID,
                timeout=10,
                retries=POLLER_RETRIES,
            )
            latency_ms = int((time.time() - start_time) * 1000)
            if resp.status_code != 200:
                logger.error(
                    "Failed to fetch orders",
                    extra={
                        "req_id": SYSTEM_REQ_ID,
                        "method": "GET",
                        "status": "new",
                        "status_code": resp.status_code,
                        "latency_ms": latency_ms,
                    },
                )
                orders = []
            else:
                fetched = resp.json()
                orders = fetched if isinstance(fetched, list) else []

            if api_unavailable_since is not None:
                downtime_sec = int(time.time() - api_unavailable_since)
                logger.info(
                    "Prep chef API connectivity restored",
                    extra={"req_id": SYSTEM_REQ_ID, "downtime_sec": downtime_sec},
                )
                api_unavailable_since = None

            for order in orders:
                req_id = order.get("req_id", "UNKNOWN")
                order_id = order.get("id")
                processing_status = order.get("processing_status")

                # Pass the original Request ID to the next hop
                logger.info(
                    "Preparing order for cooking",
                    extra={"req_id": req_id, "order_id": order_id},
                )

                start_time = time.time()
                cook_resp = service_helpers.request_control_plane_sync(
                    "POST",
                    f"{API_URL}/cook/orders/{order_id}",
                    req_id=req_id,
                    timeout=15,
                    retries=POLLER_RETRIES,
                )
                latency_ms = int((time.time() - start_time) * 1000)

                if cook_resp.status_code in [200, 201]:
                    cook_status = "unknown"
                    try:
                        cook_status = cook_resp.json().get("status", "unknown")
                    except Exception:
                        cook_status = "unknown"
                    logger.info(
                        "Order prepared",
                        extra={
                            "req_id": req_id,
                            "order_id": order_id,
                            "processing_status": processing_status,
                            "cook_status": cook_status,
                            "method": "POST",
                            "status_code": cook_resp.status_code,
                            "latency_ms": latency_ms,
                        },
                    )
                else:
                    logger.error(
                        "Cook preparation failed",
                        extra={
                            "req_id": req_id,
                            "order_id": order_id,
                            "processing_status": processing_status,
                            "method": "POST",
                            "status_code": cook_resp.status_code,
                            "latency_ms": latency_ms,
                            "response": cook_resp.text,
                        },
                    )

        except Exception as e:
            if api_unavailable_since is None:
                api_unavailable_since = time.time()
                logger.error(
                    "Prep chef lost API connectivity",
                    extra={"req_id": SYSTEM_REQ_ID, "error": str(e)},
                )
            else:
                logger.debug(
                    "Prep chef waiting for API recovery",
                    extra={"req_id": SYSTEM_REQ_ID, "error": str(e)},
                )

        time.sleep(loop_interval)


if __name__ == "__main__":
    prep_loop()
