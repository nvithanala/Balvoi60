"""Synthesize TTS segments and resolve audio file paths."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from balvoi.paths import ROOT, storage_root
from pipeline.lib import reusable_audio_cache as reusable_cache
from pipeline.lib.elevenlabs_client import synthesize


def _dynamic_tts_path(
    slug: str,
    text: str,
    voice_id: str,
    *,
    cache_root: Path | None = None,
) -> Path:
    digest = hashlib.sha256(f"{voice_id or 'default'}:{text}".encode()).hexdigest()[:16]
    root = Path(cache_root) if cache_root is not None else storage_root() / "cache" / "tts"
    return root / slug / f"{digest}.mp3"


def render_segments(
    manifest: dict,
    dry_run: bool = False,
    *,
    tts_cache_root: Path | None = None,
    reusable_write_root: Path | None = None,
    reusable_read_roots: list[Path | None] | None = None,
) -> list[Path]:
    slug = manifest["slug"]
    voice_id = manifest["voice"]["voiceId"]
    anchor_name = manifest["voice"].get("name", "")
    # Cache language token = edition slug (matches prerender path layout).
    language = slug
    read_roots = reusable_read_roots if reusable_read_roots is not None else [reusable_write_root]

    paths: list[Path] = []
    for seg in manifest["segments"]:
        if seg["type"] == "audio":
            src = ROOT / seg["path"]
            if not src.exists() or src.stat().st_size <= 0:
                raise RuntimeError(f"Required prerecorded audio is missing or empty: {src}")
            paths.append(src)
            continue

        text = seg.get("text", "").strip()
        if not text:
            continue

        label = seg.get("sheet") or seg.get("segmentType") or "tts"

        if seg.get("reusable"):
            payload = reusable_cache.build_cache_payload(
                edition_id=manifest["editionId"],
                language=language,
                anchor_name=anchor_name,
                voice_id=voice_id or "",
                segment_type=str(seg.get("sheet") or seg["segmentType"]),
                variant_id=seg.get("variant") if seg.get("variant") is not None else 0,
                text=text,
            )
            hit = reusable_cache.lookup(payload, roots=read_roots)
            if hit is not None:
                print(
                    f"  [cache] hit edition={slug} anchor={anchor_name!r} "
                    f"segment={payload['segment_type']} variant={payload['variant_id']}"
                )
                paths.append(hit)
                continue

            if dry_run or not voice_id or not os.environ.get("ELEVENLABS_API_KEY"):
                print(f"  [dry-run/tts-skip] {label}: {text[:60]}...")
                continue

            if len(text) > 9500:
                print(
                    f"  [warn] {seg['segmentType']} exceeds ElevenLabs limit "
                    f"({len(text)} chars) — truncating for TTS"
                )
                text = text[:9500].rsplit(" ", 1)[0] + "..."
                payload = reusable_cache.build_cache_payload(
                    edition_id=manifest["editionId"],
                    language=language,
                    anchor_name=anchor_name,
                    voice_id=voice_id,
                    segment_type=str(seg.get("sheet") or seg["segmentType"]),
                    variant_id=seg.get("variant") if seg.get("variant") is not None else 0,
                    text=text,
                )

            mp3_path, _sidecar = reusable_cache.cache_paths(
                payload, root=reusable_write_root, create=True
            )
            print(
                f"  [cache] miss → generate edition={slug} anchor={anchor_name!r} "
                f"segment={payload['segment_type']} variant={payload['variant_id']}"
            )
            synthesize(text, voice_id, mp3_path)
            reusable_cache.write_sidecar(payload, root=reusable_write_root)
            paths.append(mp3_path)
            continue

        # Dynamic / one-off TTS (intro_dynamic, headlines, stories).
        cache = _dynamic_tts_path(slug, text, voice_id or "default", cache_root=tts_cache_root)
        if cache.exists() and cache.stat().st_size > 0:
            paths.append(cache)
            continue

        if dry_run or not voice_id or not os.environ.get("ELEVENLABS_API_KEY"):
            print(f"  [dry-run/tts-skip] {label}: {text[:60]}...")
            continue

        if len(text) > 9500:
            print(
                f"  [warn] {seg['segmentType']} exceeds ElevenLabs limit "
                f"({len(text)} chars) — truncating for TTS"
            )
            text = text[:9500].rsplit(" ", 1)[0] + "..."
            cache = _dynamic_tts_path(slug, text, voice_id or "default", cache_root=tts_cache_root)

        print(f"  [tts] {label}: {text[:50]}...")
        synthesize(text, voice_id, cache)
        paths.append(cache)

    return paths
