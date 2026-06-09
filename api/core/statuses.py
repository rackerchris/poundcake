#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""Shared status vocabularies for PoundCake runtime objects."""

EXECUTION_NON_TERMINAL_STATUSES = {"pending", "dispatched", "running"}
EXECUTION_TERMINAL_STATUSES = {"succeeded", "failed", "errored", "timeout", "canceled"}

ORDER_TERMINAL_PROCESSING_STATUSES = {"complete", "failed", "errored", "timeout", "canceled"}

ORDER_RESOLVING_TRANSITIONABLE_STATUSES = {
    "new",
    "processing",
    "resolving",
}


def normalize_status(status: str | None) -> str:
    """Normalize status values for safe comparisons."""
    return str(status or "").strip().lower()


def is_order_terminal(status: str | None) -> bool:
    """Return True when order status is terminal."""
    return normalize_status(status) in ORDER_TERMINAL_PROCESSING_STATUSES


def can_transition_to_resolving(current_status: str | None, source_event: str) -> bool:
    """
    Return True when a source event should transition an order to `resolving`.

    Supported source events:
    - `dish_terminal`: only `processing -> resolving`
    - `alert_resolved`: `new|processing|resolving -> resolving`
    """
    normalized = normalize_status(current_status)
    if normalized in ORDER_TERMINAL_PROCESSING_STATUSES:
        return False
    if source_event == "dish_terminal":
        return normalized == "processing"
    if source_event == "alert_resolved":
        return normalized in ORDER_RESOLVING_TRANSITIONABLE_STATUSES
    return False


def should_keep_active(status: str | None) -> bool:
    """Return True for non-terminal order statuses."""
    return not is_order_terminal(status)
