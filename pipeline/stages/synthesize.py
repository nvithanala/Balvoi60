"""Synthesize TTS segments and resolve audio file paths."""

from __future__ import annotations

import hashlib
import os
from contextlib import suppress
from pathlib import Path

from balvoi.paths import ROOT
from pipeline.lib import reusable_audio_cache as reusable_cache
from pipeline.lib.edition_voice_validation import (
    log_cache_validation,
    log_segment_voice,
    text_hash,
)
from pipeline.lib.elevenlabs_client import synthesize
from pipeline.lib.storage_paths import get_storage_paths
from pipeline.lib.tts_chunking import ELEVENLABS_MAX_REQUEST_CHARS, chunk_text_for_tts
from pipeline.stages.merge_audio import merge_segments


def _log(message: str) -> None:
    """Print without crashing on Windows consoles that cannot encode some scripts."""
    try:
        print(message)
    except UnicodeEncodeError:
        print(message.encode("ascii", "replace").decode("ascii"))


def _dynamic_tts_path(
    slug: str,
    text: str,
    voice_id: str,
    *,
    cache_root: Path | None = None,
) -> Path:
    digest = hashlib.sha256(f"{voice_id or 'default'}:{text}".encode()).hexdigest()[:16]
    root = Path(cache_root) if cache_root is not None else get_storage_paths().tts_cache_root
    return root / slug / f"{digest}.mp3"


def _synthesize_full_text(text: str, voice_id: str, out_path: Path) -> Path:
    """Render the whole script, spreading over-limit text across several requests.

    Rendered part files are kept when a request or the merge fails so a retry
    reuses already-paid-for audio instead of re-synthesizing every chunk.
    """
    chunks = chunk_text_for_tts(text)
    if len(chunks) <= 1:
        return synthesize(chunks[0] if chunks else text, voice_id, out_path)

    _log(
        f"  [tts] {len(text)} chars exceeds the {ELEVENLABS_MAX_REQUEST_CHARS}-char "
        f"request limit — rendering {len(chunks)} chunks in full"
    )
    parts_dir = out_path.parent / f"{out_path.stem}.parts"
    parts_dir.mkdir(parents=True, exist_ok=True)

    part_paths: list[Path] = []
    for index, chunk in enumerate(chunks):
        part = parts_dir / f"{index:03d}.mp3"
        if not (part.exists() and part.stat().st_size > 0):
            synthesize(chunk, voice_id, part)
        part_paths.append(part)

    merge_segments(part_paths, out_path)
    for part in part_paths:
        part.unlink(missing_ok=True)
    with suppress(OSError):
        parts_dir.rmdir()
    return out_path


