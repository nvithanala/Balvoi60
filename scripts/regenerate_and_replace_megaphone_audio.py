#!/usr/bin/env python3
"""Regenerate one English run's MP3 and replace audio on its existing Megaphone episode.

Bypasses create/preflight (boundary is already published). Does not create a second
Megaphone episode. Requires --confirm-live-replace to PUT.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv  # noqa: E402

from balvoi.dates import parse_iso_datetime  # noqa: E402
from balvoi.paths import ROOT  # noqa: E402

load_dotenv(ROOT / ".env", override=True)

from pipeline.config_loader import edition_by_slug  # noqa: E402
from pipeline.lib.megaphone_client import (  # noqa: E402
    replace_episode_media,
    verify_media_file_url,
)
from pipeline.lib.megaphone_once import (  # noqa: E402
    load_run_state,
    manifest_artifact_path,
    stories_artifact_path,
)
from pipeline.lib.megaphone_publication_result import (  # noqa: E402
    load_publication_result,
    save_publication_result,
)
from pipeline.lib.openai_client import story_primer  # noqa: E402
from pipeline.lib.publication_identity import PublicationIdentity  # noqa: E402
from pipeline.lib.storage_paths import get_storage_paths  # noqa: E402
from pipeline.stages.assemble_episode import assemble_episode  # noqa: E402
from pipeline.stages.merge_audio import (  # noqa: E402
    duration_seconds,
    merge_segments,
    validate_publishable_audio,
)
from pipeline.stages.synthesize import render_segments  # noqa: E402
from pipeline.stages.transform_stories import headlines_segment  # noqa: E402


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _backup_mp3(mp3: Path, *, label: str = "pre_regen") -> Path | None:
    if not mp3.exists():
        return None
    backup = mp3.with_name(f"{mp3.name}.{label}.bak")
    if backup.exists():
        backup.unlink()
    mp3.replace(backup)
    print(f"Backed up MP3 -> {backup}")
    return backup


def _refresh_title_only_primers(stories: list[dict]) -> list[dict]:
    """Rewrite each primer to the title only (no body teaser)."""
    refreshed: list[dict] = []
    for story in stories:
        body = str(story.get("broadcastScript") or story.get("fullText") or "")
        title = str(story.get("title") or "Untitled")
        updated = dict(story)
        updated["primer"] = story_primer(title, body)
        refreshed.append(updated)
    return refreshed


def regenerate_mp3(run_id: str, boundary) -> tuple[Path, int, int]:
    """Rebuild en.mp3 from frozen English stories (title-only headlines, full TTS)."""
    paths = get_storage_paths()
    mp3 = paths.episode_mp3(run_id, "en")
    _backup_mp3(mp3, label="pre_title_only")

    stories_path = stories_artifact_path(run_id)
    if not stories_path.is_file():
        raise SystemExit(f"Missing frozen stories: {stories_path}")
    stories = list(json.loads(stories_path.read_text(encoding="utf-8")).get("stories") or [])
    if not stories:
        raise SystemExit(f"Frozen stories empty for {run_id}")

    stories = _refresh_title_only_primers(stories)
    _atomic_json(stories_path, {"stories": stories})
    primers = [str(s.get("primer") or "") for s in stories]
    print("Title-only primers:")
    for primer in primers:
        print(f"  - {primer}")

    edition = edition_by_slug("en") or {"id": "balvoi60-en", "name": "BalVoi:60", "slug": "en"}
    headlines = headlines_segment(stories, language="English")
    print(f"Headlines block ({len(headlines)} chars): {headlines[:160]}...")
    manifest = assemble_episode(
        edition,
        stories,
        run_id,
        headlines_text=headlines,
        when=boundary,
    )
    _atomic_json(manifest_artifact_path(run_id), manifest)

    print(f"Rendering {len(manifest.get('segments') or [])} segments (chunked TTS enabled)...")
    seg_paths, stats = render_segments(manifest, dry_run=False)
    print(
        f"TTS done: files={len(seg_paths)} "
        f"cache_hits={stats.get('cache_hits')} cache_misses={stats.get('cache_misses')}"
    )
    merge_segments(seg_paths, mp3)
    duration = duration_seconds(mp3)
    size = validate_publishable_audio(mp3, duration)
    print(f"Merged MP3: {mp3} duration={duration}s size={size}")
    return mp3, duration, size


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Regenerate English MP3 with full TTS (no truncation) and PUT "
            "mediaFileUrl onto the existing Megaphone episode."
        )
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--language", default="en")
    parser.add_argument(
        "--confirm-live-replace",
        action="store_true",
        help="Required to PUT the new mediaFileUrl to Megaphone",
    )
    args = parser.parse_args()
    if args.language.strip().lower() != "en":
        print("Only --language en is supported", file=sys.stderr)
        return 2

    run_id = args.run_id.strip()
    state = load_run_state(run_id)
    episode_id = str(state.get("megaphoneEpisodeId") or "").strip()
    if not episode_id:
        print(f"Run {run_id} has no megaphoneEpisodeId", file=sys.stderr)
        return 1

    boundary = parse_iso_datetime(str(state.get("publicationBoundary") or ""))
    if boundary is None:
        print("Invalid publicationBoundary on run", file=sys.stderr)
        return 1

    public_base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not public_base:
        print("PUBLIC_BASE_URL is empty", file=sys.stderr)
        return 1
    public_url = f"{public_base}/episodes/{run_id}/en.mp3"
    # Force Megaphone to re-fetch when the path is unchanged but bytes changed.
    replace_url = f"{public_url}?v=title-only"

    mp3, duration, size = regenerate_mp3(run_id, boundary)
    print(f"Public URL: {public_url}")
    print(f"Replace URL: {replace_url}")
    verify_media_file_url(public_url)

    if not args.confirm_live_replace:
        print("Stopped before Megaphone PUT (pass --confirm-live-replace to continue).")
        return 2

    payload = replace_episode_media(
        slug="en",
        episode_id=episode_id,
        public_audio_url=replace_url,
        retain_ad_locations=True,
    )
    identity = PublicationIdentity.from_existing(
        boundary=boundary, edition_slug="en", run_id=run_id
    )
    existing = load_publication_result(identity) or {}
    save_publication_result(
        identity,
        megaphone_episode_id=str(payload.get("id") or episode_id),
        media_file_url=replace_url,
        megaphone_response=payload if isinstance(payload, dict) else None,
        source=str(existing.get("source") or "created"),
    )

    state["status"] = "published"
    state["retryable"] = False
    state["failedStage"] = None
    state["mp3Path"] = str(mp3)
    state["publicMp3Url"] = public_url
    state["durationSeconds"] = duration
    state["audioSize"] = size
    state["megaphoneEpisodeId"] = str(payload.get("id") or episode_id)
    _atomic_json(get_storage_paths().run(run_id).state_path, state)

    print()
    print(f"Megaphone episode: {payload.get('id')}")
    print(f"audioFileStatus: {payload.get('audioFileStatus')}")
    print(f"audioFileProcessing: {payload.get('audioFileProcessing')}")
    print(f"duration (immediate): {payload.get('duration')}")
    print(f"draft: {payload.get('draft')}")
    print(f"local duration after regen: {duration}s (was truncated before)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
