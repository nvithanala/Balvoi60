from __future__ import annotations

from unittest.mock import Mock

import pytest

from pipeline.lib import openai_client
from pipeline.lib.duration_budget import MIN_PUBLISH_DURATION_SECONDS, fit_stories_to_budget
from pipeline.stages.transform_stories import (
    headlines_segment,
    localize_stories,
    transform_stories_english,
)

EDITION_ID = "balvoi60-en"


@pytest.fixture(autouse=True)
def _clear_llm_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests must not hit live NewsGenie Bedrock when a real key is in the env."""
    monkeypatch.delenv("BALVOI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


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


def test_english_without_key_uses_title_headlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BALVOI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    post = Mock(side_effect=AssertionError("Bedrock must not be called without a key"))
    monkeypatch.setattr(openai_client.requests, "post", post)

    stories = transform_stories_english(
        [{"id": "1", "title": "Markets Rise", "fullText": "Stocks climbed after the rate decision."}],
        EDITION_ID,
    )
    assert stories[0]["primer"] == "Markets Rise"
    assert "Stocks climbed" not in stories[0]["primer"]
    post.assert_not_called()
    headlines = headlines_segment(stories, language="English")
    assert headlines == "Markets Rise"
    post.assert_not_called()


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
