"""OpenAI tasks: English script passthrough, primers, translation."""

from __future__ import annotations

import os
import threading

import requests

from balvoi.config import is_english
from pipeline.errors import LocalizationError
from pipeline.lib.concurrency import slot

_metrics_lock = threading.Lock()
_metrics = {"requests": 0, "retries": 0, "rateLimitResponses": 0}


def reset_metrics() -> None:
    with _metrics_lock:
        _metrics.update(requests=0, retries=0, rateLimitResponses=0)


def metrics_snapshot() -> dict[str, int]:
    with _metrics_lock:
        return dict(_metrics)


def _chat(system: str, user: str, timeout: int = 120, *, strict: bool = False) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        if strict:
            raise LocalizationError("OPENAI_API_KEY is required for localization")
        return user

    try:
        with slot("translation"):
            with _metrics_lock:
                _metrics["requests"] += 1
            res = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.4,
                },
                timeout=timeout,
            )
        if res.status_code == 429:
            with _metrics_lock:
                _metrics["rateLimitResponses"] += 1
        res.raise_for_status()
        result = str(res.json()["choices"][0]["message"]["content"]).strip()
        if not result:
            raise ValueError("empty response")
        return result
    except Exception as err:
        if strict:
            raise LocalizationError(f"OpenAI localization failed: {type(err).__name__}") from err
        print(f"  [warn] OpenAI request failed: {err}")
        return user


def prepare_english_script(body: str) -> str:
    """Return the article body verbatim for English broadcast audio."""
    return str(body or "").strip()


def story_primer(title: str, body: str) -> str:
    if not os.environ.get("OPENAI_API_KEY"):
        summary = " ".join(str(body).split()[:35])
        return f"{title}. {summary}" if summary else title

    system = (
        "Write one crisp headline sentence for a news bulletin intro, readable in about 10 seconds. "
        "Return only the sentence."
    )
    user = f"Headline: {title}\n\nSummary: {body[:2000]}"
    return _chat(system, user, timeout=60)


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
        raise LocalizationError(f"OpenAI returned an empty {target_language} translation")
    if translated.strip() == text.strip():
        raise LocalizationError(f"OpenAI returned unchanged source text for {target_language}")
    return translated


def batch_headline_intro(primers: list[str]) -> str:
    """Combine primers into a headlines segment (~45-60s)."""
    if not primers:
        return ""
    trimmed = [p.strip() for p in primers[:10] if p and p.strip()]
    if not trimmed:
        return ""
    joined = " ".join(trimmed)
    if not os.environ.get("OPENAI_API_KEY"):
        return joined[:1500]

    system = (
        "Combine these story headline lines into one smooth spoken headlines segment "
        "for a news podcast intro. Keep all stories mentioned. Target 45-60 seconds when read aloud. "
        "Return only the script."
    )
    return _chat(system, joined, timeout=90)
