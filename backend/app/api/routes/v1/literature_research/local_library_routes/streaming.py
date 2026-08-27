"""Shared, transport-only helpers for durable local-library event streams."""

from __future__ import annotations

import json


def decode_pubsub_event(raw: object) -> dict[str, object] | None:
    """Decode Redis payloads without coupling stream endpoints to Redis types."""
    try:
        value = raw.decode() if isinstance(raw, bytes) else str(raw)
        event = json.loads(value)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return event if isinstance(event, dict) else None


def sync_event_sequence(event: dict[str, object]) -> int:
    """Read the monotonic sync cursor persisted in ``summary_json``."""
    data = event.get("data")
    summary = data.get("summary_json") if isinstance(data, dict) else None
    try:
        return int(summary.get("sequence", 0)) if isinstance(summary, dict) else 0
    except (TypeError, ValueError):
        return 0


def analysis_event_sequence(event: dict[str, object]) -> int:
    data = event.get("data")
    try:
        return int(data.get("sequence", 0)) if isinstance(data, dict) else 0
    except (TypeError, ValueError):
        return 0
