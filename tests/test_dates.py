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


def test_processing_trigger_minute_is_51() -> None:
    assert dates.PROCESSING_TRIGGER_MINUTE == 51


def test_publication_boundary_at_1051_is_1100() -> None:
    now = datetime(2026, 7, 17, 10, 51, 0, tzinfo=UTC)
    assert dates.publication_boundary(now) == datetime(2026, 7, 17, 11, 0, tzinfo=UTC)


def test_publication_boundary_at_2351_rolls_to_next_day() -> None:
    now = datetime(2026, 7, 17, 23, 51, 0, tzinfo=UTC)
    assert dates.publication_boundary(now) == datetime(2026, 7, 18, 0, 0, tzinfo=UTC)


def test_publication_boundary_before_51_is_current_hour() -> None:
    now = datetime(2026, 7, 17, 10, 50, 59, tzinfo=UTC)
    assert dates.publication_boundary(now) == datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
    now = datetime(2026, 7, 17, 11, 0, 0, tzinfo=UTC)
    assert dates.publication_boundary(now) == datetime(2026, 7, 17, 11, 0, tzinfo=UTC)


def test_required_window_for_1051_processing_start() -> None:
    processing = datetime(2026, 7, 17, 10, 51, tzinfo=UTC)
    boundary = dates.publication_boundary(processing)
    start, end = dates.article_ownership_window(boundary)
    assert boundary == datetime(2026, 7, 17, 11, 0, tzinfo=UTC)
    assert start == datetime(2026, 7, 17, 9, 51, tzinfo=UTC)
    assert end == datetime(2026, 7, 17, 10, 51, tzinfo=UTC)


def test_required_window_for_2351_processing_start() -> None:
    processing = datetime(2026, 7, 17, 23, 51, tzinfo=UTC)
    boundary = dates.publication_boundary(processing)
    start, end = dates.article_ownership_window(boundary)
    assert boundary == datetime(2026, 7, 18, 0, 0, tzinfo=UTC)
    assert start == datetime(2026, 7, 17, 22, 51, tzinfo=UTC)
    assert end == datetime(2026, 7, 17, 23, 51, tzinfo=UTC)


def test_previous_podcast_boundary_at_10_00_is_09_00() -> None:
    now = datetime(2026, 7, 8, 10, 0, 0, tzinfo=UTC)
    boundary = dates.previous_podcast_boundary(now)
    assert boundary == datetime(2026, 7, 8, 9, 0, 0, tzinfo=UTC)


def test_previous_podcast_boundary_at_10_01_is_10_00() -> None:
    now = datetime(2026, 7, 8, 10, 1, 0, tzinfo=UTC)
    boundary = dates.previous_podcast_boundary(now)
    assert boundary == datetime(2026, 7, 8, 10, 0, 0, tzinfo=UTC)


def test_previous_podcast_boundary_at_10_59_is_10_00() -> None:
    now = datetime(2026, 7, 8, 10, 59, 0, tzinfo=UTC)
    boundary = dates.previous_podcast_boundary(now)
    assert boundary == datetime(2026, 7, 8, 10, 0, 0, tzinfo=UTC)


def test_previous_podcast_boundary_rolls_over_midnight() -> None:
    now = datetime(2026, 7, 9, 0, 0, 0, tzinfo=UTC)
    assert dates.previous_podcast_boundary(now) == datetime(2026, 7, 8, 23, 0, 0, tzinfo=UTC)


def test_hourly_article_ownership_is_gap_free() -> None:
    boundary = datetime(2026, 7, 8, 11, 0, 0, tzinfo=UTC)
    start, end = dates.article_ownership_window(boundary)
    assert start == datetime(2026, 7, 8, 9, 51, tzinfo=UTC)
    assert end == datetime(2026, 7, 8, 10, 51, tzinfo=UTC)
    next_start, _ = dates.article_ownership_window(boundary.replace(hour=12))
    assert next_start == end


def test_publication_boundary_converts_aware_timezone() -> None:
    # 06:59 EDT == 10:59 UTC → after :51 → next hour publication boundary
    local = datetime.fromisoformat("2026-07-08T06:59:00-04:00")
    assert dates.publication_boundary(local) == datetime(2026, 7, 8, 11, 0, tzinfo=UTC)


def test_wait_until_publication_boundary_sleeps_remaining_seconds() -> None:
    slept: list[float] = []
    boundary = datetime(2026, 7, 17, 11, 0, tzinfo=UTC)
    now = datetime(2026, 7, 17, 10, 51, 30, tzinfo=UTC)
    dates.wait_until_publication_boundary(boundary, now=now, sleep=slept.append)
    assert slept == [510.0]


def test_wait_until_publication_boundary_skips_when_already_past() -> None:
    slept: list[float] = []
    boundary = datetime(2026, 7, 17, 11, 0, tzinfo=UTC)
    now = datetime(2026, 7, 17, 11, 0, 1, tzinfo=UTC)
    dates.wait_until_publication_boundary(boundary, now=now, sleep=slept.append)
    assert slept == []
