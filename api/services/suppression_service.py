"""Alert suppression matching, persistence, and summary lifecycle."""

from __future__ import annotations

from api.types import JSONObject

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.core.config import get_settings
from api.core.logging import get_logger
from api.core.metrics import (
    record_suppressed_event as metric_record_suppressed_event,
    record_suppression_summary_failure,
    set_active_suppressions,
)
from api.models.models import (
    AlertSuppression,
    AlertSuppressionMatcher,
    Order,
    SuppressedEvent,
    SuppressionSummary,
)

logger = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def suppression_status(suppression: AlertSuppression, now: datetime | None = None) -> str:
    if normalize_utc_datetime(suppression.canceled_at):
        return "canceled"
    current = normalize_utc_datetime(now or _utc_now())
    starts_at = normalize_utc_datetime(suppression.starts_at)
    ends_at = normalize_utc_datetime(suppression.ends_at)
    if current is None or starts_at is None or ends_at is None:
        return "active"
    if current < starts_at:
        return "scheduled"
    if current > ends_at:
        return "expired"
    return "active"


def _safe_str(value: Any) -> str:
    return "" if value is None else str(value)


def _matcher_match(matcher: AlertSuppressionMatcher, labels: JSONObject) -> bool:
    key = matcher.label_key
    operator = matcher.operator
    expected = matcher.value
    present = key in labels
    actual = _safe_str(labels.get(key))

    if operator == "exists":
        return present
    if operator == "not_exists":
        return not present
    if not present:
        return False
    if operator == "eq":
        return actual == _safe_str(expected)
    if operator == "neq":
        return actual != _safe_str(expected)
    if operator == "regex":
        if expected is None:
            return False
        return re.search(expected, actual) is not None
    if operator == "nregex":
        if expected is None:
            return True
        return re.search(expected, actual) is None
    return False


def suppression_matches(suppression: AlertSuppression, labels: JSONObject) -> bool:
    if suppression.scope == "all":
        return True
    if suppression.scope != "matchers":
        return False
    if not suppression.matchers:
        return False
    return all(_matcher_match(matcher, labels) for matcher in suppression.matchers)


