"""Read-only access to config + storage produced by the pipeline."""

from __future__ import annotations

import json
from pathlib import Path

from balvoi.config import edition_by_slug, editions, master_brand
from balvoi.paths import storage_root

__all__ = [
    "audio_fs_path",
    "audio_size",
    "edition_by_slug",
    "editions",
    "episode_by_id",
    "episodes_dir",
    "history",
    "history_for",
    "history_for_feed",
    "latest_for",
    "latest_map",
    "master_brand",
    "status",
]


def _manifests_dir() -> Path:
    return storage_root() / "manifests"


def _episodes_dir() -> Path:
    return storage_root() / "episodes"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def latest_map() -> dict:
    return _read_json(_manifests_dir() / "latest.json", {})


def history() -> list[dict]:
    return _read_json(_manifests_dir() / "history.json", [])


def status() -> dict:
    return _read_json(_manifests_dir() / "status.json", {"lastRunId": None})


def latest_for(slug: str) -> dict | None:
    return latest_map().get(slug)


def history_for(slug: str) -> list[dict]:
    return [e for e in history() if e.get("slug") == slug]


def history_for_feed(slug: str) -> list[dict]:
    """Return only legacy/current episodes with a real non-empty enclosure."""
    return [episode for episode in history_for(slug) if audio_size(episode) > 0]


def episode_by_id(episode_id: str) -> dict | None:
    return next((e for e in history() if e.get("id") == episode_id), None)


def episodes_dir() -> Path:
    return _episodes_dir()


def audio_fs_path(episode: dict) -> Path:
    """Map an episode's audioUrl (/episodes/<run>/<slug>.mp3) to a file path."""
    rel = str(episode.get("audioUrl", "")).lstrip("/")
    return storage_root() / rel


def audio_size(episode: dict) -> int:
    path = audio_fs_path(episode)
    try:
        return path.stat().st_size
    except OSError:
        return 0
