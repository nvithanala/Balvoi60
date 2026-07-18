#!/usr/bin/env python3
"""BalVoi:60 hourly pipeline with one frozen story set for every language."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
import traceback
from datetime import UTC, datetime

from dotenv import load_dotenv

from balvoi.config import is_english
from balvoi.dates import (
    article_ownership_window,
    format_iso_utc,
    latest_completed_publication_boundary,
    parse_iso_datetime,
    publication_boundary,
    wait_until_publication_boundary,
)
from balvoi.paths import ROOT, storage_root

load_dotenv(ROOT / ".env", override=True)

from pipeline.config_loader import edition_by_slug, ensure_storage
from pipeline.errors import (
    AudioValidationError,
    DuplicateEditionError,
    LocalizationError,
    MergeError,
    PublishRejectedError,
)
from pipeline.lib import concurrency
from pipeline.lib.config_validation import validate_pipeline_config
from pipeline.lib.edition_lock import EditionLock, boundary_key
from pipeline.lib.edition_status import record_status
from pipeline.lib.megaphone_client import publish_episode
from pipeline.lib.story_history import recently_used_story_ids
from pipeline.stages.assemble_episode import assemble_episode
from pipeline.stages.fetch_articles import fetch_articles
from pipeline.stages.merge_audio import (
    duration_seconds,
    merge_segments,
    validate_publishable_audio,
)
from pipeline.stages.publish import publish_run
from pipeline.stages.select_stories import select_stories
from pipeline.stages.synthesize import render_segments
from pipeline.stages.transform_stories import (
    headlines_segment,
    localize_stories,
    transform_stories_english,
)

ALL_SLUGS = ("en", "es", "pt", "fr", "de", "ar", "ru", "tr")


def _selection_path(boundary: datetime) -> os.PathLike:
    return storage_root() / "manifests" / "selection" / f"{boundary_key(boundary)}.json"


def _freeze_selection(
    boundary: datetime,
    run_id: str,
    cooldown_minutes: int,
) -> tuple[list[dict], dict]:
    path = _selection_path(boundary)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("selectedArticles") or [], payload
    selection_lock = path.with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    owns_lock = False
    deadline = time.monotonic() + 60
    while not owns_lock:
        try:
            fd = os.open(selection_lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(str(time.time()))
            owns_lock = True
        except FileExistsError:
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
                return payload.get("selectedArticles") or [], payload
            try:
                created = float(selection_lock.read_text(encoding="utf-8"))
                if time.time() - created > 120:
                    selection_lock.unlink(missing_ok=True)
                    continue
            except (OSError, ValueError):
                selection_lock.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                raise RuntimeError("Timed out waiting for frozen selection manifest")
            time.sleep(0.1)

    try:
        window_start, window_end = article_ownership_window(boundary)
        print(
            "1. Fetch articles once for "
            f"[{format_iso_utc(window_start)}, {format_iso_utc(window_end)})"
        )
        started = time.monotonic()
        pool = fetch_articles(window_start, window_end)
        fetch_elapsed = time.monotonic() - started
        exclude_ids = recently_used_story_ids(cooldown_minutes)
        decisions: list[dict] = []
        selected = select_stories(
            pool,
            "balvoi60-global",
            exclude_ids=exclude_ids,
            record=decisions,
            window_start=window_start,
            window_end_exclusive=window_end,
        )
        payload = {
            "runId": run_id,
            "publicationBoundary": format_iso_utc(boundary),
            "windowStart": format_iso_utc(window_start),
            "windowEndExclusive": format_iso_utc(window_end),
            "selectionTimestamp": datetime.now(UTC).isoformat(),
            "orderedStoryIds": [str(story["id"]) for story in selected],
            "canonicalUrls": [story.get("url") for story in selected],
            "sourceIds": [str(story.get("source") or story["id"]) for story in selected],
            "selectedArticles": selected,
            "decisions": decisions,
            "fetchElapsedSeconds": round(fetch_elapsed, 3),
        }
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        return selected, payload
    finally:
        if owns_lock:
            selection_lock.unlink(missing_ok=True)


def _failed_stage(stage: str, err: Exception) -> str:
    if isinstance(err, LocalizationError):
        return "failed_localization"
    if isinstance(err, MergeError):
        return "failed_merge"
    if isinstance(err, AudioValidationError):
        return "failed_validation"
    if isinstance(err, PublishRejectedError):
        return "failed_publish"
    if stage == "synthesis":
        return "failed_synthesis"
    return "failed_validation"


def _process_language(
    edition: dict,
    english: list[dict],
    *,
    run_id: str,
    boundary: datetime,
    dry_run: bool,
    minimum_seconds: int,
    lock: EditionLock,
) -> bool:
    slug = edition["slug"]
    story_ids = [str(story["id"]) for story in english]
    boundary_text = format_iso_utc(boundary)
    started = time.monotonic()
    stage = "translation"
    record_status(
        run_id=run_id,
        boundary=boundary_text,
        slug=slug,
        stage="started",
        story_ids=story_ids,
    )
    try:
        if is_english(edition["language"]):
            stories = english
        else:
            stories = localize_stories(english, edition["language"])
        record_status(
            run_id=run_id,
            boundary=boundary_text,
            slug=slug,
            stage="translation",
            story_ids=story_ids,
            elapsed_seconds=time.monotonic() - started,
        )
        headlines = headlines_segment(stories)
        stage = "assembly"
        manifest = assemble_episode(
            edition,
            stories,
            run_id,
            headlines_text=headlines,
            when=boundary,
        )
        record_status(
            run_id=run_id,
            boundary=boundary_text,
            slug=slug,
            stage="assembly",
            story_ids=story_ids,
        )
        stage = "synthesis"
        seg_paths = render_segments(manifest, dry_run=dry_run)
        if dry_run:
            print(f"  [dry-run] {slug}: {len(seg_paths)} existing audio segments")
            return True
        record_status(
            run_id=run_id,
            boundary=boundary_text,
            slug=slug,
            stage="synthesis",
            story_ids=story_ids,
        )
        stage = "merge"
        out = storage_root() / "episodes" / run_id / f"{slug}.mp3"
        merge_segments(seg_paths, out)
        duration = duration_seconds(out)
        record_status(
            run_id=run_id,
            boundary=boundary_text,
            slug=slug,
            stage="merge",
            story_ids=story_ids,
            output_path=str(out),
            audio_size=out.stat().st_size,
            duration=duration,
        )
        stage = "validation"
        audio_size = validate_publishable_audio(out, duration, minimum_seconds)
        record_status(
            run_id=run_id,
            boundary=boundary_text,
            slug=slug,
            stage="validation",
            story_ids=story_ids,
            output_path=str(out),
            audio_size=audio_size,
            duration=duration,
        )
        stage = "publish"
        # Validated languages wait independently for the :00 publication boundary.
        wait_until_publication_boundary(boundary)
        record_status(
            run_id=run_id,
            boundary=boundary_text,
            slug=slug,
            stage="publication_gate",
            story_ids=story_ids,
            output_path=str(out),
            audio_size=audio_size,
            duration=duration,
        )
        public_base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
        public_audio_url = f"{public_base}/episodes/{run_id}/{slug}.mp3"
        upload = publish_episode(
            boundary=boundary,
            slug=slug,
            title=stories[0].get("title") or edition["name"],
            summary=" • ".join(str(story.get("title") or "") for story in stories),
            audio_path=out,
            public_audio_url=public_audio_url,
        )
        if upload:
            record_status(
                run_id=run_id,
                boundary=boundary_text,
                slug=slug,
                stage="upload",
                story_ids=story_ids,
                output_path=str(out),
                audio_size=audio_size,
                duration=duration,
            )
        episode = publish_run(
            run_id,
            edition,
            manifest,
            out,
            duration,
            stories,
            publication_boundary=boundary,
            minimum_duration_seconds=minimum_seconds,
        )
        record_status(
            run_id=run_id,
            boundary=boundary_text,
            slug=slug,
            stage="published",
            story_ids=story_ids,
            output_path=str(out),
            audio_size=audio_size,
            duration=duration,
            elapsed_seconds=time.monotonic() - started,
            metrics=concurrency.snapshot(),
        )
        print(f"  [published] {slug}: {episode['audioUrl']}")
        return True
    except Exception as err:
        status = _failed_stage(stage, err)
        record_status(
            run_id=run_id,
            boundary=boundary_text,
            slug=slug,
            stage=status,
            story_ids=story_ids,
            error=f"{type(err).__name__}: {err}",
            elapsed_seconds=time.monotonic() - started,
            metrics=concurrency.snapshot(),
        )
        print(f"  [{status}] edition={slug} language={edition['language']}: {err}")
        return False
    finally:
        lock.release()


def run_pipeline(
    run_id: str,
    edition_slugs: list[str],
    dry_run: bool,
    *,
    boundary: datetime | None = None,
) -> int:
    ensure_storage()
    boundary = (
        (boundary or publication_boundary())
        .astimezone(UTC)
        .replace(minute=0, second=0, microsecond=0)
    )
    requested = [slug for slug in edition_slugs if edition_by_slug(slug)]
    settings = validate_pipeline_config(requested, dry_run=dry_run)
    concurrency.configure(
        translation=settings["translation_workers"],
        tts=settings["tts_workers"],
        merge=settings["merge_workers"],
    )
    locks: dict[str, EditionLock] = {}
    for slug in requested:
        lock = EditionLock(boundary, slug)
        try:
            lock.acquire()
        except DuplicateEditionError as err:
            print(f"  [{err}] {slug} {format_iso_utc(boundary)}")
            continue
        locks[slug] = lock
    if not locks:
        return 0

    print(f"\nBalVoi:60 hourly pipeline — {format_iso_utc(boundary)}\n")
    try:
        try:
            selected, selection = _freeze_selection(
                boundary, run_id, settings["story_cooldown_minutes"]
            )
        except Exception as err:
            for slug, lock in locks.items():
                record_status(
                    run_id=run_id,
                    boundary=format_iso_utc(boundary),
                    slug=slug,
                    stage="failed_fetch",
                    error=f"{type(err).__name__}: {err}",
                )
                lock.release()
            return 2
        for slug in locks:
            record_status(
                run_id=run_id,
                boundary=format_iso_utc(boundary),
                slug=slug,
                stage="fetch",
                elapsed_seconds=selection.get("fetchElapsedSeconds"),
            )
        if not selected:
            for slug, lock in locks.items():
                record_status(
                    run_id=run_id,
                    boundary=format_iso_utc(boundary),
                    slug=slug,
                    stage="failed_selection",
                    error="No unique stories in the hourly ownership window",
                )
                lock.release()
            return 2
        for slug in locks:
            record_status(
                run_id=run_id,
                boundary=format_iso_utc(boundary),
                slug=slug,
                stage="selection",
                story_ids=selection["orderedStoryIds"],
            )
        english = transform_stories_english(selected, "balvoi60-global")
        if not english:
            for slug, lock in locks.items():
                record_status(
                    run_id=run_id,
                    boundary=format_iso_utc(boundary),
                    slug=slug,
                    stage="failed_selection",
                    story_ids=selection["orderedStoryIds"],
                    error="Selected stories had no usable body text",
                )
                lock.release()
            return 2
        published = 0
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=settings["language_workers"],
            thread_name_prefix="balvoi-language",
        ) as executor:
            futures = {
                executor.submit(
                    _process_language,
                    edition_by_slug(slug),
                    english,
                    run_id=run_id,
                    boundary=boundary,
                    dry_run=dry_run,
                    minimum_seconds=settings["minimum_publish_seconds"],
                    lock=lock,
                ): slug
                for slug, lock in locks.items()
            }
            for future in concurrent.futures.as_completed(futures):
                if future.result():
                    published += 1
        return 0 if published else 1
    except Exception:
        for lock in locks.values():
            lock.release()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="BalVoi:60 podcast pipeline")
    parser.add_argument("--run-id", default="")
    parser.add_argument(
        "--preview",
        action="store_true",
        default=os.environ.get("PREVIEW_MODE", "").lower() == "true",
        help="Generate isolated local previews without locks, publication, or live metadata",
    )
    parser.add_argument(
        "--all-languages",
        action="store_true",
        help="Generate en, es, pt, fr, de, ar, ru, and tr",
    )
    parser.add_argument(
        "--boundary",
        default="",
        help=(
            "Publication boundary as ISO-8601 UTC. Default resolves from now: "
            "at/after :51 → next hour :00; before :51 → current hour :00"
        ),
    )
    parser.add_argument(
        "--editions",
        default=os.environ.get("PIPELINE_EDITIONS", "en,es,pt,fr,de,ar,ru,tr"),
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=os.environ.get("DRY_RUN", "").lower() == "true"
    )
    args = parser.parse_args()

    slugs = list(ALL_SLUGS) if args.all_languages else [
        s.strip() for s in args.editions.split(",") if s.strip()
    ]
    if args.boundary:
        boundary = parse_iso_datetime(args.boundary)
    elif args.preview:
        boundary = latest_completed_publication_boundary()
    else:
        boundary = publication_boundary()
    if boundary is None:
        parser.error("--boundary must be a valid ISO-8601 datetime")
    if args.preview:
        # .env is loaded with override semantics, so enforce preview safety after parsing.
        os.environ["PREVIEW_MODE"] = "true"
        os.environ["SCHEDULER_ENABLED"] = "false"
        os.environ["CRON_ENABLED"] = "false"
        os.environ["MEGAPHONE_ENABLED"] = "false"
        os.environ["BALVOI_ALLOW_DEMO_ARTICLES"] = "false"
        os.environ["BALVOI_ARTICLE_WINDOW_MINUTES"] = "60"
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            print(
                "\nPreview generation failed: OPENAI_API_KEY is empty. "
                "Set a real OpenAI key in .env, then re-run the preview command."
            )
            sys.exit(2)
        settings = validate_pipeline_config(slugs, dry_run=False)
        run_id = args.run_id or datetime.now(UTC).strftime("preview-%Y-%m-%dT%H-%M-%SZ")
        from pipeline.preview import run_preview

        try:
            code, preview_dir, _summary = run_preview(
                run_id=run_id,
                boundary=boundary,
                edition_slugs=slugs,
                settings=settings,
            )
            print(f"\nPreview output: {preview_dir}")
            sys.exit(code)
        except Exception as err:
            print(f"\nPreview generation failed: {err}")
            traceback.print_exc()
            sys.exit(1)

    run_id = args.run_id or boundary.strftime("%Y-%m-%dT%H-%M-%SZ")
    try:
        code = run_pipeline(run_id, slugs, args.dry_run, boundary=boundary)
        sys.exit(code)
    except Exception as err:
        print(f"\nPipeline failed: {err}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
