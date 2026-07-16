"""Shared ISO datetime parsing and formatting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any


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


def previous_podcast_boundary(now: datetime | None = None) -> datetime:
    """Return the latest :25 or :55 mark strictly before ``now`` (UTC)."""
    current = (now or datetime.now(UTC)).astimezone(UTC).replace(second=0, microsecond=0)
    probe = current
    if current.minute in (25, 55):
        probe = current - timedelta(minutes=1)

    minute = probe.minute
    if minute >= 55:
        return probe.replace(minute=55)
    if minute >= 25:
        return probe.replace(minute=25)
    return (probe.replace(minute=55) - timedelta(hours=1))
