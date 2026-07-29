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


def test_processing_trigger_minute_is_45() -> None:
    assert dates.PROCESSING_TRIGGER_MINUTE == 45


def test_publication_boundary_at_1045_is_1100() -> None:
    now = datetime(2026, 7, 17, 10, 45, 0, tzinfo=UTC)
    assert dates.publication_boundary(now) == datetime(2026, 7, 17, 11, 0, tzinfo=UTC)


def test_publication_boundary_at_1051_is_still_1100() -> None:
    now = datetime(2026, 7, 17, 10, 51, 0, tzinfo=UTC)
    assert dates.publication_boundary(now) == datetime(2026, 7, 17, 11, 0, tzinfo=UTC)


def test_publication_boundary_at_2345_rolls_to_next_day() -> None:
    now = datetime(2026, 7, 17, 23, 45, 0, tzinfo=UTC)
    assert dates.publication_boundary(now) == datetime(2026, 7, 18, 0, 0, tzinfo=UTC)


def test_publication_boundary_before_45_is_current_hour() -> None:
    now = datetime(2026, 7, 17, 10, 44, 59, tzinfo=UTC)
    assert dates.publication_boundary(now) == datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
    now = datetime(2026, 7, 17, 11, 0, 0, tzinfo=UTC)
    assert dates.publication_boundary(now) == datetime(2026, 7, 17, 11, 0, tzinfo=UTC)


def test_required_window_for_1045_processing_start() -> None:
    processing = datetime(2026, 7, 17, 10, 45, tzinfo=UTC)
    boundary = dates.publication_boundary(processing)
    start, end = dates.article_ownership_window(boundary)
    assert boundary == datetime(2026, 7, 17, 11, 0, tzinfo=UTC)
    assert start == datetime(2026, 7, 17, 9, 45, tzinfo=UTC)
    assert end == datetime(2026, 7, 17, 10, 45, tzinfo=UTC)


def test_required_window_for_2345_processing_start() -> None:
    processing = datetime(2026, 7, 17, 23, 45, tzinfo=UTC)
    boundary = dates.publication_boundary(processing)
    start, end = dates.article_ownership_window(boundary)
    assert boundary == datetime(2026, 7, 18, 0, 0, tzinfo=UTC)
    assert start == datetime(2026, 7, 17, 22, 45, tzinfo=UTC)
    assert end == datetime(2026, 7, 17, 23, 45, tzinfo=UTC)


def test_article_window_for_1900_publication_is_1745_to_1845() -> None:
    boundary = datetime(2026, 7, 17, 19, 0, tzinfo=UTC)
    start, end = dates.article_ownership_window(boundary)
    assert start == datetime(2026, 7, 17, 17, 45, tzinfo=UTC)
    assert end == datetime(2026, 7, 17, 18, 45, tzinfo=UTC)
    assert (end - start).total_seconds() == 60 * 60


def test_article_window_duration_is_exactly_60_minutes() -> None:
    boundary = datetime(2026, 7, 17, 19, 0, tzinfo=UTC)
    start, end = dates.article_ownership_window(boundary)
    assert start.tzinfo is not None and end.tzinfo is not None
    assert (end - start).total_seconds() == 3600


def test_article_window_start_inclusive_end_exclusive() -> None:
    from balvoi.dates import article_publish_timestamp
    from pipeline.stages.select_stories import select_stories

    boundary = datetime(2026, 7, 17, 19, 0, tzinfo=UTC)
    start, end = dates.article_ownership_window(boundary)
    articles = [
        {
            "id": "at-start",
            "title": "At start",
            "publishTimestamp": start.timestamp(),
            "breaking": False,
        },
        {
            "id": "before-end",
            "title": "Before end",
            "publishTimestamp": (end.timestamp() - 0.001),
            "breaking": False,
        },
        {
            "id": "at-end",
            "title": "At end belongs to next",
            "publishTimestamp": end.timestamp(),
            "breaking": False,
        },
    ]
    selected = select_stories(
        articles,
        "balvoi60-global",
        window_start=start,
        window_end_exclusive=end,
    )
    ids = [row["id"] for row in selected]
    assert "at-start" in ids
    assert "before-end" in ids
    assert "at-end" not in ids
    assert article_publish_timestamp(articles[2]) == end.timestamp()


