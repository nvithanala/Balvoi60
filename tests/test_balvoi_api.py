from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from pipeline.lib.balvoi_api import (
    clean_article_body,
    fetch_podcast_articles,
    normalize_podcast_article,
)

JUNK_PARAGRAPHS = [
    "I'm ready to rewrite sentences according to the specifications. Please provide the input sentence you would like me to process.",
    "The sentence to rewrite was not provided in your input. Please provide the sentence you would like me to rewrite according to the guidelines.",
    "I'm ready to rewrite the biased sentence. Please provide the sentence you'd like me to process for debiasing.",
    "I'm ready to rewrite sentences according to the bias detection and humanization guidelines. Please share the text.",
    "I'm ready to help rewrite biased sentences. Please provide the sentence you want me to rewrite.",
]

REAL_NEWS_PARAGRAPHS = [
    "England defeated Ghana 3-1 in Boston after a dominant second-half performance.",
    "The Republican Party said it would provide additional funding following the Senate vote.",
    "Contributions from private donors helped fund the recovery plan after the floods.",
    "Analysts said the following developments could reshape global energy markets this year.",
    "Party leaders met in Berlin to discuss provisions in the new infrastructure bill.",
    "Officials confirmed the rewrite of zoning rules would not take effect until next month.",
    "The opposition argued the government failed to provide adequate relief to affected communities.",
]


def test_clean_article_body_drops_only_leakage_paragraphs() -> None:
    for paragraph in JUNK_PARAGRAPHS:
        cleaned = clean_article_body(f"<p>{paragraph}</p>")
        assert cleaned == "", f"expected junk removed: {paragraph[:60]}..."

    for paragraph in REAL_NEWS_PARAGRAPHS:
        cleaned = clean_article_body(f"<p>{paragraph}</p>")
        assert cleaned == paragraph, f"expected news kept: {paragraph[:60]}..."


def test_clean_article_body_mixed_html_preserves_real_news() -> None:
    parts = REAL_NEWS_PARAGRAPHS[:3] + JUNK_PARAGRAPHS[:2] + REAL_NEWS_PARAGRAPHS[3:5]
    raw = "".join(f"<p>{p}</p>" for p in parts)
    cleaned = clean_article_body(raw, article_id="mixed-article")
    for paragraph in REAL_NEWS_PARAGRAPHS[:3] + REAL_NEWS_PARAGRAPHS[3:5]:
        assert paragraph in cleaned
    for paragraph in JUNK_PARAGRAPHS[:2]:
        assert paragraph[:40] not in cleaned


def test_clean_article_body_strips_html_and_junk() -> None:
    raw = (
        "<p>England defeated Ghana 3-1 in Boston.</p>"
        "<p>I'm ready to rewrite biased sentences according to your specifications.</p>"
        "<p>Another real paragraph about the match.</p>"
    )
    cleaned = clean_article_body(raw, article_id="abc123")
    assert "England defeated Ghana" in cleaned
    assert "Another real paragraph" in cleaned
    assert "ready to rewrite" not in cleaned.lower()
    assert "<p>" not in cleaned


def test_clean_article_body_plain_text_junk_mid_body() -> None:
    raw = (
        "Markets rose sharply today. "
        "Please   provide the sentence to rewrite. "
        "Analysts expect further gains."
    )
    cleaned = clean_article_body(raw, article_id="story-1")
    assert "Markets rose sharply" in cleaned
    assert "Analysts expect further gains" in cleaned
    assert "provide the sentence" not in cleaned.lower()


def test_clean_article_body_debiasing_phrase() -> None:
    raw = "Real lead.\n\nDebiasing and humanisation process details here.\n\nReal tail."
    cleaned = clean_article_body(raw)
    assert "Real lead" in cleaned
    assert "Real tail" in cleaned
    assert "debiasing" not in cleaned.lower()


def test_clean_article_body_plain_text() -> None:
    assert clean_article_body("Short summary only.") == "Short summary only."


def test_normalize_podcast_article_maps_fields() -> None:
    raw = {
        "_id": "mongo-id-1",
        "title": "Test headline",
        "body": "<p>Full article body.</p>",
        "countryName": "United States",
        "createdAt": "2026-06-16T23:29:48Z",
        "categories": ["Politics", "US"],
        "summary": "Short summary",
        "slug": "test-headline",
    }
    article = normalize_podcast_article(raw, "https://staging.balvoi.com")
    assert article["id"] == "mongo-id-1"
    assert article["title"] == "Test headline"
    assert article["fullText"] == "Full article body."
    assert article["country"] == "United States"
    assert article["category"] == "Politics"
    assert article["tags"] == ["Politics", "US"]
    assert article["summary"] == "Short summary"
    assert article["slug"] == "test-headline"
    assert article["url"] == "https://staging.balvoi.com/story/test-headline"
    assert article["publishTimestamp"] > 0
    assert article["source"] == "BalVoi"


@patch("pipeline.lib.balvoi_api.requests.get")
def test_fetch_podcast_articles_parses_response(mock_get: MagicMock) -> None:
    mock_get.return_value.ok = True
    mock_get.return_value.json.return_value = {
        "status": True,
        "status_code": 200,
        "data": {
            "filter": {},
            "count": 1,
            "articles": [
                {
                    "_id": "a1",
                    "title": "Story one",
                    "body": "Body one.",
                    "createdAt": "2026-06-16T12:00:00Z",
                    "categories": ["World"],
                    "summary": "Sum",
                    "slug": "story-one",
                }
            ],
        },
        "message": "ok",
    }

    with patch.dict(
        "os.environ",
        {
            "BALVOI_API_KEY": "test-key",
            "BALVOI_API_URL": "https://api.example.test",
            "BALVOI_SINCE_OVERRIDE": "2026-06-16T10:00:00Z",
            "BALVOI_ARTICLE_LIMIT": "50",
        },
        clear=False,
    ):
        articles = fetch_podcast_articles()

    assert len(articles) == 1
    assert articles[0]["id"] == "a1"
    mock_get.assert_called_once()
    call_kwargs = mock_get.call_args
    assert call_kwargs[0][0] == "https://api.example.test/podcast_articles"
    assert call_kwargs[1]["headers"]["X-Api-Token"] == "test-key"
    assert call_kwargs[1]["params"]["limit"] == 50
    assert call_kwargs[1]["params"]["since"] == "2026-06-16T10:00:00Z"
    assert "edition" not in call_kwargs[1]["params"]


@patch("pipeline.lib.balvoi_api.requests.get")
def test_fetch_podcast_articles_empty_returns_list(mock_get: MagicMock) -> None:
    mock_get.return_value.ok = True
    mock_get.return_value.json.return_value = {
        "status": True,
        "data": {"articles": []},
    }

    with patch.dict("os.environ", {"BALVOI_API_KEY": "test-key"}, clear=False):
        assert fetch_podcast_articles() == []


def test_fetch_podcast_articles_no_key() -> None:
    with patch.dict("os.environ", {"BALVOI_API_KEY": ""}, clear=False):
        assert fetch_podcast_articles() == []
