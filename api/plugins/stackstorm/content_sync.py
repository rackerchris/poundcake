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


def _pack_actions_roots() -> list[Path]:
    roots: list[Path] = []
    configured_root = os.getenv("POUNDCAKE_STACKSTORM_PACK_ROOT", "").strip()
    if configured_root:
        roots.append(Path(configured_root).expanduser())
    sibling_repo_root = (
        STACKSTORM_PLUGIN_ROOT.parent.parent.parent.parent / "poundcake-stackstorm" / "packs" / "poundcake"
    )
    roots.append(sibling_repo_root)
    roots.append(STACKSTORM_CONTENT_ROOT)

    action_roots: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        candidate = root / "actions" if root.name != "actions" else root
        resolved = str(candidate)
        if resolved in seen or not candidate.is_dir():
            continue
        seen.add(resolved)
        action_roots.append(candidate)
    return action_roots


def _pack_profile_candidates() -> list[Path]:
    candidates: list[Path] = []
    configured = os.getenv("POUNDCAKE_STACKSTORM_PROFILE_PATH", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    configured_root = os.getenv("POUNDCAKE_STACKSTORM_PACK_ROOT", "").strip()
    if configured_root:
        candidates.append(Path(configured_root).expanduser() / "poundcake_profiles.json")
    candidates.extend(
        [
            STACKSTORM_PLUGIN_ROOT.parent.parent.parent.parent
            / "poundcake-stackstorm"
            / "packs"
            / "poundcake"
            / "poundcake_profiles.json",
            Path("/app/config/poundcake_profiles.json"),
        ]
    )
    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        rendered = str(candidate)
        if rendered in seen:
            continue
        seen.add(rendered)
        deduped.append(candidate)
    return deduped


def load_stackstorm_action_definitions(pack_name: str | None = None) -> list[JSONObject]:
    """Load PoundCake-authored StackStorm action metadata from plugin content."""
    resolved_pack_name = pack_name or _content_pack_name()
    actions: list[JSONObject] = []
    seen_names: set[str] = set()
    for actions_dir in _pack_actions_roots():
        for path in sorted(actions_dir.glob("*.yaml")):
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(payload, dict):
                raise StackStormError(f"Invalid StackStorm action metadata: {path.name}")
            action = dict(payload)
            action["pack"] = str(action.get("pack") or resolved_pack_name)
            action_name = str(action.get("name") or "").strip()
            if not action_name or action_name in seen_names:
                continue
            seen_names.add(action_name)
            actions.append(action)
    return actions


def load_stackstorm_profile_metadata() -> JSONObject:
    """Load StackStorm profile metadata when a profile file is available."""
    for candidate in _pack_profile_candidates():
        if not candidate.is_file():
            continue
        payload = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise StackStormError(f"Invalid StackStorm profile metadata: {candidate}")
        return payload
    return {}


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
