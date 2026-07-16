from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from balvoi import dates


def test_parse_iso_datetime_z_suffix() -> None:
    dt = dates.parse_iso_datetime("2026-06-16T23:29:48.947892+00:00")
    assert dt is not None
    assert dt.year == 2026
    assert dt.tzinfo == UTC


def test_parse_iso_datetime_trailing_z() -> None:
    dt = dates.parse_iso_datetime("2026-06-16T23:29:48Z")
    assert dt is not None
    assert dt.hour == 23


def test_parse_iso_datetime_naive_assumes_utc() -> None:
    dt = dates.parse_iso_datetime("2026-06-16T12:00:00")
    assert dt is not None
    assert dt.tzinfo == UTC


def test_parse_iso_datetime_invalid() -> None:
    assert dates.parse_iso_datetime("") is None
    assert dates.parse_iso_datetime("not-a-date") is None


def test_parse_iso_timestamp() -> None:
    ts = dates.parse_iso_timestamp("2026-06-16T23:29:48+00:00")
    assert ts > 0
    assert dates.parse_iso_timestamp("bad") == 0.0


def test_parse_any_datetime_epoch_seconds() -> None:
    dt = dates.parse_any_datetime(1_700_000_000)
    assert dt is not None
    assert dt.tzinfo == UTC


def test_parse_any_datetime_epoch_millis() -> None:
    dt = dates.parse_any_datetime(1_700_000_000_000)
    assert dt is not None
    assert dt.year >= 2023


def test_article_publish_timestamp_prefers_numeric() -> None:
    article = {"publishTimestamp": 1_700_000_000, "publishDate": "2020-01-01T00:00:00Z"}
    assert dates.article_publish_timestamp(article) == 1_700_000_000


def test_article_publish_timestamp_falls_back_to_iso() -> None:
    article = {"publishDate": "2026-06-16T23:29:48+00:00"}
    assert dates.article_publish_timestamp(article) > 0


def test_format_display_datetime_invalid() -> None:
    assert dates.format_display_datetime(None) == ""
    assert dates.format_display_datetime("nope") == "nope"


def test_format_rfc2822_parsable_by_email_utils() -> None:
    value = dates.format_rfc2822("2026-06-16T23:29:48+00:00")
    parsed = parsedate_to_datetime(value)
    assert parsed.year == 2026


def test_format_rfc2822_invalid_falls_back_to_now() -> None:
    before = datetime.now(UTC).replace(microsecond=0)
    value = dates.format_rfc2822("invalid")
    parsed = parsedate_to_datetime(value).replace(microsecond=0)
    after = datetime.now(UTC).replace(microsecond=0)
    assert before <= parsed <= after


def test_format_iso_utc() -> None:
    dt = datetime(2026, 7, 8, 14, 25, 0, tzinfo=UTC)
    assert dates.format_iso_utc(dt) == "2026-07-08T14:25:00Z"


def test_previous_podcast_boundary_on_25_uses_prior_55() -> None:
    now = datetime(2026, 7, 8, 14, 25, 0, tzinfo=UTC)
    boundary = dates.previous_podcast_boundary(now)
    assert boundary == datetime(2026, 7, 8, 13, 55, 0, tzinfo=UTC)


def test_previous_podcast_boundary_on_55_uses_prior_25() -> None:
    now = datetime(2026, 7, 8, 14, 55, 0, tzinfo=UTC)
    boundary = dates.previous_podcast_boundary(now)
    assert boundary == datetime(2026, 7, 8, 14, 25, 0, tzinfo=UTC)


def test_previous_podcast_boundary_mid_hour() -> None:
    now = datetime(2026, 7, 8, 14, 30, 0, tzinfo=UTC)
    boundary = dates.previous_podcast_boundary(now)
    assert boundary == datetime(2026, 7, 8, 14, 25, 0, tzinfo=UTC)


def test_previous_podcast_boundary_top_of_hour() -> None:
    now = datetime(2026, 7, 8, 14, 0, 0, tzinfo=UTC)
    boundary = dates.previous_podcast_boundary(now)
    assert boundary == datetime(2026, 7, 8, 13, 55, 0, tzinfo=UTC)


def test_previous_podcast_boundary_after_55() -> None:
    now = datetime(2026, 7, 8, 14, 56, 0, tzinfo=UTC)
    boundary = dates.previous_podcast_boundary(now)
    assert boundary == datetime(2026, 7, 8, 14, 55, 0, tzinfo=UTC)
