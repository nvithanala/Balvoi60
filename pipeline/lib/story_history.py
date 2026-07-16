"""Track which stories have recently aired so cycles don't repeat them."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from balvoi.dates import parse_iso_timestamp
from balvoi.paths import storage_root


def recently_used_story_ids(cooldown_minutes: int) -> set[str]:
    """Return story IDs aired within the last ``cooldown_minutes`` (any edition).

    Scans the per-run manifests in ``storage/manifests/runs`` and unions the
    ``storyIds`` of every run whose timestamp falls inside the cooldown window.
    A cooldown of 0 (or less) disables exclusion entirely.
    """
    if cooldown_minutes <= 0:
        return set()

    runs_dir = storage_root() / "manifests" / "runs"
    if not runs_dir.exists():
        return set()

    cutoff = datetime.now(UTC).timestamp() - cooldown_minutes * 60
    used: set[str] = set()

    for path in runs_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if parse_iso_timestamp(data.get("timestamp")) < cutoff:
            continue
        for sid in data.get("storyIds", []) or []:
            if sid:
                used.add(str(sid))

    return used