def render_segments(
    manifest: dict,
    dry_run: bool = False,
    *,
    tts_cache_root: Path | None = None,
    reusable_write_root: Path | None = None,
    reusable_read_roots: list[Path | None] | None = None,
    diagnostics: bool = False,
) -> tuple[list[Path], dict]:
    slug = manifest["slug"]
    voice_id = manifest["voice"]["voiceId"]
    anchor_name = manifest["voice"].get("name", "")
    edition_id = manifest["editionId"]
    # Cache language token = edition slug (matches prerender path layout).
    language = slug
    read_roots = reusable_read_roots if reusable_read_roots is not None else [reusable_write_root]
    stats = {"cache_hits": 0, "cache_misses": 0, "live_tts_voice_ids": set(), "prerecorded": 0}

    paths: list[Path] = []
    for seg in manifest["segments"]:
        if seg["type"] == "audio":
            src = ROOT / seg["path"]
            if not src.exists() or src.stat().st_size <= 0:
                raise RuntimeError(f"Required prerecorded audio is missing or empty: {src}")
            paths.append(src)
            stats["prerecorded"] += 1
            if diagnostics:
                log_segment_voice(
                    edition_id=edition_id,
                    segment_type=str(seg.get("segmentType") or "audio"),
                    source="prerecorded_audio",
                    language=language,
                    anchor_name=anchor_name,
                    voice_id="",
                    cache_key="",
                    cache_hit=False,
                    text=str(seg.get("path") or ""),
                )
            continue

        text = seg.get("text", "").strip()
        if not text:
            continue

        segment_type = str(seg.get("segmentType") or "tts")
        label = segment_type
        stats["live_tts_voice_ids"].add(voice_id)

        if seg.get("reusable"):
            payload = reusable_cache.build_cache_payload(
                edition_id=edition_id,
                language=language,
                anchor_name=anchor_name,
                voice_id=voice_id or "",
                segment_type=str(seg.get("sheet") or seg["segmentType"]),
                variant_id=seg.get("variant") if seg.get("variant") is not None else 0,
                text=text,
            )
            cache_key = reusable_cache.compute_cache_key(payload)
            requested_hash = reusable_cache.payload_text_hash(payload)
            hit = reusable_cache.lookup(payload, roots=read_roots)
            if hit is not None:
                sidecar = hit.with_suffix(".json")
                meta = reusable_cache.read_sidecar_metadata(sidecar)
                stored_hash = str(meta.get("text_hash") or "") or text_hash(str(meta.get("text") or ""))
                valid = reusable_cache.metadata_matches_payload(meta, payload)
                if diagnostics:
                    log_cache_validation(
                        edition_id=edition_id,
                        language=language,
                        anchor_name=anchor_name,
                        voice_id=voice_id or "",
                        segment_type=str(payload["segment_type"]),
                        variant=str(payload["variant_id"]),
                        stored_text_hash=stored_hash,
                        requested_text_hash=requested_hash,
                        valid=valid,
                    )
                if not valid:
                    hit = None
                else:
                    stats["cache_hits"] += 1
                    _log(
                        f"  [cache] hit edition={slug} anchor={anchor_name!r} "
                        f"segment={payload['segment_type']} variant={payload['variant_id']}"
                    )
                    if diagnostics:
                        log_segment_voice(
                            edition_id=edition_id,
                            segment_type=segment_type,
                            source="reusable_cache",
                            language=language,
                            anchor_name=anchor_name,
                            voice_id=voice_id or "",
                            cache_key=cache_key,
                            cache_hit=True,
                            text=text,
                        )
                    paths.append(hit)
                    continue

            if dry_run or not voice_id or not os.environ.get("ELEVENLABS_API_KEY"):
                _log(f"  [dry-run/tts-skip] {label}: {text[:60]}...")
                continue

            mp3_path, _sidecar = reusable_cache.cache_paths(
                payload, root=reusable_write_root, create=True
            )
            stats["cache_misses"] += 1
            _log(
                f"  [cache] miss → generate edition={slug} anchor={anchor_name!r} "
                f"segment={payload['segment_type']} variant={payload['variant_id']}"
            )
            if diagnostics:
                log_segment_voice(
                    edition_id=edition_id,
                    segment_type=segment_type,
                    source="elevenlabs_reusable",
                    language=language,
                    anchor_name=anchor_name,
                    voice_id=voice_id or "",
                    cache_key=cache_key,
                    cache_hit=False,
                    text=text,
                )
            _synthesize_full_text(text, voice_id, mp3_path)
            reusable_cache.write_sidecar(payload, root=reusable_write_root)
            paths.append(mp3_path)
            continue

        # Dynamic / one-off TTS (intro_dynamic, headlines, stories).
        cache = _dynamic_tts_path(slug, text, voice_id or "default", cache_root=tts_cache_root)
        dynamic_key = hashlib.sha256(f"{voice_id or 'default'}:{text}".encode()).hexdigest()
        if cache.exists() and cache.stat().st_size > 0:
            stats["cache_hits"] += 1
            if diagnostics:
                log_segment_voice(
                    edition_id=edition_id,
                    segment_type=segment_type,
                    source="dynamic_tts_cache",
                    language=language,
                    anchor_name=anchor_name,
                    voice_id=voice_id or "",
                    cache_key=dynamic_key,
                    cache_hit=True,
                    text=text,
                )
            paths.append(cache)
            continue

        if dry_run or not voice_id or not os.environ.get("ELEVENLABS_API_KEY"):
            _log(f"  [dry-run/tts-skip] {label}: {text[:60]}...")
            continue

        stats["cache_misses"] += 1
        _log(f"  [tts] {label}: {text[:50]}...")
        if diagnostics:
            log_segment_voice(
                edition_id=edition_id,
                segment_type=segment_type,
                source="elevenlabs_dynamic",
                language=language,
                anchor_name=anchor_name,
                voice_id=voice_id or "",
                cache_key=dynamic_key,
                cache_hit=False,
                text=text,
            )
        _synthesize_full_text(text, voice_id, cache)
        paths.append(cache)

    stats["live_tts_voice_ids"] = sorted(stats["live_tts_voice_ids"])
    return paths, stats
