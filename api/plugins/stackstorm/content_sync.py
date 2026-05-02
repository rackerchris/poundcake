"""StackStorm content synchronization helpers."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import yaml

from api.plugins.stackstorm.service import StackStormActionManager, StackStormError
from api.types import JSONObject

STACKSTORM_PLUGIN_ROOT = Path(__file__).resolve().parent
STACKSTORM_CONTENT_ROOT = STACKSTORM_PLUGIN_ROOT / "content"


def _content_pack_name() -> str:
    return os.getenv("POUNDCAKE_ST2_PACK", "poundcake")


def load_stackstorm_action_definitions(pack_name: str | None = None) -> list[JSONObject]:
    """Load PoundCake-authored StackStorm action metadata from plugin content."""
    resolved_pack_name = pack_name or _content_pack_name()
    actions_dir = STACKSTORM_CONTENT_ROOT / "actions"
    if not actions_dir.is_dir():
        return []
    actions: list[JSONObject] = []
    for path in sorted(actions_dir.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise StackStormError(f"Invalid StackStorm action metadata: {path.name}")
        action = dict(payload)
        action["pack"] = str(action.get("pack") or resolved_pack_name)
        actions.append(action)
    return actions


def stackstorm_content_hash(pack_name: str | None = None) -> str:
    digest = hashlib.sha256()
    for action in load_stackstorm_action_definitions(pack_name=pack_name):
        digest.update(yaml.safe_dump(action, sort_keys=True).encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


async def sync_stackstorm_content(
    manager: StackStormActionManager,
    *,
    force: bool = False,
    pack_name: str | None = None,
) -> JSONObject:
    """Sync PoundCake-owned StackStorm action metadata during content_sync execution."""
    actions = load_stackstorm_action_definitions(pack_name=pack_name)
    sync_result = await manager.sync_action_definitions(actions)
    return {
        "content_hash": stackstorm_content_hash(pack_name=pack_name),
        "force": force,
        "actions": sync_result,
    }
