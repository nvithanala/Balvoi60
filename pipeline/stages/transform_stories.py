"""Transform articles into broadcast scripts (English base + localization)."""

from __future__ import annotations

from balvoi.config import is_english
from pipeline.lib import openai_client


def transform_stories_english(articles: list[dict], edition_id: str) -> list[dict]:
    out = []
    for article in articles:
        primer = openai_client.story_primer(article["title"], article["fullText"])
        out.append(
            {
                **article,
                "broadcastScript": openai_client.prepare_english_script(article["fullText"]),
                "primer": primer,
            }
        )
    return out


def localize_stories(english_stories: list[dict], language: str) -> list[dict]:
    if is_english(language):
        return english_stories

    out = []
    for story in english_stories:
        out.append(
            {
                **story,
                "broadcastScript": openai_client.translate(story["broadcastScript"], language),
                "primer": openai_client.translate(story["primer"], language),
            }
        )
    return out


def headlines_segment(stories: list[dict]) -> str:
    return openai_client.batch_headline_intro([s["primer"] for s in stories[:10]])
