"""Deterministic reusable ElevenLabs audio cache with sidecar metadata."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from balvoi.paths import storage_root
from pipeline.lib.elevenlabs_client import DEFAULT_VOICE_SETTINGS, MODEL_ID

# Sheets whose TTS variants are eligible for reusable caching.
REUSABLE_SHEETS = ("welcome", "started", "right_back", "welcome_back", "thank_you")


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def build_cache_payload(
    *,
    edition_id: str,
    language: str,
    anchor_name: str,
    voice_id: str,
    segment_type: str,
    variant_id: str | int,
    text: str,
    model_id: str | None = None,
    voice_settings: dict | None = None,
) -> dict[str, Any]:
    """Build the canonical identity for a cached reusable clip."""
    vid = variant_id if isinstance(variant_id, str) else f"{int(variant_id):02d}"
    return {
        "edition_id": edition_id,
        "language": language,
        "anchor_name": anchor_name,
        "voice_id": voice_id,
        "segment_type": segment_type,
        "variant_id": vid,
        "text": text,
        "model_id": model_id or MODEL_ID,
        "voice_settings": voice_settings or dict(DEFAULT_VOICE_SETTINGS),
    }


def compute_cache_key(payload: dict[str, Any]) -> str:
    """Return a stable sha256 hex digest for ``payload``."""
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def anchor_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")
    return slug or "anchor"


def reusable_root(root: Path | None = None, *, create: bool = True) -> Path:
    path = Path(root) if root is not None else storage_root() / "audio_assets" / "reusable"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def cache_paths(
    payload: dict[str, Any],
    cache_key: str | None = None,
    *,
    root: Path | None = None,
    create: bool = True,
) -> tuple[Path, Path]:
    """Return ``(mp3_path, sidecar_path)`` for a cache payload."""
    key = cache_key or compute_cache_key(payload)
    short = key[:12]
    language = str(payload["language"]).lower()
    # Prefer edition slug-like language code when present (en/es/…); fall back safely.
    edition_part = language
    segment = str(payload["segment_type"])
    variant = str(payload["variant_id"])
    directory = (
        reusable_root(root, create=create)
        / edition_part
        / anchor_slug(str(payload["anchor_name"]))
        / segment
    )
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    stem = f"{variant}_{short}"
    return directory / f"{stem}.mp3", directory / f"{stem}.json"


def write_atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_atomic_json(path: Path, obj: dict[str, Any]) -> None:
    data = json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8")
    write_atomic_bytes(path, data)


def is_valid_hit(mp3_path: Path, sidecar_path: Path, expected_key: str) -> bool:
    """True when a finished MP3 and matching sidecar exist (never trusts ``*.tmp``)."""
    if mp3_path.suffix == ".tmp" or sidecar_path.suffix == ".tmp":
        return False
    if str(mp3_path).endswith(".tmp") or str(sidecar_path).endswith(".tmp"):
        return False
    if not mp3_path.exists() or not sidecar_path.exists():
        return False
    if mp3_path.stat().st_size <= 0:
        return False
    try:
        meta = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return meta.get("cache_key") == expected_key


def lookup(
    payload: dict[str, Any],
    *,
    roots: list[Path | None] | None = None,
) -> Path | None:
    """Return the cached MP3 path on a valid hit, else ``None``.

    Lookup never creates directories. ``roots`` defaults to the production reusable root.
    """
    key = compute_cache_key(payload)
    search_roots: list[Path | None] = roots if roots is not None else [None]
    for root in search_roots:
        mp3_path, sidecar_path = cache_paths(payload, key, root=root, create=False)
        if is_valid_hit(mp3_path, sidecar_path, key):
            return mp3_path
    return None


def write_sidecar(payload: dict[str, Any], *, root: Path | None = None) -> Path:
    """Write/replace sidecar for an already-written MP3. Returns the MP3 path."""
    key = compute_cache_key(payload)
    mp3_path, sidecar_path = cache_paths(payload, key, root=root, create=True)
    try:
        relative = str(mp3_path.relative_to(storage_root())).replace("\\", "/")
    except ValueError:
        relative = str(mp3_path).replace("\\", "/")
    sidecar = {
        "cache_key": key,
        **payload,
        "file": relative,
    }
    write_atomic_json(sidecar_path, sidecar)
    return mp3_path


def save_cached_audio(payload: dict[str, Any], audio: bytes, *, root: Path | None = None) -> Path:
    """Atomically write MP3 + sidecar metadata; create directories as needed."""
    key = compute_cache_key(payload)
    mp3_path, _sidecar_path = cache_paths(payload, key, root=root, create=True)
    write_atomic_bytes(mp3_path, audio)
    write_sidecar(payload, root=root)
    return mp3_path


def primary_anchors_for_edition(edition: dict) -> list[dict]:
    """Unique primary anchors with a non-null voiceId across all voice shifts."""
    seen: set[str] = set()
    out: list[dict] = []
    for shift in edition.get("voiceShifts") or []:
        primary = shift.get("primary") or {}
        voice_id = primary.get("voiceId")
        name = primary.get("name")
        if not voice_id or not name:
            continue
        dedupe = f"{name}::{voice_id}"
        if dedupe in seen:
            continue
        seen.add(dedupe)
        out.append({"name": name, "voiceId": voice_id})
    return out


def language_for_edition(edition: dict) -> str:
    """Stable language token for cache paths (edition slug)."""
    return str(edition.get("slug") or edition.get("locale") or "xx")


def reusable_variant_jobs(
    edition: dict,
    segments_doc: dict,
    assets_doc: dict | None = None,
) -> list[dict[str, Any]]:
    """Enumerate reusable TTS jobs for one edition (all primary shift anchors).

    Skips ``right_back`` when ``assets.json`` already provides prerecorded clips.
    """
    slug = edition["slug"]
    edition_id = edition["id"]
    language = language_for_edition(edition)
    assets_for = (assets_doc or {}).get(edition_id) or {}

    sheets: list[str] = []
    for sheet in REUSABLE_SHEETS:
        if sheet == "right_back" and assets_for.get("right_back"):
            continue
        sheets.append(sheet)

    jobs: list[dict[str, Any]] = []
    for anchor in primary_anchors_for_edition(edition):
        for sheet in sheets:
            variants = (segments_doc.get(sheet) or {}).get(slug) or []
            for idx, text in enumerate(variants):
                cleaned = str(text or "").strip()
                if not cleaned:
                    continue
                jobs.append(
                    {
                        "edition_id": edition_id,
                        "language": language,
                        "anchor_name": anchor["name"],
                        "voice_id": anchor["voiceId"],
                        "segment_type": sheet,
                        "variant_id": f"{idx:02d}",
                        "text": cleaned,
                    }
                )
    return jobs