def _payload_hash(alert_data: JSONObject, req_id: str) -> str:
    canonical = json.dumps(alert_data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{req_id}:{canonical}".encode("utf-8")).hexdigest()


async def find_first_matching_suppression(
    db: AsyncSession,
    labels: JSONObject,
    received_at: datetime | None = None,
) -> AlertSuppression | None:
    now = received_at or _utc_now()
    result = await db.execute(
        select(AlertSuppression)
        .options(selectinload(AlertSuppression.matchers))
        .where(
            AlertSuppression.enabled.is_(True),
            AlertSuppression.canceled_at.is_(None),
            AlertSuppression.starts_at <= now,
            AlertSuppression.ends_at >= now,
        )
        .order_by(AlertSuppression.created_at.asc(), AlertSuppression.id.asc())
    )
    candidates = result.scalars().all()
    set_active_suppressions(len(candidates))
    for suppression in candidates:
        if suppression_matches(suppression, labels):
            return suppression
    return None


async def save_suppressed_event(
    db: AsyncSession,
    suppression: AlertSuppression,
    alert_data: JSONObject,
    req_id: str,
    received_at: datetime | None = None,
) -> SuppressedEvent:
    received = received_at or _utc_now()
    labels = alert_data.get("labels", {}) or {}
    payload_hash = _payload_hash(alert_data, req_id)

    result = await db.execute(
        select(SuppressedEvent).where(
            SuppressedEvent.suppression_id == suppression.id,
            SuppressedEvent.payload_hash == payload_hash,
        )
    )
    existing = result.scalars().first()
    if existing:
        return existing

    event = SuppressedEvent(
        suppression_id=suppression.id,
        received_at=received,
        fingerprint=alert_data.get("fingerprint"),
        alertname=labels.get("alertname", "Unknown"),
        severity=labels.get("severity", "unknown"),
        labels_json=labels,
        annotations_json=alert_data.get("annotations", {}) or {},
        status=alert_data.get("status", "firing"),
        payload_hash=payload_hash,
        req_id=req_id,
    )
    db.add(event)
    await db.flush()
    record_suppressed_event_metric(
        suppression_id=suppression.id,
        alertname=event.alertname or "Unknown",
        severity=event.severity or "unknown",
    )
    return event


def record_suppressed_event_metric(suppression_id: int, alertname: str, severity: str) -> None:
    metric_record_suppressed_event(suppression_id, alertname, severity)


def build_summary_ticket_payload(
    suppression: AlertSuppression,
    summary: SuppressionSummary,
) -> JSONObject:
    by_alertname = summary.by_alertname_json or {}
    by_severity = summary.by_severity_json or {}
    starts_at = normalize_utc_datetime(suppression.starts_at)
    ends_at = normalize_utc_datetime(suppression.ends_at)
    starts = starts_at.isoformat() if starts_at else "N/A"
    ends = ends_at.isoformat() if ends_at else "N/A"
    matcher_snapshot = [
        {
            "label_key": m.label_key,
            "operator": m.operator,
            "value": m.value,
        }
        for m in suppression.matchers
    ]

    description = (
        f"Suppression Name: {suppression.name}\n"
        f"Reason: {suppression.reason or 'N/A'}\n"
        f"Window: {starts} - {ends}\n"
        f"Scope: {suppression.scope}\n"
        f"Matcher Snapshot: {json.dumps(matcher_snapshot)}\n"
        f"Total Suppressed: {summary.total_suppressed}\n"
        f"Cleared During Suppression: {summary.total_cleared}\n"
        f"Still Firing At End: {summary.total_still_firing}\n"
        f"By Alertname: {json.dumps(by_alertname)}\n"
        f"By Severity: {json.dumps(by_severity)}\n"
        f"Still Firing Alerts: {json.dumps(summary.still_firing_alerts_json or {})}\n"
        f"First Seen: {summary.first_seen_at.isoformat() if summary.first_seen_at else 'N/A'}\n"
        f"Last Seen: {summary.last_seen_at.isoformat() if summary.last_seen_at else 'N/A'}"
    )
    return {
        "title": f"[PoundCake Suppression Summary] {suppression.name} ({starts} - {ends})",
        "description": description,
        "severity": "info",
        "source": "poundcake_system",
        "context": {
            "source": "poundcake_system",
            "suppression_id": suppression.id,
            "scope": suppression.scope,
            "total_suppressed": summary.total_suppressed,
            "total_cleared": summary.total_cleared,
            "total_still_firing": summary.total_still_firing,
        },
    }


async def compute_suppression_stats(
    db: AsyncSession,
    suppression_id: int,
) -> JSONObject:
    result = await db.execute(
        select(SuppressedEvent).where(SuppressedEvent.suppression_id == suppression_id)
    )
    events = result.scalars().all()
    by_alertname = Counter((e.alertname or "Unknown") for e in events)
    by_severity = Counter((e.severity or "unknown") for e in events)
    first_seen = min((e.received_at for e in events), default=None)
    last_seen = max((e.received_at for e in events), default=None)
    latest_by_fingerprint: dict[str, SuppressedEvent] = {}
    for event in sorted(events, key=lambda item: item.received_at):
        if not event.fingerprint:
            continue
        latest_by_fingerprint[event.fingerprint] = event
    cleared = 0
    still_firing = 0
    still_firing_alerts: JSONObject = {}
    for fingerprint, event in latest_by_fingerprint.items():
        status = (event.status or "").lower()
        if status == "resolved":
            cleared += 1
            continue
        still_firing += 1
        still_firing_alerts[fingerprint] = {
            "alertname": event.alertname or "Unknown",
            "severity": event.severity or "unknown",
            "last_status": event.status,
            "last_received_at": event.received_at.isoformat(),
            "labels": event.labels_json or {},
            "annotations": event.annotations_json or {},
            "req_id": event.req_id,
        }
    return {
        "total_suppressed": len(events),
        "by_alertname": dict(by_alertname),
        "by_severity": dict(by_severity),
        "first_seen_at": first_seen,
        "last_seen_at": last_seen,
        "total_cleared": cleared,
        "total_still_firing": still_firing,
        "still_firing_alerts": still_firing_alerts,
        "latest_by_fingerprint": latest_by_fingerprint,
    }


async def _requeue_still_firing_events(
    db: AsyncSession,
    suppression_id: int,
    latest_by_fingerprint: dict[str, SuppressedEvent],
    req_id: str,
) -> int:
    created = 0
    for fingerprint, event in latest_by_fingerprint.items():
        if (event.status or "").lower() == "resolved":
            continue
        active_result = await db.execute(
            select(Order).where(Order.fingerprint == fingerprint, Order.is_active.is_(True))
        )
        if active_result.scalars().first():
            continue

        labels = event.labels_json or {}
        annotations = event.annotations_json or {}
        group_name = labels.get("group_name") or labels.get("alertname") or "Unknown"
        replay_order = Order(
            req_id=event.req_id or f"{req_id}-SUPPRESSION-REPLAY",
            fingerprint=fingerprint,
            alert_status="firing",
            processing_status="new",
            is_active=True,
            alert_group_name=group_name,
            severity=labels.get("severity", "unknown"),
            instance=labels.get("instance"),
            labels=labels,
            annotations=annotations,
            raw_data={
                "fingerprint": fingerprint,
                "status": "firing",
                "labels": labels,
                "annotations": annotations,
                "suppression_replay": True,
                "suppression_id": suppression_id,
            },
            counter=1,
            starts_at=event.received_at,
        )
        db.add(replay_order)
        created += 1
    if created > 0:
        await db.flush()
    return created


async def _get_or_create_summary(
    db: AsyncSession,
    suppression_id: int,
) -> SuppressionSummary:
    result = await db.execute(
        select(SuppressionSummary).where(SuppressionSummary.suppression_id == suppression_id)
    )
    summary = result.scalars().first()
    if summary:
        return summary
    summary = SuppressionSummary(suppression_id=suppression_id, state="pending")
    db.add(summary)
    await db.flush()
    return summary


async def finalize_expired_suppressions(db: AsyncSession, req_id: str) -> int:
    settings = get_settings()
    if not settings.suppressions_enabled or not settings.suppression_lifecycle_enabled:
        return 0

    now = _utc_now()
    result = await db.execute(
        select(AlertSuppression)
        .options(selectinload(AlertSuppression.matchers))
        .where(
            AlertSuppression.ends_at < now,
            AlertSuppression.canceled_at.is_(None),
        )
        .order_by(AlertSuppression.ends_at.asc())
        .limit(settings.suppression_lifecycle_batch_limit)
    )
    suppressions = result.scalars().all()
    finalized = 0

    for suppression in suppressions:
        summary = await _get_or_create_summary(db, suppression.id)
        if summary.state == "closed":
            continue

        try:
            stats = await compute_suppression_stats(db, suppression.id)
            summary.total_suppressed = stats["total_suppressed"]
            summary.total_cleared = stats["total_cleared"]
            summary.total_still_firing = stats["total_still_firing"]
            summary.by_alertname_json = stats["by_alertname"]
            summary.by_severity_json = stats["by_severity"]
            summary.still_firing_alerts_json = stats["still_firing_alerts"]
            summary.first_seen_at = stats["first_seen_at"]
            summary.last_seen_at = stats["last_seen_at"]
            summary.summary_created_at = now

            replayed_orders = await _requeue_still_firing_events(
                db=db,
                suppression_id=suppression.id,
                latest_by_fingerprint=stats["latest_by_fingerprint"],
                req_id=req_id,
            )

            if summary.total_suppressed == 0:
                summary.state = "closed"
                summary.summary_close_at = now
                await db.commit()
                finalized += 1
                continue

            summary.state = "closed"
            summary.summary_close_at = _utc_now()
            summary.last_error = None
            await db.commit()
            finalized += 1
            logger.info(
                "Suppression finalized",
                extra={
                    "req_id": req_id,
                    "suppression_id": suppression.id,
                    "replayed_orders": replayed_orders,
                    "total_still_firing": summary.total_still_firing,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Suppression summary lifecycle failed",
                extra={
                    "req_id": req_id,
                    "suppression_id": suppression.id,
                    "error": str(exc),
                },
            )
            summary.state = "failed"
            summary.last_error = str(exc)
            await db.commit()
            record_suppression_summary_failure()

    return finalized


async def count_active_suppressions(db: AsyncSession) -> int:
    now = _utc_now()
    result = await db.execute(
        select(func.count(AlertSuppression.id)).where(
            AlertSuppression.enabled.is_(True),
            AlertSuppression.canceled_at.is_(None),
            AlertSuppression.starts_at <= now,
            AlertSuppression.ends_at >= now,
        )
    )
    return int(result.scalar() or 0)


async def list_suppression_activity(
    db: AsyncSession,
    suppression_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[SuppressedEvent]:
    query = select(SuppressedEvent).order_by(SuppressedEvent.received_at.desc())
    if suppression_id:
        query = query.where(SuppressedEvent.suppression_id == suppression_id)
    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    return list(result.scalars().all())


async def list_suppressions(
    db: AsyncSession,
    status: str | None = None,
    enabled: bool | None = None,
    scope: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AlertSuppression]:
    now = _utc_now()
    query = (
        select(AlertSuppression)
        .options(selectinload(AlertSuppression.matchers))
        .order_by(AlertSuppression.starts_at.desc())
    )
    if enabled is not None:
        query = query.where(AlertSuppression.enabled.is_(enabled))
    if scope:
        query = query.where(AlertSuppression.scope == scope)

    if status == "scheduled":
        query = query.where(
            AlertSuppression.canceled_at.is_(None),
            AlertSuppression.starts_at > now,
        )
    elif status == "active":
        query = query.where(
            AlertSuppression.canceled_at.is_(None),
            AlertSuppression.starts_at <= now,
            AlertSuppression.ends_at >= now,
        )
    elif status == "expired":
        query = query.where(
            AlertSuppression.canceled_at.is_(None),
            AlertSuppression.ends_at < now,
        )
    elif status == "canceled":
        query = query.where(AlertSuppression.canceled_at.is_not(None))

    result = await db.execute(query.limit(limit).offset(offset))
    return list(result.scalars().all())


async def get_suppression(db: AsyncSession, suppression_id: int) -> AlertSuppression | None:
    result = await db.execute(
        select(AlertSuppression)
        .options(selectinload(AlertSuppression.matchers), selectinload(AlertSuppression.summary))
        .where(AlertSuppression.id == suppression_id)
    )
    return result.scalars().first()
