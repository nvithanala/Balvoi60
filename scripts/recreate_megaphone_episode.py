#!/usr/bin/env python3
"""Recreate a Megaphone episode after the remote episode was deleted manually.

Clears stale local publication markers for one run, then POSTs Create Episode
using the existing local MP3. Does not regenerate audio.
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

from balvoi.dates import format_iso_utc, parse_iso_datetime  # noqa: E402
from balvoi.paths import ROOT  # noqa: E402

load_dotenv(ROOT / ".env", override=True)

from pipeline.config_loader import edition_by_slug  # noqa: E402
from pipeline.lib.megaphone_client import (  # noqa: E402
    production_episode_summary,
    production_episode_title,
    publish_episode,
    set_episode_draft,
    verify_media_file_url,
)
from pipeline.lib.megaphone_once import (  # noqa: E402
    load_run_state,
    stories_artifact_path,
)
from pipeline.lib.publication_identity import PublicationIdentity  # noqa: E402
from pipeline.lib.storage_paths import get_storage_paths  # noqa: E402


def _atomic_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _clear_stale_local_markers(run_id: str, boundary, slug: str = "en") -> None:
    paths = get_storage_paths()
    identity = PublicationIdentity.from_existing(
        boundary=boundary, edition_slug=slug, run_id=run_id
    )

    pub_path = paths.megaphone_publication_result_path(identity.publication_key)
    if pub_path.exists():
        pub_path.unlink()
        print(f"Removed local publication result: {pub_path.name}")

    from pipeline.lib.megaphone_once import megaphone_episode_artifact_path

    ep_path = megaphone_episode_artifact_path(run_id)
    if ep_path.exists():
        ep_path.unlink()
        print("Removed megaphone_episode.json")

    history_path = paths.history_path
    history = json.loads(history_path.read_text(encoding="utf-8"))
    expected = format_iso_utc(boundary)
    kept = [
        row
        for row in history
        if not (
            row.get("slug") == slug
            and row.get("publicationBoundary") == expected
        )
    ]
    removed = len(history) - len(kept)
    if removed:
        _atomic_json(history_path, kept)
        print(f"Removed {removed} history row(s) for {expected} / {slug}")

    state = load_run_state(run_id)
    state["megaphoneEpisodeId"] = ""
    state["status"] = "public_url_verified"
    state["retryable"] = True
    _atomic_json(paths.run(run_id).state_path, state)
    print("Cleared megaphoneEpisodeId from run state")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recreate Megaphone episode for an existing local English MP3"
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--language", default="en")
    parser.add_argument(
        "--confirm-live-create",
        action="store_true",
        help="Required to POST Create Episode to Megaphone",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Create as non-draft (or flip draft=false after create)",
    )
    args = parser.parse_args()
    if args.language.strip().lower() != "en":
        print("Only --language en is supported", file=sys.stderr)
        return 2

    run_id = args.run_id.strip()
    state = load_run_state(run_id)
    boundary = parse_iso_datetime(str(state.get("publicationBoundary") or ""))
    if boundary is None:
        print("Invalid publicationBoundary on run", file=sys.stderr)
        return 1

    paths = get_storage_paths()
    mp3 = paths.episode_mp3(run_id, "en")
    if not mp3.exists() or mp3.stat().st_size <= 0:
        print(f"Missing MP3: {mp3}", file=sys.stderr)
        return 1

    public_base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not public_base:
        print("PUBLIC_BASE_URL is empty", file=sys.stderr)
        return 1
    public_url = f"{public_base}/episodes/{run_id}/en.mp3"

    stories_path = stories_artifact_path(run_id)
    stories = list(json.loads(stories_path.read_text(encoding="utf-8")).get("stories") or [])
    if not stories:
        print("No frozen English stories", file=sys.stderr)
        return 1

    edition = edition_by_slug("en") or {"name": "BalVoi:60 News", "slug": "en"}
    title = production_episode_title(edition, stories, boundary)
    summary = production_episode_summary(edition, stories, boundary)

    print(f"MP3: {mp3} ({mp3.stat().st_size} bytes)")
    print(f"Public URL: {public_url}")
    print(f"Title: {title}")
    verify_media_file_url(public_url)

    if not args.confirm_live_create:
        print("Stopped before Megaphone create (pass --confirm-live-create).")
        return 2

    _clear_stale_local_markers(run_id, boundary, slug="en")

    # Create as live when requested; default draft policy otherwise.
    if args.live:
        os.environ["MEGAPHONE_CREATE_AS_DRAFT"] = "false"
    os.environ["MEGAPHONE_ENABLED"] = "true"

    upload = publish_episode(
        boundary=boundary,
        slug="en",
        title=title,
        summary=summary,
        audio_path=mp3,
        public_audio_url=public_url,
        run_id=run_id,
    )
    if not upload or not upload.get("id"):
        print("Megaphone create returned no episode id", file=sys.stderr)
        return 1

    episode_id = str(upload["id"])
    print(f"Created Megaphone episode: {episode_id}")

    if args.live:
        payload = set_episode_draft(slug="en", episode_id=episode_id, draft=False)
        print(f"draft={payload.get('draft')} status={payload.get('status')}")
        print(f"duration={payload.get('duration')} audioFileStatus={payload.get('audioFileStatus')}")
        print(f"downloadUrl={payload.get('downloadUrl')}")
    else:
        print("Created as draft (pass --live to publish into the feed).")

    # Persist episode id on the run.
    from pipeline.lib.megaphone_once import megaphone_episode_artifact_path

    _atomic_json(
        megaphone_episode_artifact_path(run_id),
        {
            "megaphoneEpisodeId": episode_id,
            "externalId": upload.get("externalId") or f"balvoi60:en:{state.get('publicationBoundary')}",
            "publicationKey": f"balvoi60:en:{state.get('publicationBoundary')}",
            "runId": run_id,
        },
    )
    state = load_run_state(run_id)
    state["megaphoneEpisodeId"] = episode_id
    state["status"] = "published"
    state["retryable"] = False
    state["publicMp3Url"] = public_url
    state["mp3Path"] = str(mp3)
    _atomic_json(paths.run(run_id).state_path, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
