from __future__ import annotations

from datetime import UTC, datetime

SAMPLE_EDITION = {
    "id": "balvoi30-en",
    "slug": "en",
    "name": "BalVoi:30 News",
    "editionName": "Five Eyes Edition",
    "locale": "en-US",
    "city": "New York City",
    "colors": {"primary": "#0A3D91", "secondary": "#C0C7D1", "accent": "#152238"},
}

SAMPLE_EPISODE = {
    "id": "2026-06-16T23-25-10Z-en",
    "runId": "2026-06-16T23-25-10Z",
    "slug": "en",
    "timestamp": "2026-06-16T23:29:48+00:00",
    "audioUrl": "/episodes/2026-06-16T23-25-10Z/en.mp3",
    "durationSeconds": 1926,
    "headlines": ["Headline Alpha", "Headline Beta"],
}


def article(
    article_id: str,
    *,
    publish_offset_seconds: int = 0,
    breaking: bool = False,
    summary: str = "word " * 200,
    fixed_now: datetime | None = None,
) -> dict:
    base = fixed_now or datetime(2026, 6, 16, 12, 0, 0, tzinfo=UTC)
    return {
        "id": article_id,
        "title": f"Story {article_id}",
        "summary": summary,
        "breaking": breaking,
        "publishTimestamp": base.timestamp() + publish_offset_seconds,
    }
