#!/usr/bin/env python3
"""BalVoi:30 production pipeline — ~30 min multilingual episodes every cycle."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import UTC, datetime

from dotenv import load_dotenv

from balvoi.config import is_english
from balvoi.paths import ROOT, pipeline_lock, storage_root

load_dotenv(ROOT / ".env", override=True)

from pipeline.config_loader import edition_by_slug, ensure_storage
from pipeline.lib.duration_budget import MAX_EPISODE_SECONDS, budget_summary
from pipeline.lib.story_history import recently_used_story_ids
from pipeline.stages.assemble_episode import assemble_episode
from pipeline.stages.fetch_articles import fetch_articles
from pipeline.stages.merge_audio import duration_seconds, merge_segments
from pipeline.stages.publish import publish_run
from pipeline.stages.select_stories import select_stories
from pipeline.stages.synthesize import render_segments
from pipeline.stages.transform_stories import (
    headlines_segment,
    localize_stories,
    transform_stories_english,
)

LOCK = pipeline_lock()


def _write_selection_audit(
    run_id: str,
    *,
    window_minutes: int,
    cooldown_minutes: int,
    editions: dict,
) -> None:
    path = storage_root() / "manifests" / "selection" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "runId": run_id,
        "generatedAt": datetime.now(UTC).isoformat(),
        "windowMinutes": window_minutes,
        "cooldownMinutes": cooldown_minutes,
        "editions": editions,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  [select] audit: {path}")


def run_pipeline(run_id: str, edition_slugs: list[str], dry_run: bool) -> int:
    ensure_storage()

    if LOCK.exists():
        print("  [skip] pipeline already running")
        return 2

    LOCK.write_text(run_id, encoding="utf-8")
    try:
        print(f"\nBalVoi:30 pipeline — run {run_id}\n")

        print("1. Fetch articles from BalVoi...")
        pool = fetch_articles()
        if not pool:
            print("   No articles — exiting")
            return 2

        since = int(os.environ.get("BALVOI_ARTICLE_WINDOW_MINUTES", "30"))
        cooldown = int(os.environ.get("BALVOI_STORY_COOLDOWN_MINUTES", "360"))
        exclude_ids = recently_used_story_ids(cooldown)

        print(f"2. Select stories per edition (breaking first, last {since}m window)...")
        audit_editions: dict[str, dict] = {}
        edition_work: list[tuple[dict, list[dict]]] = []

        for slug in edition_slugs:
            edition = edition_by_slug(slug)
            if not edition:
                print(f"  [warn] unknown edition {slug}")
                continue

            print(f"\n   Edition: {edition['name']} ({slug})")
            decisions: list[dict] = []
            selected = select_stories(
                pool,
                edition["id"],
                since_minutes=since,
                exclude_ids=exclude_ids,
                source_countries=edition.get("sourceCountries"),
                record=decisions,
            )

            available_count = sum(
                1
                for row in decisions
                if row.get("reason") not in ("out_of_country", "no_country_tag")
            )
            audit_editions[slug] = {
                "editionName": edition.get("editionName"),
                "language": edition.get("language"),
                "sourceCountries": edition.get("sourceCountries") or [],
                "availableCount": available_count,
                "selectedCount": len(selected),
                "stories": decisions,
            }
            edition_work.append((edition, selected))

            if selected:
                summary = budget_summary(edition["id"], len(selected))
                print(
                    f"   Budget: {summary['fixedOverheadSeconds']}s fixed + "
                    f"{summary['storyCount']}x{summary['secondsPerStory']}s stories "
                    f"~ {summary['estimatedTotalMinutes']} min"
                )
            else:
                print("   No stories selected for this edition")

        if not any(selected for _, selected in edition_work):
            print("   No stories selected for any edition — exiting")
            return 2

        _write_selection_audit(
            run_id,
            window_minutes=since,
            cooldown_minutes=cooldown,
            editions=audit_editions,
        )

        published = 0
        for edition, selected in edition_work:
            if not selected:
                continue

            slug = edition["slug"]
            print(f"\n3. Edition: {edition['name']} ({slug})")

            print("   Transform stories...")
            english = transform_stories_english(selected, edition["id"])
            if is_english(edition["language"]):
                stories = english
                headlines = headlines_segment(english)
            else:
                stories = localize_stories(english, edition["language"])
                headlines = headlines_segment(stories)

            manifest = assemble_episode(edition, stories, run_id, headlines_text=headlines)
            print(f"   {len(manifest['segments'])} segments, {len(stories)} stories")

            print("4. Synthesize + merge...")
            seg_paths = render_segments(manifest, dry_run=dry_run)
            out = storage_root() / "episodes" / run_id / f"{slug}.mp3"
            merge_segments(seg_paths, out)
            dur = duration_seconds(out)
            print(f"   audio: {out} ({dur}s / {dur / 60:.1f} min)")

            if dur > MAX_EPISODE_SECONDS:
                print(
                    f"   [warn] episode exceeds 30 min cap ({dur}s) — "
                    "tighten story count or script length"
                )

            print("5. Publish...")
            ep = publish_run(
                run_id,
                edition,
                manifest,
                out,
                dur,
                stories,
                budget=budget_summary(edition["id"], len(stories)),
            )
            print(f"   {ep['audioUrl']}")
            published += 1

        return 0 if published else 1
    finally:
        LOCK.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="BalVoi:30 podcast pipeline")
    parser.add_argument("--run-id", default=datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ"))
    parser.add_argument(
        "--editions",
        default=os.environ.get("PIPELINE_EDITIONS", "en,es,pt,fr,de,ar,ru,tr"),
    )
    parser.add_argument("--dry-run", action="store_true", default=os.environ.get("DRY_RUN", "").lower() == "true")
    args = parser.parse_args()

    slugs = [s.strip() for s in args.editions.split(",") if s.strip()]
    try:
        code = run_pipeline(args.run_id, slugs, args.dry_run)
        sys.exit(code)
    except Exception as err:
        print(f"\nPipeline failed: {err}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
