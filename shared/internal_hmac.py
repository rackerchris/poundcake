"""PoundCake internal control-plane HMAC helpers."""

from __future__ import annotations

import secrets
import time
from urllib.parse import urlsplit

from shared.hmac import build_hmac_signing_payload, hmac_sha256_hex

INTERNAL_HMAC_SCHEME = "HMAC"
INTERNAL_HMAC_KEY_ID_HEADER = "X-PoundCake-Internal-Key-ID"
INTERNAL_HMAC_TIMESTAMP_HEADER = "X-PoundCake-Timestamp"
INTERNAL_HMAC_NONCE_HEADER = "X-PoundCake-Nonce"


def canonical_request_path(url_or_path: str) -> str:
    """Return the path and query string used for internal request signing."""
    parsed = urlsplit(url_or_path)
    path = parsed.path or "/"
    if parsed.query:
        return f"{path}?{parsed.query}"
    return path


def build_internal_hmac_headers(
    *,
    key_id: str,
    secret: str,
    method: str,
    url_or_path: str,
    body: bytes = b"",
    timestamp: str | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """Build internal HMAC auth headers for one PoundCake control-plane request."""
    ts = timestamp or str(int(time.time()))
    request_nonce = nonce or secrets.token_urlsafe(24)
    path = canonical_request_path(url_or_path)
    signing_payload = build_internal_hmac_signing_payload(
        ts,
        method,
        path,
        body,
        nonce=request_nonce,
    )
    signature = hmac_sha256_hex(secret, signing_payload)
    return {
        "Authorization": f"{INTERNAL_HMAC_SCHEME} {key_id}:{signature}",
        INTERNAL_HMAC_KEY_ID_HEADER: key_id,
        INTERNAL_HMAC_TIMESTAMP_HEADER: ts,
        INTERNAL_HMAC_NONCE_HEADER: request_nonce,
    }


def build_internal_hmac_signing_payload(
    timestamp: str,
    method: str,
    path: str,
    body: bytes,
    *,
    nonce: str,
) -> str:
    """Build the signed payload for internal control-plane HMAC requests."""
    base_payload = build_hmac_signing_payload(timestamp, method, path, body)
    if not nonce:
        return base_payload
    return f"{base_payload}\n{nonce}"


def parse_internal_hmac_authorization(value: str) -> tuple[str, str] | None:
    """Parse an Authorization header containing a PoundCake internal HMAC signature."""
    scheme, _, credential = value.strip().partition(" ")
    if scheme.upper() != INTERNAL_HMAC_SCHEME or not credential:
        return None
    key_id, separator, signature = credential.rpartition(":")
    if not separator or not key_id.strip() or not signature.strip():
        return None
    return key_id.strip(), signature.strip()