def test_consecutive_windows_touch_at_45_with_no_gap_or_overlap() -> None:
    b19 = datetime(2026, 7, 17, 19, 0, tzinfo=UTC)
    b20 = datetime(2026, 7, 17, 20, 0, tzinfo=UTC)
    start_19, end_19 = dates.article_ownership_window(b19)
    start_20, end_20 = dates.article_ownership_window(b20)
    assert end_19 == datetime(2026, 7, 17, 18, 45, tzinfo=UTC)
    assert start_20 == datetime(2026, 7, 17, 18, 45, tzinfo=UTC)
    assert end_19 == start_20
    assert end_20 == datetime(2026, 7, 17, 19, 45, tzinfo=UTC)
    assert start_19 < end_19 <= start_20 < end_20


def test_article_window_for_0000_crosses_previous_utc_date() -> None:
    boundary = datetime(2026, 7, 18, 0, 0, tzinfo=UTC)
    start, end = dates.article_ownership_window(boundary)
    assert start == datetime(2026, 7, 17, 22, 45, tzinfo=UTC)
    assert end == datetime(2026, 7, 17, 23, 45, tzinfo=UTC)
    assert start.date() < boundary.date()
    assert end.date() < boundary.date()


def test_all_eight_editions_share_same_utc_article_window() -> None:
    from pipeline.lib.publication_identity import ALLOWED_EDITION_SLUGS

    boundary = datetime(2026, 7, 17, 19, 0, tzinfo=UTC)
    expected = (
        datetime(2026, 7, 17, 17, 45, tzinfo=UTC),
        datetime(2026, 7, 17, 18, 45, tzinfo=UTC),
    )
    assert ALLOWED_EDITION_SLUGS == frozenset(
        {"en", "es", "pt", "fr", "de", "ar", "ru", "tr"}
    )
    for _slug in sorted(ALLOWED_EDITION_SLUGS):
        # Window is derived from UTC boundary only — language slug is unused.
        assert dates.article_ownership_window(boundary) == expected


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
    assert start == datetime(2026, 7, 8, 9, 45, tzinfo=UTC)
    assert end == datetime(2026, 7, 8, 10, 45, tzinfo=UTC)
    next_start, _ = dates.article_ownership_window(boundary.replace(hour=12))
    assert next_start == end


def test_article_lookback_window_keeps_ownership_end_and_extends_start() -> None:
    boundary = datetime(2026, 7, 8, 11, 0, 0, tzinfo=UTC)
    start, end = dates.article_lookback_window(boundary, hours=2)
    assert end == datetime(2026, 7, 8, 10, 45, tzinfo=UTC)
    assert start == datetime(2026, 7, 8, 8, 45, tzinfo=UTC)


def test_publication_boundary_converts_aware_timezone() -> None:
    # 06:50 EDT == 10:50 UTC → after :45 → next hour publication boundary
    local = datetime.fromisoformat("2026-07-08T06:50:00-04:00")
    assert dates.publication_boundary(local) == datetime(2026, 7, 8, 11, 0, tzinfo=UTC)


def test_wait_until_publication_boundary_sleeps_remaining_seconds() -> None:
    slept: list[float] = []
    boundary = datetime(2026, 7, 17, 11, 0, tzinfo=UTC)
    now = datetime(2026, 7, 17, 10, 45, 30, tzinfo=UTC)
    dates.wait_until_publication_boundary(boundary, now=now, sleep=slept.append)
    assert slept == [870.0]


def test_wait_until_publication_boundary_skips_when_already_past() -> None:
    slept: list[float] = []
    boundary = datetime(2026, 7, 17, 11, 0, tzinfo=UTC)
    now = datetime(2026, 7, 17, 11, 0, 1, tzinfo=UTC)
    dates.wait_until_publication_boundary(boundary, now=now, sleep=slept.append)
    assert slept == []


def test_publication_delay_seconds_zero_at_boundary() -> None:
    boundary = datetime(2026, 7, 17, 19, 0, tzinfo=UTC)
    assert dates.publication_delay_seconds(boundary, success_at=boundary) == 0.0


def test_publication_delay_seconds_positive_when_late() -> None:
    boundary = datetime(2026, 7, 17, 19, 0, tzinfo=UTC)
    success = datetime(2026, 7, 17, 19, 0, 12, tzinfo=UTC)
    assert dates.publication_delay_seconds(boundary, success_at=success) == 12.0
