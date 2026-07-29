"""Shared ISO datetime parsing and formatting."""

from __future__ import annotations

import time as time_module
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

# Processing starts when the article ownership window closes (:45).
# Publication boundary remains the next hour's :00.
PROCESSING_TRIGGER_MINUTE = 45


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

    Processing begins at minute ``:45`` (ownership window close). A run at
    ``18:45`` publishes for ``19:00``. Times before ``:45`` still resolve to
    the current hour's ``:00`` boundary (useful for retries after publication
    opens).
    """
    current = (now or datetime.now(UTC)).astimezone(UTC)
    hour_start = current.replace(minute=0, second=0, microsecond=0)
    if current.minute >= PROCESSING_TRIGGER_MINUTE:
        return hour_start + timedelta(hours=1)
    return hour_start


def latest_completed_publication_boundary(now: datetime | None = None) -> datetime:
    """Return the newest publication boundary whose article window has closed.

    The ownership window for boundary ``HH:00`` ends at ``(HH-1):45``. Once
    that minute is reached, processing and catch-up runs may use that boundary
    (resolver currently matches ``publication_boundary``).
    """
    return publication_boundary(now)


def previous_podcast_boundary(now: datetime | None = None) -> datetime:
    """Return the latest publication boundary strictly before ``now`` (UTC).

    Publication boundaries are always at ``:00``. This is independent of the
    ``:45`` processing trigger (used for API ``since`` fallbacks).
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
    """Block until the publication boundary when ready early; no-op when late.

    Early editions sleep until ``:00``. Late editions return immediately so
    Megaphone create can proceed and record a positive publication delay.
    """
    sleeper = sleep or time_module.sleep
    target = boundary.astimezone(UTC)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    remaining = (target - current).total_seconds()
    if remaining > 0:
        sleeper(remaining)


def publication_delay_seconds(
    boundary: datetime,
    *,
    success_at: datetime | None = None,
) -> float:
    """Megaphone create success UTC minus publication boundary UTC."""
    success = (success_at or datetime.now(UTC)).astimezone(UTC)
    return (success - boundary.astimezone(UTC)).total_seconds()


def article_ownership_window(boundary: datetime) -> tuple[datetime, datetime]:
    """Return the gap-free ownership interval for an hourly publication.

    Derived only from the UTC publication boundary (top of the hour), never from
    wall-clock processing time. Window length is exactly 60 minutes and ends
    15 minutes before publication:

    - ``window_end`` = ``boundary − 15 minutes`` (exclusive)
    - ``window_start`` = ``window_end − 60 minutes`` (inclusive)

    Example: boundary ``19:00`` UTC owns ``[17:45, 18:45)``. The next boundary
    ``20:00`` owns ``[18:45, 19:45)``, so adjacent windows neither overlap nor
    leave gaps. An article at exactly ``18:45`` belongs only to the ``20:00``
    edition.
    """
    boundary_utc = boundary.astimezone(UTC).replace(second=0, microsecond=0)
    end_exclusive = boundary_utc - timedelta(minutes=15)
    return end_exclusive - timedelta(minutes=60), end_exclusive


def article_lookback_window(
    boundary: datetime, *, hours: int = 2
) -> tuple[datetime, datetime]:
    """Return a wider lookback ending at the same ownership close.

    Used only when the traditional one-hour ownership window returns no articles.
    Example: boundary 19:00 → ownership end 18:45 → 2h lookback ``[16:45, 18:45)``.
    """
    if hours < 1:
        raise ValueError("hours must be >= 1")
    _hourly_start, end_exclusive = article_ownership_window(boundary)
    return end_exclusive - timedelta(hours=hours), end_exclusive
