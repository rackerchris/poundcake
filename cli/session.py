"""Local session persistence for the PoundCake CLI."""

from __future__ import annotations

from shared.types import JSONObject

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _config_dir() -> Path:
    root = os.getenv("XDG_CONFIG_HOME", "").strip()
    if root:
        return Path(root).expanduser()
    return Path.home() / ".config"


def _parse_expires_at(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass
class StoredSession:
    """Serialized session metadata stored on disk."""

    session_id: str
    username: str
    expires_at: str
    provider: str = "local"
    role: str = "reader"
    display_name: str | None = None
    is_superuser: bool = False
    permissions: list[str] | None = None

    @classmethod
    def from_dict(cls, data: JSONObject) -> "StoredSession":
        return cls(
            session_id=str(data["session_id"]),
            username=str(data["username"]),
            expires_at=str(data["expires_at"]),
            provider=str(data.get("provider") or "local"),
            role=str(data.get("role") or "reader"),
            display_name=(
                None if data.get("display_name") is None else str(data.get("display_name"))
            ),
            is_superuser=bool(data.get("is_superuser")),
            permissions=[str(item) for item in data.get("permissions") or []] or None,
        )

    def is_expired(self) -> bool:
        return _parse_expires_at(self.expires_at) <= datetime.now(timezone.utc)

    def to_dict(self) -> JSONObject:
        return asdict(self)


class SessionStore:
    """Manage CLI sessions keyed by normalized API base URL."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (_config_dir() / "poundcake" / "session.json")

    def get(self, base_url: str) -> StoredSession | None:
        sessions = self._load()
        key = _normalize_base_url(base_url)
        raw = sessions.get(key)
        if not isinstance(raw, dict):
            return None
        session = StoredSession.from_dict(raw)
        if session.is_expired():
            self.delete(base_url)
            return None
        return session

    def save(self, base_url: str, session: StoredSession) -> None:
        sessions = self._load()
        sessions[_normalize_base_url(base_url)] = session.to_dict()
        self._write(sessions)

    def delete(self, base_url: str) -> None:
        sessions = self._load()
        key = _normalize_base_url(base_url)
        if key not in sessions:
            return
        sessions.pop(key, None)
        self._write(sessions)

    def _load(self) -> JSONObject:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _write(self, sessions: JSONObject) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(sessions, indent=2, sort_keys=True)
        self.path.write_text(payload + "\n", encoding="utf-8")
        os.chmod(self.path, 0o600)
