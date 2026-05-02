"""UTC timestamp helpers for database and API runtime code."""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp for API/runtime calculations."""
    return datetime.now(timezone.utc)


def utc_now_db() -> datetime:
    """Return a UTC timestamp suitable for MySQL/MariaDB DATETIME storage."""
    return utc_now().replace(tzinfo=None)


def align_datetime_pair(left: datetime, right: datetime) -> tuple[datetime, datetime]:
    """Align mixed naive/aware datetimes for safe arithmetic.

    MariaDB DATETIME values are returned as naive values. PoundCake treats those
    values as UTC, so arithmetic can safely drop tzinfo when one side is naive.
    """
    if left.tzinfo is None and right.tzinfo is not None:
        right = right.replace(tzinfo=None)
    elif left.tzinfo is not None and right.tzinfo is None:
        left = left.replace(tzinfo=None)
    return left, right


def utc_runtime_seconds(started_at: datetime | None, completed_at: datetime | None) -> int | None:
    """Return non-negative runtime seconds for UTC timestamps."""
    if started_at is None or completed_at is None:
        return None
    started_at, completed_at = align_datetime_pair(started_at, completed_at)
    return max(0, int((completed_at - started_at).total_seconds()))
