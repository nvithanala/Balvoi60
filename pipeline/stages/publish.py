"""Write episode manifests for the API."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from balvoi.dates import format_iso_utc, parse_iso_datetime
from balvoi.paths import storage_root
from pipeline.errors import PublishRejectedError
from pipeline.lib.edition_lock import edition_was_published
from pipeline.stages.merge_audio import validate_publishable_audio


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _atomic_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _restore(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        return
    tmp = path.with_suffix(path.suffix + ".rollback")
    tmp.write_bytes(previous)
    os.replace(tmp, path)


def publish_run(
    run_id: str,
    edition: dict,
    manifest: dict,
    audio_path: Path,
    duration: int,
    stories: list[dict],
    budget: dict | None = None,
    *,
    publication_boundary: datetime | str | None = None,
    minimum_duration_seconds: int = 600,
) -> dict:
    boundary = (
        parse_iso_datetime(publication_boundary)
        if isinstance(publication_boundary, str)
        else publication_boundary
    )
    if boundary is None:
        raise PublishRejectedError("Publication boundary is required")

    slug = edition["slug"]
    try:
        audio_size = validate_publishable_audio(audio_path, duration, minimum_duration_seconds)
    except Exception as err:
        raise PublishRejectedError(f"Publication rejected: {err}") from err
    if edition_was_published(boundary, slug):
        raise PublishRejectedError("Publication rejected: already_published")

    rel_audio = f"/episodes/{run_id}/{slug}.mp3"
    dest = storage_root() / "episodes" / run_id / f"{slug}.mp3"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if audio_path.resolve() != dest.resolve():
        tmp_audio = dest.with_suffix(".mp3.tmp")
        tmp_audio.write_bytes(audio_path.read_bytes())
        os.replace(tmp_audio, dest)
    if not dest.exists() or dest.stat().st_size != audio_size:
        raise PublishRejectedError("Publication rejected: final audio copy validation failed")

    episode = {
        "id": f"{run_id}-{slug}",
        "runId": run_id,
        "publicationBoundary": format_iso_utc(boundary),
        "editionId": edition["id"],
        "slug": slug,
        "name": edition["name"],
        "editionName": edition["editionName"],
        "city": edition["city"],
        "colors": edition["colors"],
        "timestamp": datetime.now(UTC).isoformat(),
        "audioUrl": rel_audio,
        "durationSeconds": duration,
        "anchor": manifest["voice"]["name"],
        "storyIds": manifest["storyIds"],
        "headlines": [s["title"] for s in stories],
        "picks": manifest["picks"],
        "budget": budget,
    }

    root = storage_root()
    runs_dir = root / "manifests" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_path = runs_dir / f"{run_id}-{slug}.json"

    latest_path = root / "manifests" / "latest.json"
    latest: dict = _read_json(latest_path, {})
    latest[slug] = episode

    history_path = root / "manifests" / "history.json"
    history: list = _read_json(history_path, [])
    history = [episode] + [h for h in history if h.get("id") != episode["id"]]

    status_path = root / "manifests" / "status.json"
    status = {"lastRunId": run_id, "lastSuccess": datetime.now(UTC).isoformat()}
    updates = [
        (latest_path, latest),
        (history_path, history[:200]),
        (run_path, episode),
        (status_path, status),
    ]
    previous = {path: path.read_bytes() if path.exists() else None for path, _ in updates}
    written: list[Path] = []
    try:
        for path, payload in updates:
            _atomic_json(path, payload)
            written.append(path)
    except OSError as err:
        for path in reversed(written):
            _restore(path, previous[path])
        raise PublishRejectedError(
            f"Publication metadata transaction failed: {type(err).__name__}"
        ) from err

    return episode
