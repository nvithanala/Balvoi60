"""Load BalVoi:60 configuration from config/*.json."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from balvoi.config import edition_by_slug, editions
from balvoi.paths import CONFIG_DIR, storage_root

__all__ = [
    "edition_by_slug",
    "editions",
    "assets",
    "ensure_storage",
    "episode_template",
    "get_voice_for_edition",
    "load_json",
    "segments",
]


def load_json(name: str) -> dict | list:
    return json.loads((CONFIG_DIR / name).read_text(encoding="utf-8"))


def segments() -> dict:
    return load_json("segments.json")


def assets() -> dict:
    return load_json("assets.json")


def episode_template() -> dict:
    return load_json("episode-template.json")


def get_voice_for_edition(edition: dict, when: datetime | None = None) -> dict:
    """Pick primary voice based on edition local time shift."""
    try:
        tz = ZoneInfo(edition["timezone"])
    except Exception:
        tz = ZoneInfo("UTC")
    local = (when or datetime.now(tz)).astimezone(tz)
    minutes = local.hour * 60 + local.minute

    def to_minutes(hhmm: str) -> int:
        h, m = map(int, hhmm.split(":"))
        return h * 60 + m

    for shift in edition["voiceShifts"]:
        start = to_minutes(shift["start"])
        end = to_minutes(shift["end"])
        if start <= end:
            in_shift = start <= minutes <= end
        else:
            in_shift = minutes >= start or minutes <= end
        if in_shift:
            return shift["primary"]
    return edition["voiceShifts"][0]["primary"]


def ensure_storage() -> None:
    root = storage_root()
    for sub in ("episodes", "manifests", "cache/tts", "cache/reusable", "logs", "articles"):
        (root / sub).mkdir(parents=True, exist_ok=True)
