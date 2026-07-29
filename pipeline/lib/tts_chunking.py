"""Split long TTS scripts into request-sized chunks without dropping content.

ElevenLabs caps the text of a single text-to-speech request. The cap is per
request, not per segment, so a script longer than the cap must be rendered as
several requests and concatenated — never truncated.
"""

from __future__ import annotations

import re

ELEVENLABS_MAX_REQUEST_CHARS = 9500

# Break after sentence-ending punctuation (plus any closing quote/bracket)
# followed by whitespace, so chunk edges land where a reader would pause.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?\u2026])[\"'\u2019\u201d)\]]*\s+")


def _split_sentences(text: str) -> list[str]:
    return [part for part in (p.strip() for p in _SENTENCE_BOUNDARY.split(text)) if part]


def _split_oversized(sentence: str, limit: int) -> list[str]:
    """Break one over-limit sentence on word boundaries, keeping every word."""
    pieces: list[str] = []
    current = ""
    for word in sentence.split():
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > limit:
            pieces.append(current)
            current = word
        else:
            current = candidate
        # A single word longer than the limit has no boundary to respect.
        while len(current) > limit:
            pieces.append(current[:limit])
            current = current[limit:]
    if current:
        pieces.append(current)
    return pieces


def chunk_text_for_tts(
    text: str, *, limit: int = ELEVENLABS_MAX_REQUEST_CHARS
) -> list[str]:
    """Return ordered chunks of at most ``limit`` characters covering all of ``text``.

    Text that already fits is returned unchanged so existing cache keys, which
    are derived from the rendered text, stay stable.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")

    original = str(text or "").strip()
    if not original:
        return []
    if len(original) <= limit:
        return [original]

    collapsed = " ".join(original.split())
    if len(collapsed) <= limit:
        return [collapsed]

    chunks: list[str] = []
    current = ""
    for sentence in _split_sentences(collapsed):
        fragments = (
            [sentence] if len(sentence) <= limit else _split_oversized(sentence, limit)
        )
        for fragment in fragments:
            candidate = f"{current} {fragment}".strip()
            if current and len(candidate) > limit:
                chunks.append(current)
                current = fragment
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks
