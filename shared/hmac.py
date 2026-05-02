"""Shared HMAC/canonical payload helpers."""

from __future__ import annotations

from shared.types import JSONObject

import hashlib
import hmac
import json
from typing import Any


def canonical_json_body(payload: JSONObject | None) -> str:
    """Serialize JSON payload into a stable canonical representation."""
    if not payload:
        return ""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def sha256_hex(data: bytes) -> str:
    """Compute SHA-256 hex digest for bytes."""
    return hashlib.sha256(data).hexdigest()


def build_hmac_signing_payload(timestamp: str, method: str, path: str, body: bytes) -> str:
    """Build canonical payload string used by HMAC signing."""
    body_hash = sha256_hex(body)
    return f"{timestamp}\n{method.upper()}\n{path}\n{body_hash}"


def hmac_sha256_hex(secret: str, payload: str) -> str:
    """Compute HMAC-SHA256 hex digest for payload using shared secret."""
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
