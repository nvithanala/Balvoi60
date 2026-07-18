"""Per-edition operational status records without sensitive payloads."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from balvoi.paths import storage_root


def _path(run_id: str, slug: str) -> Path:
    return storage_root() / "manifests" / "status" / f"{run_id}-{slug}.json"


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def record_status(
    *,
    run_id: str,
    boundary: str,
    slug: str,
    stage: str,
    story_ids: list[str] | None = None,
    error: str | None = None,
    output_path: str | None = None,
    audio_size: int | None = None,
    duration: int | None = None,
    elapsed_seconds: float | None = None,
    metrics: dict | None = None,
) -> None:
    path = _path(run_id, slug)
    existing: dict = {}
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    now = datetime.now(UTC).isoformat()
    event = {"stage": stage, "timestamp": now}
    if elapsed_seconds is not None:
        event["elapsedSeconds"] = round(elapsed_seconds, 3)
    events = [*(existing.get("events") or []), event]
    payload = {
        "runId": run_id,
        "publicationBoundary": boundary,
        "language": slug,
        "stage": stage,
        "timestamp": now,
        "storyIds": story_ids or existing.get("storyIds") or [],
        "events": events,
    }
    if error:
        payload["error"] = " ".join(str(error).split())[:500]
    if output_path:
        payload["outputPath"] = output_path
    if audio_size is not None:
        payload["audioSize"] = audio_size
    if duration is not None:
        payload["durationSeconds"] = duration
    if metrics:
        payload["metrics"] = metrics
    _atomic_json(path, payload)
