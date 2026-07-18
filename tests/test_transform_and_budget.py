from __future__ import annotations

from pipeline.lib.duration_budget import MIN_PUBLISH_DURATION_SECONDS, fit_stories_to_budget
from pipeline.stages.transform_stories import localize_stories, transform_stories_english

EDITION_ID = "balvoi60-en"


def test_prepare_english_script_is_verbatim() -> None:
    body = "Original article text. No rewriting allowed."
    stories = transform_stories_english(
        [{"id": "1", "title": "Test", "fullText": body}],
        EDITION_ID,
    )
    assert stories[0]["broadcastScript"] == body


def test_transform_falls_back_to_summary() -> None:
    stories = transform_stories_english(
        [{"id": "1", "title": "Fallback", "summary": "Summary only body text."}],
        EDITION_ID,
    )
    assert stories[0]["broadcastScript"] == "Summary only body text."


def test_transform_skips_empty_body() -> None:
    stories = transform_stories_english(
        [{"id": "1", "title": "Empty"}],
        EDITION_ID,
    )
    assert stories == []


def test_localize_skips_english() -> None:
    english = [{"id": "1", "broadcastScript": "Hello", "primer": "Hi"}]
    assert localize_stories(english, "English") == english


def test_compatibility_budget_does_not_trim_stories() -> None:
    long_text = "word " * 2000
    stories = [
        {"id": "a", "broadcastScript": long_text},
        {"id": "b", "broadcastScript": long_text},
        {"id": "c", "broadcastScript": long_text},
    ]
    fitted = fit_stories_to_budget(stories, EDITION_ID)
    assert fitted == stories


def test_only_minimum_publication_duration_remains() -> None:
    assert MIN_PUBLISH_DURATION_SECONDS == 600
