"""NewsGenie Bedrock prompt client for translation and headline rewrite.

Calls ``POST {BALVOI_API_URL}/bedrock/prompt`` with ``{prompt, data}`` and reads
``data.text`` from the response. English broadcast scripts remain verbatim.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

import requests

from balvoi.config import is_english
from pipeline.errors import LocalizationError
from pipeline.lib.concurrency import slot

_metrics_lock = threading.Lock()
_metrics = {"requests": 0, "retries": 0, "rateLimitResponses": 0}

# Deterministic chunk size so large hours stay within context without redesign.
HEADLINE_BATCH_CHUNK_SIZE = 25
_EXCERPT_CHARS = 400
_BEDROCK_PATH = "/bedrock/prompt"

_HEADLINE_SYSTEM = (
    "You are writing concise spoken headlines for an hourly multilingual news "
    "bulletin. Rewrite each supplied story into one factual, neutral, natural "
    "spoken headline. Return exactly one headline for every story_id. Do not add "
    "introductions, transitions, category labels, explanations, or facts not "
    "contained in the source material."
)


def reset_metrics() -> None:
    with _metrics_lock:
        _metrics.update(requests=0, retries=0, rateLimitResponses=0)


def metrics_snapshot() -> dict[str, int]:
    with _metrics_lock:
        return dict(_metrics)


def _api_base() -> str:
    return (os.environ.get("BALVOI_API_URL") or "https://api.staging.newsgenie.ai").rstrip(
        "/"
    )


def _api_key() -> str:
    return (os.environ.get("BALVOI_API_KEY") or "").strip()


def _extract_text(payload: Any) -> str:
    """Return the model output from a ``/bedrock/prompt`` response body."""
    if not isinstance(payload, dict):
        raise ValueError("bedrock response must be a JSON object")
    data = payload.get("data")
    if isinstance(data, dict):
        text = data.get("text")
        if text is None:
            raise ValueError("bedrock response missing data.text")
        return str(text).strip()
    if isinstance(data, str):
        return data.strip()
    raise ValueError("bedrock response missing data.text")


def _bedrock_prompt(
    prompt: str,
    data: Any,
    *,
    timeout: int = 120,
    strict: bool = False,
    fallback: str = "",
) -> str:
    """POST ``{prompt, data}`` to NewsGenie Bedrock and return ``data.text``."""
    api_key = _api_key()
    if not api_key:
        if strict:
            raise LocalizationError("BALVOI_API_KEY is required for localization")
        return fallback

    url = f"{_api_base()}{_BEDROCK_PATH}"
    try:
        with slot("translation"):
            with _metrics_lock:
                _metrics["requests"] += 1
            res = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json={"prompt": prompt, "data": data},
                timeout=timeout,
            )
        if res.status_code == 429:
            with _metrics_lock:
                _metrics["rateLimitResponses"] += 1
        res.raise_for_status()
        result = _extract_text(res.json())
        if not result:
            raise ValueError("empty response")
        return result
    except Exception as err:
        if strict:
            raise LocalizationError(
                f"Bedrock localization failed: {type(err).__name__}"
            ) from err
        print(f"  [warn] Bedrock prompt request failed: {err}")
        return fallback


def _chat(system: str, user: str, timeout: int = 120, *, strict: bool = False) -> str:
    """Run a system+user style prompt via Bedrock ``/bedrock/prompt``."""
    return _bedrock_prompt(
        system,
        user,
        timeout=timeout,
        strict=strict,
        fallback=user,
    )


def prepare_english_script(body: str) -> str:
    """Return the article body verbatim for English broadcast audio."""
    return str(body or "").strip()


def story_primer(title: str, body: str) -> str:
    """Safe title-only fallback headline (no LLM).

    Body is unused on purpose: fallback headlines must not replay story openings.
    """
    del body
    return str(title or "").strip() or "Untitled"


def stable_story_id(story: dict[str, Any]) -> str:
    """Return the repository story/article id as a nonempty string."""
    return str(story.get("id") or "").strip()


def _story_excerpt(story: dict[str, Any]) -> str:
    summary = str(story.get("summary") or "").strip()
    if summary:
        return summary[:_EXCERPT_CHARS]
    body = str(story.get("fullText") or story.get("broadcastScript") or "").strip()
    return body[:_EXCERPT_CHARS]


def _headline_inputs(stories: list[dict[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for story in stories:
        story_id = stable_story_id(story)
        if not story_id:
            continue
        items.append(
            {
                "story_id": story_id,
                "source_title": str(story.get("title") or "").strip() or "Untitled",
                "excerpt": _story_excerpt(story),
            }
        )
    return items


def validate_batch_headlines(
    requested_ids: list[str],
    payload: Any,
) -> dict[str, str]:
    """Validate structured headline JSON and return ``{story_id: headline}``.

    Raises ``ValueError`` with a precise reason on any failure.
    """
    if not isinstance(payload, dict):
        raise ValueError("headline payload must be a JSON object")
    rows = payload.get("headlines")
    if not isinstance(rows, list):
        raise ValueError("headlines must be a list")
    expected = list(requested_ids)
    expected_set = set(expected)
    if len(expected) != len(expected_set):
        raise ValueError("requested story_id list contains duplicates")
    if len(rows) != len(expected):
        raise ValueError(
            f"headline count mismatch: got {len(rows)} expected {len(expected)}"
        )

    mapped: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("headline entry must be an object")
        story_id = str(row.get("story_id") or "").strip()
        headline = str(row.get("headline") or "").strip()
        if not story_id:
            raise ValueError("headline entry missing story_id")
        if story_id not in expected_set:
            raise ValueError(f"unknown story_id returned: {story_id!r}")
        if story_id in mapped:
            raise ValueError(f"duplicate story_id returned: {story_id!r}")
        if not headline:
            raise ValueError(f"empty headline for story_id={story_id!r}")
        mapped[story_id] = headline

    missing = [sid for sid in expected if sid not in mapped]
    if missing:
        raise ValueError(f"missing story_id(s): {missing!r}")
    return mapped


def _request_headline_batch_json(items: list[dict[str, str]]) -> dict[str, Any] | None:
    if not _api_key():
        print("  [warn] headline batch skipped: BALVOI_API_KEY missing")
        return None

    user = (
        "Return JSON only in this exact shape:\n"
        '{"headlines":[{"story_id":"...","headline":"..."}]}\n\n'
        "Rules for each headline:\n"
        "- factual, neutral, natural when spoken aloud\n"
        "- normally 6 to 12 words; one complete concise news sentence\n"
        "- preserve important names, places, organizations, and numbers\n"
        "- rewrite for audio; do not copy the source title verbatim when a "
        "natural rewrite is possible\n"
        "- do not repeat the first sentence of the excerpt verbatim\n"
        "- no filler, teasers, transitions, labels, or commentary\n"
        "- avoid phrases like today's news, today's headlines, this morning, "
        "this evening, coming up, here's what happened, in this hour, our top "
        "story, breaking news\n"
        "- do not add facts not present in the source\n"
        "- return only the headline text in the headline field\n\n"
        f"Stories:\n{json.dumps(items, ensure_ascii=False)}"
    )
    try:
        content = _bedrock_prompt(
            _HEADLINE_SYSTEM,
            user,
            timeout=120,
            strict=False,
            fallback="",
        )
        if not content:
            raise ValueError("empty response")
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("JSON root must be an object")
        return parsed
    except Exception as err:
        print(f"  [warn] headline batch request failed: {type(err).__name__}: {err}")
        return None


def generate_batch_headlines(stories: list[dict[str, Any]]) -> dict[str, str] | None:
    """Generate spoken headlines for ``stories`` in one or more Bedrock batches.

    Returns a complete ``{story_id: headline}`` map on full success, or ``None``
    when any chunk is missing/invalid so callers can fall back safely without
    partial mismapping across the transform.
    """
    inputs = _headline_inputs(stories)
    if not inputs:
        return {}

    combined: dict[str, str] = {}
    for start in range(0, len(inputs), HEADLINE_BATCH_CHUNK_SIZE):
        chunk = inputs[start : start + HEADLINE_BATCH_CHUNK_SIZE]
        requested_ids = [item["story_id"] for item in chunk]
        payload = _request_headline_batch_json(chunk)
        if payload is None:
            print(
                "  [warn] headline batch unavailable; "
                f"failing closed for {len(requested_ids)} stories"
            )
            return None
        try:
            mapped = validate_batch_headlines(requested_ids, payload)
        except ValueError as err:
            print(f"  [warn] headline batch validation failed: {err}")
            return None
        combined.update(mapped)
    return combined


def translate(text: str, target_language: str) -> str:
    if is_english(target_language):
        return text
    system = (
        f"Translate into natural {target_language} for broadcast news. "
        "Culturally localize names and references. Keep similar length and pacing. "
        "Return only the translation."
    )
    translated = _chat(system, text, timeout=120, strict=True)
    if not translated.strip():
        raise LocalizationError(
            f"Bedrock returned an empty {target_language} translation"
        )
    if translated.strip() == text.strip():
        raise LocalizationError(
            f"Bedrock returned unchanged source text for {target_language}"
        )
    return translated


def batch_headline_intro(primers: list[str], *, language: str = "English") -> str:
    """Join primer lines into one headlines segment locally (no LLM)."""
    del language  # Language is already reflected in translated primers.
    if not primers:
        return ""
    trimmed = [p.strip() for p in primers[:10] if p and p.strip()]
    if not trimmed:
        return ""
    return " ".join(trimmed)[:1500]
