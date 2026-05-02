"""Shared encrypted credential payload helpers.

This module is crypto/plumbing only. Credential authority lives in
``api.services.credential_manager`` for adapter/provider credentials and in
``api.services.service_identity`` for internal service HMAC credentials.
"""

from __future__ import annotations

import base64
import hashlib
import json
from cryptography.fernet import Fernet
import os

from api.types import JSONObject

INTERNAL_CONTROL_PLANE_HMAC_CREDENTIAL_TYPE = "internal_control_plane_hmac"
CREDENTIAL_MANAGER_SERVICE_TYPE = "credential-manager"


class ServicePluginCredentialError(RuntimeError):
    """Raised when service plugin credential storage is unavailable."""


def _encryption_key() -> str:
    value = os.getenv("POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not value:
        raise ServicePluginCredentialError(
            "POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY is required for plugin credentials"
        )
    return value


def _service_identity_encryption_key() -> str:
    value = os.getenv("POUNDCAKE_SERVICE_IDENTITY_CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not value:
        raise ServicePluginCredentialError(
            "POUNDCAKE_SERVICE_IDENTITY_CREDENTIAL_ENCRYPTION_KEY is required "
            "for service identity credentials"
        )
    return value


def _fernet_for_key(key: str) -> Fernet:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _fernet() -> Fernet:
    return _fernet_for_key(_encryption_key())


def encrypt_payload(payload: JSONObject) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _fernet().encrypt(encoded).decode("utf-8")


def decrypt_payload(value: str) -> JSONObject:
    decoded = _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    payload = json.loads(decoded)
    if not isinstance(payload, dict):
        raise ServicePluginCredentialError("plugin credential payload must decrypt to an object")
    return payload


def encrypt_service_identity_payload(payload: JSONObject) -> str:
    """Encrypt internal service-identity credential material with its own key domain."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _fernet_for_key(_service_identity_encryption_key()).encrypt(encoded).decode("utf-8")


def decrypt_service_identity_payload(value: str) -> JSONObject:
    """Decrypt service-identity material with the service identity key domain."""
    decoded = (
        _fernet_for_key(_service_identity_encryption_key())
        .decrypt(value.encode("utf-8"))
        .decode("utf-8")
    )
    payload = json.loads(decoded)
    if not isinstance(payload, dict):
        raise ServicePluginCredentialError(
            "service identity credential payload must decrypt to an object"
        )
    return payload
