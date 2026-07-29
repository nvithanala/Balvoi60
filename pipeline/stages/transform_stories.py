"""Transform articles into broadcast scripts (English base + localization)."""

from __future__ import annotations

from balvoi.config import is_english
from pipeline.lib import openai_client


def _article_body(article: dict) -> str:
    return str(article.get("fullText") or article.get("summary") or "").strip()


def transform_stories_english(articles: list[dict], edition_id: str) -> list[dict]:
    del edition_id
    out: list[dict] = []
    for article in articles:
        body = _article_body(article)
        if not body:
            print(f"  [transform] skip {article.get('id')}: no article body")
            continue

        title = str(article.get("title") or "Untitled")
        # Title fallback first; batch rewrite may replace primers below.
        primer = openai_client.story_primer(title, body)
        out.append(
            {
                **article,
                "broadcastScript": openai_client.prepare_english_script(body),
                "primer": primer,
            }
        )

    if not out:
        return out

    mapped = openai_client.generate_batch_headlines(out)
    if mapped is None:
        # Keep title primers — never partially remap on a failed batch.
        return out

    for story in out:
        story_id = openai_client.stable_story_id(story)
        if story_id and story_id in mapped:
            story["primer"] = mapped[story_id]
    return out


def localize_stories(english_stories: list[dict], language: str) -> list[dict]:
    if is_english(language):
        return english_stories

    out = []
    for story in english_stories:
        script = str(story.get("broadcastScript") or "")
        primer = str(story.get("primer") or "")
        out.append(
            {
                **story,
                "broadcastScript": openai_client.translate(script, language),
                "primer": openai_client.translate(primer, language),
            }
        )
    return out


def headlines_segment(stories: list[dict], language: str = "English") -> str:
    return openai_client.batch_headline_intro(
        [s["primer"] for s in stories[:10]],
        language=language,
    )
