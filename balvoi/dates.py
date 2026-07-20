"""Shared ISO datetime parsing and formatting."""

from __future__ import annotations

import time as time_module
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

PROCESSING_TRIGGER_MINUTE = 51


def parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string (including trailing ``Z``) to an aware UTC datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def parse_iso_timestamp(value: str | None) -> float:
    """Parse an ISO-8601 string to a Unix timestamp; return ``0.0`` on failure."""
    dt = parse_iso_datetime(value)
    return dt.timestamp() if dt else 0.0


def parse_any_datetime(value: Any) -> datetime | None:
    """Parse epoch seconds/ms or ISO-8601 strings from API payloads."""
    if not value:
        return None
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 1e12 else value
        return datetime.fromtimestamp(ts, tz=UTC)
    return parse_iso_datetime(str(value))


def article_publish_timestamp(article: dict) -> float:
    """Return a story's publish time as Unix seconds."""
    if "publishTimestamp" in article:
        return float(article["publishTimestamp"])
    return parse_iso_timestamp(article.get("publishDate"))


def format_display_datetime(iso: str | None) -> str:
    """Format an ISO timestamp for server-rendered pages (local timezone)."""
    dt = parse_iso_datetime(iso)
    if dt is None:
        return str(iso or "")
    return dt.astimezone().strftime("%b %d, %Y · %I:%M %p").replace(" 0", " ")


def format_rfc2822(iso: str | None) -> str:
    """Format an ISO timestamp for RSS ``pubDate`` (RFC 2822)."""
    dt = parse_iso_datetime(iso) or datetime.now(UTC)
    return format_datetime(dt)


def format_iso_utc(dt: datetime) -> str:
    """Format an aware datetime as ISO-8601 UTC with a trailing ``Z``."""
    return dt.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def publication_boundary(now: datetime | None = None) -> datetime:
    """Return the UTC publication boundary for a processing or manual run.

    Processing begins at minute ``:51``. A run at ``10:51`` publishes for
    ``11:00``. Times before ``:51`` still resolve to the current hour's
    ``:00`` boundary (useful for retries after publication opens).
    """
    current = (now or datetime.now(UTC)).astimezone(UTC)
    hour_start = current.replace(minute=0, second=0, microsecond=0)
    if current.minute >= PROCESSING_TRIGGER_MINUTE:
        return hour_start + timedelta(hours=1)
    return hour_start


def latest_completed_publication_boundary(now: datetime | None = None) -> datetime:
    """Return the newest publication boundary whose article window has closed.

    The ownership window for boundary ``HH:00`` ends at ``(HH-1):51``. Once
    that minute is reached, preview and catch-up runs may use that boundary
    without waiting for the next scheduler tick.
    """
    return publication_boundary(now)


def previous_podcast_boundary(now: datetime | None = None) -> datetime:
    """Return the latest publication boundary strictly before ``now`` (UTC).

    Publication boundaries are always at ``:00``. This is independent of the
    ``:51`` processing trigger (used for API ``since`` fallbacks).
    """
    current = (now or datetime.now(UTC)).astimezone(UTC)
    hour_start = current.replace(minute=0, second=0, microsecond=0)
    if current == hour_start:
        return hour_start - timedelta(hours=1)
    return hour_start


def wait_until_publication_boundary(
    boundary: datetime,
    *,
    now: datetime | None = None,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Block until the publication boundary so validated editions publish on time."""
    sleeper = sleep or time_module.sleep
    target = boundary.astimezone(UTC)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    remaining = (target - current).total_seconds()
    if remaining > 0:
        sleeper(remaining)


def article_ownership_window(boundary: datetime) -> tuple[datetime, datetime]:
    """Return the gap-free ownership interval for an hourly publication.

    A boundary at 11:00 UTC owns ``[09:51, 10:51)``. The next boundary owns
    ``[10:51, 11:51)``, so adjacent windows neither overlap nor leave gaps.
    The article-window formula is fixed and independent of when processing starts.
    """
    boundary_utc = boundary.astimezone(UTC).replace(second=0, microsecond=0)
    end_exclusive = boundary_utc - timedelta(minutes=9)
    return end_exclusive - timedelta(hours=1), end_exclusive
