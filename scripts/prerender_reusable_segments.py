#!/usr/bin/env python3
"""Pre-render reusable ElevenLabs clips for all primary voice-shift anchors."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Prefer this repo over any other installed balvoi60 package.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

from balvoi.paths import ROOT

load_dotenv(ROOT / ".env", override=True)

from pipeline.config_loader import assets, editions, segments  # noqa: E402
from pipeline.lib.elevenlabs_client import synthesize  # noqa: E402
from pipeline.lib import reusable_audio_cache as cache  # noqa: E402


def _jobs(
    edition_slugs: list[str] | None,
    anchor_filter: str | None,
) -> list[dict]:
    segs = segments()
    assets_doc = assets()
    jobs: list[dict] = []
    for edition in editions():
        if edition_slugs and edition["slug"] not in edition_slugs:
            continue
        for job in cache.reusable_variant_jobs(edition, segs, assets_doc):
            if anchor_filter and job["anchor_name"].lower() != anchor_filter.lower():
                continue
            jobs.append(job)
    return jobs


def prerender(
    *,
    edition_slugs: list[str] | None = None,
    anchor_filter: str | None = None,
    dry_run: bool = False,
) -> int:
    jobs = _jobs(edition_slugs, anchor_filter)
    generated = reused = skipped = failed = 0

    print(f"Reusable prerender: {len(jobs)} jobs")
    for job in jobs:
        payload = cache.build_cache_payload(**job)
        label = (
            f"{job['language']}/{job['anchor_name']}/"
            f"{job['segment_type']}/{job['variant_id']}"
        )

        hit = cache.lookup(payload)
        if hit is not None:
            print(f"  [reused] {label}")
            reused += 1
            continue

        if dry_run or not os.environ.get("ELEVENLABS_API_KEY"):
            print(f"  [skipped] {label} (dry-run or missing API key)")
            skipped += 1
            continue

        try:
            mp3_path, _ = cache.cache_paths(payload)
            print(f"  [generate] {label}")
            synthesize(job["text"], job["voice_id"], mp3_path)
            cache.write_sidecar(payload)
            generated += 1
        except Exception as err:  # noqa: BLE001 — keep prerender resilient
            print(f"  [failed] {label}: {err}")
            failed += 1

    print(
        f"\nSummary: generated={generated} reused={reused} "
        f"skipped={skipped} failed={failed}"
    )
    return 1 if failed and generated == 0 and reused == 0 else 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Pre-render reusable BalVoi:60 TTS segments for all primary anchors"
    )
    parser.add_argument(
        "--editions",
        default="",
        help="Comma-separated edition slugs (default: all)",
    )
    parser.add_argument(
        "--anchor",
        default="",
        help="Only prerender this primary anchor name (exact, case-insensitive)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Enumerate jobs without calling ElevenLabs",
    )
    args = parser.parse_args(argv)

    slugs = [s.strip() for s in args.editions.split(",") if s.strip()] or None
    anchor = args.anchor.strip() or None
    code = prerender(edition_slugs=slugs, anchor_filter=anchor, dry_run=args.dry_run)
    sys.exit(code)


if __name__ == "__main__":
    main()
