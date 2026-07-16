"""Write episode manifests for the API."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from balvoi.paths import storage_root


def publish_run(
    run_id: str,
    edition: dict,
    manifest: dict,
    audio_path: Path,
    duration: int,
    stories: list[dict],
    budget: dict | None = None,
) -> dict:
    slug = edition["slug"]
    rel_audio = f"/episodes/{run_id}/{slug}.mp3"
    dest = storage_root() / "episodes" / run_id / f"{slug}.mp3"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if audio_path.exists() and audio_path.stat().st_size > 0:
        dest.write_bytes(audio_path.read_bytes())

    episode = {
        "id": f"{run_id}-{slug}",
        "runId": run_id,
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
    (runs_dir / f"{run_id}-{slug}.json").write_text(
        json.dumps(episode, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    latest_path = root / "manifests" / "latest.json"
    latest: dict = {}
    if latest_path.exists():
        try:
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            latest = {}

    latest[slug] = episode
    latest_path.write_text(json.dumps(latest, indent=2, ensure_ascii=False), encoding="utf-8")

    history_path = root / "manifests" / "history.json"
    history: list = []
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            history = []

    history = [episode] + [h for h in history if h.get("id") != episode["id"]]
    history_path.write_text(
        json.dumps(history[:200], indent=2, ensure_ascii=False), encoding="utf-8"
    )

    status_path = root / "manifests" / "status.json"
    status = {"lastRunId": run_id, "lastSuccess": datetime.now(UTC).isoformat()}
    status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")

    return episode
