"""Synthesize TTS segments and resolve audio file paths."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from balvoi.paths import ROOT, storage_root
from pipeline.lib.elevenlabs_client import synthesize


def _cache_path(slug: str, text: str, voice_id: str) -> Path:
    h = hashlib.sha256(f"{voice_id}:{text}".encode()).hexdigest()[:16]
    return storage_root() / "cache" / "tts" / slug / f"{h}.mp3"


def render_segments(manifest: dict, dry_run: bool = False) -> list[Path]:
    slug = manifest["slug"]
    voice_id = manifest["voice"]["voiceId"]
    tmp_dir = storage_root() / "episodes" / "_tmp" / slug
    tmp_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    for i, seg in enumerate(manifest["segments"]):
        if seg["type"] == "audio":
            src = ROOT / seg["path"]
            if not src.exists():
                print(f"  [warn] missing audio {src}")
                continue
            paths.append(src)
            continue

        text = seg.get("text", "").strip()
        if not text:
            continue

        cache = _cache_path(slug, text, voice_id or "default")

        if cache.exists():
            paths.append(cache)
            continue

        if dry_run or not voice_id or not os.environ.get("ELEVENLABS_API_KEY"):
            print(f"  [dry-run/tts-skip] {seg['segmentType']}: {text[:60]}...")
            continue

        if len(text) > 9500:
            text = text[:9500].rsplit(" ", 1)[0] + "..."
            seg = {**seg, "text": text}

        print(f"  [tts] {seg['segmentType']}: {text[:50]}...")
        synthesize(text, voice_id, cache)
        paths.append(cache)

    return paths
