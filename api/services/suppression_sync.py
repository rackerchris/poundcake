"""Helpers for applying normalized suppression data from service plugins."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.time import align_datetime_pair
from api.models.models import AlertSuppression, AlertSuppressionMatcher
from api.types import JSONObject


def _parse_time(value: object, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return fallback
    return fallback


async def upsert_plugin_suppressions(
    db: AsyncSession,
    *,
    service_type: str,
    suppressions: list[JSONObject],
    synced_at: datetime | None = None,
) -> JSONObject:
    """Upsert plugin-owned suppressions without making ingestion call external APIs."""
    now = synced_at or datetime.now(timezone.utc)
    created = 0
    updated = 0
    matcher_count = 0
    normalized_service = service_type.strip().lower()

    for item in suppressions:
        source_ref = str(item.get("source_ref") or item.get("id") or "").strip()
        if not source_ref:
            continue

        result = await db.execute(
            select(AlertSuppression).where(
                AlertSuppression.source_service_type == normalized_service,
                AlertSuppression.source_ref == source_ref,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = AlertSuppression(
                name=str(item.get("name") or f"{normalized_service}:{source_ref}"),
                starts_at=now,
                ends_at=now,
                source="plugin",
                source_service_type=normalized_service,
                source_ref=source_ref,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.flush()
            created += 1
        else:
            updated += 1

        status = str(item.get("status") or "active").strip().lower()
        starts_at = _parse_time(item.get("starts_at"), now)
        ends_at = _parse_time(item.get("ends_at"), starts_at)
        row.name = str(item.get("name") or row.name)
        row.reason = str(item.get("reason") or "") or None
        row.scope = "matchers"
        compare_ends_at, compare_now = align_datetime_pair(ends_at, now)
        row.enabled = status in {"active", "pending"} and compare_ends_at > compare_now
        row.starts_at = starts_at
        row.ends_at = ends_at
        row.canceled_at = now if status == "expired" else None
        row.created_by = str(item.get("created_by") or "") or None
        if "summary_ticket_enabled" in item:
            row.summary_ticket_enabled = bool(item.get("summary_ticket_enabled"))
        row.source = "plugin"
        row.source_service_type = normalized_service
        row.source_ref = source_ref
        row.source_payload = item
        row.last_synced_at = now
        row.updated_at = now

        await db.execute(
            delete(AlertSuppressionMatcher).where(AlertSuppressionMatcher.suppression_id == row.id)
        )
        for matcher in item.get("matchers") or []:
            if not isinstance(matcher, dict):
                continue
            label_key = str(matcher.get("label_key") or "").strip()
            if not label_key:
                continue
            db.add(
                AlertSuppressionMatcher(
                    suppression_id=row.id,
                    label_key=label_key,
                    operator=str(matcher.get("operator") or "eq").strip().lower(),
                    value=str(matcher.get("value") or ""),
                    created_at=now,
                )
            )
            matcher_count += 1

    return {
        "success": True,
        "service_type": normalized_service,
        "created": created,
        "updated": updated,
        "matchers": matcher_count,
        "synced_at": now.isoformat(),
    }
