"""Voice/localization diagnostics and fail-closed edition validation."""

from __future__ import annotations

import hashlib
import re
import threading
from collections import Counter
from dataclasses import dataclass, field

from balvoi.config import is_english

# Common English tokens that should not appear in non-English live TTS.
_ENGLISH_DYNAMIC_MARKERS = (
    "it's ",
    "i'm ",
    "here are our latest stories",
    " on the ",
    " of january ",
    " of february ",
    " of march ",
    " of april ",
    " of may ",
    " of june ",
    " of july ",
    " of august ",
    " of september ",
    " of october ",
    " of november ",
    " of december ",
)


def text_hash(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def looks_like_english_dynamic_intro(text: str) -> bool:
    lowered = f" {str(text).strip().lower()} "
    return any(marker in lowered for marker in _ENGLISH_DYNAMIC_MARKERS)


@dataclass
class EditionRunTracker:
    """Detect duplicate edition starts/completions within one orchestrated run."""

    expected_slugs: list[str]
    _lock: threading.Lock = field(default_factory=threading.Lock)
    started: Counter[str] = field(default_factory=Counter)
    completed: Counter[str] = field(default_factory=Counter)

    def mark_start(self, slug: str) -> None:
        with self._lock:
            self.started[slug] += 1
            if self.started[slug] > 1:
                raise RuntimeError(f"Edition {slug} started more than once in this run")

    def mark_complete(self, slug: str) -> None:
        with self._lock:
            self.completed[slug] += 1
            if self.completed[slug] > 1:
                raise RuntimeError(f"Edition {slug} completed more than once in this run")

    def assert_unique_completion(self) -> None:
        expected = list(self.expected_slugs)
        started = sorted(self.started)
        completed = sorted(slug for slug, count in self.completed.items() if count)
        if started != sorted(expected):
            raise RuntimeError(
                f"Edition start set mismatch: expected={sorted(expected)} started={started}"
            )
        if completed != sorted(expected):
            raise RuntimeError(
                f"Edition completion set mismatch: expected={sorted(expected)} completed={completed}"
            )
        if len(completed) != len(set(expected)):
            raise RuntimeError(
                f"Expected {len(set(expected))} unique editions to complete, got {len(completed)}"
            )


def validate_live_tts_segments(
    *,
    edition: dict,
    manifest: dict,
    language: str,
) -> list[str]:
    """Return validation errors for live TTS voice/language consistency."""
    errors: list[str] = []
    expected_voice = str((manifest.get("voice") or {}).get("voiceId") or "")
    if not expected_voice:
        errors.append("manifest is missing voice.voiceId")
    live_voice_ids: set[str] = set()
    for segment in manifest.get("segments") or []:
        if segment.get("type") != "tts":
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        live_voice_ids.add(expected_voice)
        segment_type = str(segment.get("segmentType") or "")
        if not is_english(language) and segment_type == "intro_dynamic":
            if looks_like_english_dynamic_intro(text):
                errors.append(
                    f"{edition.get('slug')}: intro_dynamic remains English: {text[:80]!r}"
                )
    if len(live_voice_ids) > 1:
        errors.append(
            f"{edition.get('slug')}: multiple live TTS voice IDs in one edition: {sorted(live_voice_ids)}"
        )
    return errors


def log_edition_start(
    *,
    edition_id: str,
    language: str,
    anchor_name: str,
    voice_id: str,
    timezone: str,
    shift: str,
) -> None:
    print(
        "[edition_start]\n"
        f"edition_id={edition_id}\n"
        f"language={language}\n"
        f"anchor_name={anchor_name}\n"
        f"voice_id={voice_id}\n"
        f"timezone={timezone}\n"
        f"shift={shift}"
    )


def log_segment_voice(
    *,
    edition_id: str,
    segment_type: str,
    source: str,
    language: str,
    anchor_name: str,
    voice_id: str,
    cache_key: str,
    cache_hit: bool,
    text: str,
) -> None:
    preview = re.sub(r"\s+", " ", str(text)).strip()[:80]
    print(
        "[segment_voice]\n"
        f"edition_id={edition_id}\n"
        f"segment_type={segment_type}\n"
        f"source={source}\n"
        f"language={language}\n"
        f"anchor_name={anchor_name}\n"
        f"voice_id={voice_id}\n"
        f"cache_key={cache_key}\n"
        f"cache_hit={str(cache_hit).lower()}\n"
        f"text_hash={text_hash(text)}\n"
        f"text_preview={preview}"
    )


def log_edition_end(*, edition_id: str, duration_seconds: int, output_path: str) -> None:
    print(
        "[edition_end]\n"
        f"edition_id={edition_id}\n"
        f"duration_seconds={duration_seconds}\n"
        f"output_path={output_path}"
    )


def log_cache_validation(
    *,
    edition_id: str,
    language: str,
    anchor_name: str,
    voice_id: str,
    segment_type: str,
    variant: str,
    stored_text_hash: str,
    requested_text_hash: str,
    valid: bool,
) -> None:
    print(
        "[cache_validation]\n"
        f"edition_id={edition_id}\n"
        f"language={language}\n"
        f"anchor_name={anchor_name}\n"
        f"voice_id={voice_id}\n"
        f"segment_type={segment_type}\n"
        f"variant={variant}\n"
        f"stored_text_hash={stored_text_hash}\n"
        f"requested_text_hash={requested_text_hash}\n"
        f"valid={str(valid).lower()}"
    )
