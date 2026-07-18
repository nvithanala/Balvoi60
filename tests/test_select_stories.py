from __future__ import annotations

from pipeline.stages.select_stories import select_stories
from tests.helpers import article

EDITION_ID = "balvoi60-en"


def test_select_stories_empty() -> None:
    assert select_stories([], EDITION_ID) == []


def test_select_stories_prefers_breaking_first(patch_select_now, fixed_now) -> None:
    articles = [
        article("old-regular", publish_offset_seconds=-600, fixed_now=fixed_now),
        article("new-breaking", publish_offset_seconds=-120, breaking=True, fixed_now=fixed_now),
        article("new-regular", publish_offset_seconds=-60, fixed_now=fixed_now),
    ]
    selected = select_stories(articles, EDITION_ID, since_minutes=30)
    assert selected[0]["id"] == "new-breaking"
    assert "new-regular" in [a["id"] for a in selected]


def test_select_stories_excludes_recently_aired(patch_select_now, fixed_now) -> None:
    articles = [
        article("a1", publish_offset_seconds=-300, fixed_now=fixed_now),
        article("a2", publish_offset_seconds=-240, fixed_now=fixed_now),
    ]
    selected = select_stories(articles, EDITION_ID, exclude_ids={"a1"})
    assert all(s["id"] != "a1" for s in selected)
    assert any(s["id"] == "a2" for s in selected)


def test_select_stories_never_repeats_when_all_on_cooldown(patch_select_now, fixed_now) -> None:
    articles = [
        article("a1", publish_offset_seconds=-300, fixed_now=fixed_now),
        article("a2", publish_offset_seconds=-240, fixed_now=fixed_now),
    ]
    selected = select_stories(articles, EDITION_ID, exclude_ids={"a1", "a2"})
    assert selected == []


def test_select_stories_does_not_fall_back_when_window_empty(patch_select_now, fixed_now) -> None:
    articles = [
        article("stale", publish_offset_seconds=-7200, fixed_now=fixed_now),
    ]
    selected = select_stories(articles, EDITION_ID, since_minutes=30)
    assert selected == []


def test_select_stories_has_no_runtime_cap(patch_select_now, fixed_now) -> None:
    articles = [
        article("short", full_text="word " * 100, publish_offset_seconds=-300, fixed_now=fixed_now),
        article(
            "too-long",
            full_text="word " * 5000,
            publish_offset_seconds=-240,
            fixed_now=fixed_now,
        ),
    ]
    selected = select_stories(articles, EDITION_ID, since_minutes=30)
    ids = [a["id"] for a in selected]
    assert "short" in ids
    assert "too-long" in ids


def test_select_stories_uses_half_open_ownership_window(fixed_now) -> None:
    from datetime import timedelta

    start = fixed_now - timedelta(hours=2, minutes=9)
    end = fixed_now - timedelta(hours=1, minutes=9)
    articles = [
        article("at-start", fixed_now=start),
        article("before-end", fixed_now=end - timedelta(milliseconds=1)),
        article("at-end", fixed_now=end),
    ]
    selected = select_stories(
        articles,
        EDITION_ID,
        window_start=start,
        window_end_exclusive=end,
    )
    assert [row["id"] for row in selected] == ["before-end", "at-start"]
