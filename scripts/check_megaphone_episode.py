#!/usr/bin/env python3
"""Read-only Megaphone episode status poll (never creates, updates, or prints tokens)."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from balvoi.paths import ROOT  # noqa: E402

load_dotenv(ROOT / ".env", override=True)

from pipeline.lib.megaphone_client import (  # noqa: E402
    BASE_URL,
    resolve_megaphone_config,
)
from pipeline.lib.megaphone_discover import authorization_header  # noqa: E402

TERMINAL_AUDIO_STATUSES = frozenset({"success", "error"})
REPORT_FIELDS = (
    "id",
    "title",
    "pubdate",
    "draft",
    "status",
    "audioFileStatus",
    "audioFileProcessing",
    "duration",
    "size",
    "externalId",
    "downloadUrl",
    "uid",
)


def fetch_episode(episode_id: str, *, slug: str) -> dict:
    token, network, podcast = resolve_megaphone_config(slug)
    if not (token and network and podcast):
        raise SystemExit(f"Missing Megaphone token/network/podcast config for {slug!r}")
    url = (
        f"{BASE_URL.rstrip('/')}/networks/{network}"
        f"/podcasts/{podcast}/episodes/{episode_id}"
    )
    response = requests.get(
        url,
        headers={"Authorization": authorization_header(token), "Accept": "application/json"},
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise SystemExit("Megaphone returned an unexpected episode payload")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll one Megaphone episode's status")
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--language", default="en")
    parser.add_argument("--watch", action="store_true", help="Poll until audio processing settles")
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--attempts", type=int, default=20)
    args = parser.parse_args()

    slug = args.language.strip().lower()
    attempts = max(1, args.attempts) if args.watch else 1

    episode: dict = {}
    for attempt in range(attempts):
        episode = fetch_episode(args.episode_id.strip(), slug=slug)
        audio_status = str(episode.get("audioFileStatus") or "")
        processing = bool(episode.get("audioFileProcessing"))
        print(
            f"[{attempt + 1}/{attempts}] status={episode.get('status')} "
            f"audioFileStatus={audio_status or '(none)'} processing={processing} "
            f"duration={episode.get('duration')} size={episode.get('size')}",
            flush=True,
        )
        if audio_status in TERMINAL_AUDIO_STATUSES and not processing:
            break
        if attempt < attempts - 1:
            time.sleep(max(0.0, args.interval))

    print()
    for field in REPORT_FIELDS:
        print(f"{field:22} = {episode.get(field)}")
    print(f"{'audioFile':22} = {episode.get('audioFile')}")
    return 0 if str(episode.get("audioFileStatus") or "") != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
