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

from balvoi.config import is_english
from balvoi.dates import (
    article_ownership_window,
    format_iso_utc,
    latest_completed_publication_boundary,
    parse_iso_datetime,
    publication_boundary,
    wait_until_publication_boundary,
)
from balvoi.paths import ROOT
from pipeline.lib.settings import load_app_dotenv

# Process environment wins over ``.env`` (see ``pipeline.lib.settings``).
load_app_dotenv(ROOT / ".env")

from pipeline.config_loader import edition_by_slug, ensure_storage
from pipeline.errors import (
    AudioValidationError,
    DuplicateEditionError,
    LocalizationError,
    MergeError,
    PublishRejectedError,
)
from pipeline.lib import concurrency
from pipeline.lib.already_published_claim import (
    try_recover_already_published_claim,
)
from pipeline.lib.config_validation import validate_pipeline_config
from pipeline.lib.edition_lock import EditionLock, boundary_key
from pipeline.lib.edition_status import record_status
from pipeline.lib.episode_media import prepare_public_episode_media
from pipeline.lib.hourly_report import write_hourly_report
from pipeline.lib.logging_utils import (
    announce,
    bind_context,
    log_event,
    log_exception,
    structured_logs_enabled,
)
from pipeline.lib.megaphone_client import (
    enabled as megaphone_enabled,
)
from pipeline.lib.megaphone_client import (
    production_episode_summary,
    production_episode_title,
    publish_episode,
)
from pipeline.lib.publication_claim import complete_claim, create_claim, fail_claim
from pipeline.lib.publication_identity import PublicationIdentity, canonical_run_id
from pipeline.lib.storage_paths import get_storage_paths
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
    return get_storage_paths().production_selection_manifest(
        boundary_key=boundary_key(boundary)
    )


def _freeze_selection(
    boundary: datetime,
    run_id: str,
    cooldown_minutes: int,
) -> tuple[list[dict], dict]:
    path = _selection_path(boundary)

    def _usable_frozen(payload: dict) -> list[dict] | None:
        selected = payload.get("selectedArticles") or []
        if isinstance(selected, list) and selected:
            return selected
        return None

    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        selected = _usable_frozen(payload)
        if selected is not None:
            return selected, payload
        # Empty freeze from a failed fetch must not block retries.
        path.unlink(missing_ok=True)

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
                selected = _usable_frozen(payload)
                if selected is not None:
                    return selected, payload
                path.unlink(missing_ok=True)
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
        # Another writer may have frozen a usable selection while we waited.
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            selected = _usable_frozen(payload)
            if selected is not None:
                return selected, payload
            path.unlink(missing_ok=True)

        window_start, window_end = article_ownership_window(boundary)
        window_source = "hourly_ownership"
        announce(
            "1. Fetch articles once for "
            f"[{format_iso_utc(window_start)}, {format_iso_utc(window_end)})",
            "Selection Started",
            stage="selection",
            runId=run_id,
            publicationBoundary=format_iso_utc(boundary),
            boundaryKey=boundary_key(boundary),
        )
        started = time.monotonic()
        pool = fetch_articles(window_start, window_end)
        # Empty ownership hour → no episode. Do not widen to prior hours.
        if not pool:
            announce(
                "1b. Hourly window empty — no articles; skipping episode",
                "Selection Empty",
                stage="selection",
                runId=run_id,
                publicationBoundary=format_iso_utc(boundary),
                boundaryKey=boundary_key(boundary),
                articleWindowSource=window_source,
            )
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
            "articleWindowSource": window_source,
            "selectionTimestamp": datetime.now(UTC).isoformat(),
            "orderedStoryIds": [str(story["id"]) for story in selected],
            "canonicalUrls": [story.get("url") for story in selected],
            "sourceIds": [str(story.get("source") or story["id"]) for story in selected],
            "selectedArticles": selected,
            "decisions": decisions,
            "fetchElapsedSeconds": round(fetch_elapsed, 3),
        }
        # Only freeze non-empty selections so failed hours can be retried.
        if selected:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
            log_event(
                "Selection Frozen",
                stage="selection",
                runId=run_id,
                publicationBoundary=format_iso_utc(boundary),
                boundaryKey=boundary_key(boundary),
                storyCount=len(selected),
                elapsedMs=int(fetch_elapsed * 1000),
            )
        else:
            log_event(
                "Selection Empty Not Frozen",
                stage="selection",
                runId=run_id,
                publicationBoundary=format_iso_utc(boundary),
                boundaryKey=boundary_key(boundary),
                storyCount=0,
                elapsedMs=int(fetch_elapsed * 1000),
            )
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
    bkey = boundary_key(boundary)
    identity = PublicationIdentity.from_boundary(boundary, slug, run_id=run_id)
    pub_key = identity.publication_key
    started = time.monotonic()
    stage = "translation"
    claim_held = False
    out = None
    audio_size = None
    duration = None
    with bind_context(
        runId=run_id,
        publicationBoundary=boundary_text,
        boundaryKey=bkey,
        publicationKey=pub_key,
        slug=slug,
        language=edition.get("language"),
        editionId=edition.get("id"),
    ):
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
                log_event("Localization Started", stage="translation")
                loc_started = time.monotonic()
                stories = localize_stories(english, edition["language"])
                log_event(
                    "Localization Completed",
                    stage="translation",
                    elapsedMs=int((time.monotonic() - loc_started) * 1000),
                )
            record_status(
                run_id=run_id,
                boundary=boundary_text,
                slug=slug,
                stage="translation",
                story_ids=story_ids,
                elapsed_seconds=time.monotonic() - started,
            )
            headlines = headlines_segment(stories, language=edition["language"])
            stage = "assembly"
            log_event("Assembly Started", stage="assembly")
            asm_started = time.monotonic()
            manifest = assemble_episode(
                edition,
                stories,
                run_id,
                headlines_text=headlines,
                when=boundary,
            )
            log_event(
                "Assembly Completed",
                stage="assembly",
                elapsedMs=int((time.monotonic() - asm_started) * 1000),
            )
            record_status(
                run_id=run_id,
                boundary=boundary_text,
                slug=slug,
                stage="assembly",
                story_ids=story_ids,
            )
            stage = "synthesis"
            log_event("TTS Started", stage="synthesis")
            tts_started = time.monotonic()
            seg_paths, _synth_stats = render_segments(manifest, dry_run=dry_run)
            log_event(
                "TTS Completed",
                stage="synthesis",
                elapsedMs=int((time.monotonic() - tts_started) * 1000),
                segmentCount=len(seg_paths),
            )
            if dry_run:
                announce(
                    f"  [dry-run] {slug}: {len(seg_paths)} existing audio segments",
                    "Edition Completed",
                    stage="synthesis",
                    dryRun=True,
                )
                return True
            record_status(
                run_id=run_id,
                boundary=boundary_text,
                slug=slug,
                stage="synthesis",
                story_ids=story_ids,
            )
            stage = "merge"
            log_event("Merge Started", stage="merge")
            merge_started = time.monotonic()
            out = get_storage_paths().episode_mp3(run_id, slug)
            merge_segments(seg_paths, out)
            duration = duration_seconds(out)
            log_event(
                "Merge Completed",
                stage="merge",
                elapsedMs=int((time.monotonic() - merge_started) * 1000),
                durationSeconds=duration,
            )
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
            log_event("Validation Started", stage="validation")
            val_started = time.monotonic()
            audio_size = validate_publishable_audio(out, duration, minimum_seconds)
            log_event(
                "Validation Completed",
                stage="validation",
                elapsedMs=int((time.monotonic() - val_started) * 1000),
                audioSize=audio_size,
                durationSeconds=duration,
            )
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
            stage = "media_prepare"
            # Build phase: public media + metadata before :00. No Megaphone create yet.
            episode_title = production_episode_title(edition, stories, boundary)
            episode_summary = production_episode_summary(edition, stories, boundary)
            media = prepare_public_episode_media(
                audio_path=out,
                run_id=run_id,
                slug=slug,
                require_reachable=megaphone_enabled(),
            )
            public_audio_url = str(media["publicUrl"])
            record_status(
                run_id=run_id,
                boundary=boundary_text,
                slug=slug,
                stage="media_prepare",
                story_ids=story_ids,
                output_path=str(out),
                audio_size=audio_size,
                duration=duration,
                public_audio_url=public_audio_url,
            )
            stage = "publish"
            claim = create_claim(identity)
            if not claim.acquired:
                announce(
                    f"  [duplicate_blocked] {slug} {boundary_text}",
                    "Claim Already Owned",
                    stage="publish",
                    reason=claim.reason,
                )
                record_status(
                    run_id=run_id,
                    boundary=boundary_text,
                    slug=slug,
                    stage="failed_publish",
                    story_ids=story_ids,
                    output_path=str(out),
                    audio_size=audio_size,
                    duration=duration,
                    error=f"duplicate_blocked:{claim.reason or 'already_owned'}",
                    elapsed_seconds=time.monotonic() - started,
                    metrics=concurrency.snapshot(),
                    public_audio_url=public_audio_url,
                )
                return False
            claim_held = True
            # Ready early → wait until :00; ready late → publish immediately.
            log_event("Publication Gate Waiting", stage="publication_gate")
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
                public_audio_url=public_audio_url,
            )
            log_event("Megaphone Publish Started", stage="upload")
            # Intentional live Create Episode at/after boundary (no draft-then-undraft).
            upload = publish_episode(
                boundary=boundary,
                slug=slug,
                title=episode_title,
                summary=episode_summary,
                audio_path=out,
                public_audio_url=public_audio_url,
                run_id=run_id,
                draft=False,
            )
            if upload:
                log_event(
                    "Megaphone Publish Completed",
                    stage="upload",
                    megaphoneEpisodeId=upload.get("id"),
                    reused=bool(upload.get("reused")),
                    publicationDelaySeconds=upload.get("publicationDelaySeconds"),
                    publishedAt=upload.get("publishedAt"),
                )
                record_status(
                    run_id=run_id,
                    boundary=boundary_text,
                    slug=slug,
                    stage="upload",
                    story_ids=story_ids,
                    output_path=str(out),
                    audio_size=audio_size,
                    duration=duration,
                    megaphone_episode_id=str(upload.get("id") or ""),
                    published_at=str(upload.get("publishedAt") or ""),
                    publication_delay_seconds=(
                        float(upload["publicationDelaySeconds"])
                        if upload.get("publicationDelaySeconds") is not None
                        else None
                    ),
                    public_audio_url=public_audio_url,
                )
            else:
                log_event(
                    "Megaphone Publish Completed",
                    stage="upload",
                    skipped=True,
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
            complete_claim(identity)
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
                megaphone_episode_id=(
                    str(upload.get("id") or "") if upload else None
                ),
                published_at=str(upload.get("publishedAt") or "") if upload else None,
                publication_delay_seconds=(
                    float(upload["publicationDelaySeconds"])
                    if upload and upload.get("publicationDelaySeconds") is not None
                    else None
                ),
                public_audio_url=public_audio_url,
            )
            announce(
                f"  [published] {slug}: {episode['audioUrl']}",
                "Local Publish Completed",
                stage="published",
                audioUrl=episode.get("audioUrl"),
                publicationDelaySeconds=(
                    upload.get("publicationDelaySeconds") if upload else None
                ),
            )
            log_event(
                "Edition Completed",
                stage="published",
                elapsedMs=int((time.monotonic() - started) * 1000),
                publicationDelaySeconds=(
                    upload.get("publicationDelaySeconds") if upload else None
                ),
            )
            return True
        except Exception as err:
            if claim_held:
                proof = try_recover_already_published_claim(identity, err)
                if proof is not None:
                    completed = complete_claim(identity)
                    if completed is not None:
                        claim_held = False
                        log_event(
                            "Claim Completed From Existing Successful Publication",
                            stage="published",
                            publicationKey=identity.publication_key,
                            runId=run_id,
                            slug=slug,
                            megaphoneEpisodeId=proof.episode_id,
                        )
                        announce(
                            f"  [already_published] {slug}: megaphone={proof.episode_id}",
                            "Local Publish Already Published",
                            stage="published",
                            megaphoneEpisodeId=proof.episode_id,
                            publicationKey=identity.publication_key,
                        )
                        record_status(
                            run_id=run_id,
                            boundary=boundary_text,
                            slug=slug,
                            stage="published",
                            story_ids=story_ids,
                            output_path=str(out) if out is not None else None,
                            audio_size=audio_size,
                            duration=duration,
                            elapsed_seconds=time.monotonic() - started,
                            metrics=concurrency.snapshot(),
                        )
                        log_event(
                            "Edition Completed",
                            stage="published",
                            alreadyPublished=True,
                            elapsedMs=int((time.monotonic() - started) * 1000),
                        )
                        return True
                    log_event(
                        "Already Published Claim Complete Failed",
                        stage="publish",
                        publicationKey=identity.publication_key,
                        runId=run_id,
                        slug=slug,
                        megaphoneEpisodeId=proof.episode_id,
                        failClosed=True,
                    )
                fail_claim(identity, error=f"{type(err).__name__}: {err}")
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
            log_exception(
                "Edition Failed",
                err,
                stage=status,
                elapsedMs=int((time.monotonic() - started) * 1000),
            )
            if not structured_logs_enabled():
                print(f"  [{status}] edition={slug} language={edition['language']}: {err}")
            return False
        finally:
            lock.release()


def _write_hourly_review_report(
    run_id: str,
    boundary: datetime,
    edition_slugs: list[str] | None = None,
) -> None:
    """Best-effort operator review JSON for all languages; never fail the pipeline."""
    try:
        path = write_hourly_report(
            run_id=run_id,
            boundary=boundary,
            edition_slugs=edition_slugs,  # None → all eight languages
        )
        if not structured_logs_enabled():
            print(f"  [report] {path}")
    except Exception as err:  # noqa: BLE001 — report must not break publish
        print(f"  [warn] hourly report failed: {type(err).__name__}: {err}")


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
    boundary_text = format_iso_utc(boundary)
    bkey = boundary_key(boundary)
    log_event(
        "Pipeline Started",
        stage="pipeline",
        runId=run_id,
        publicationBoundary=boundary_text,
        boundaryKey=bkey,
        dryRun=dry_run,
    )
    requested = [slug for slug in edition_slugs if edition_by_slug(slug)]
    settings = validate_pipeline_config(requested, dry_run=dry_run)
    log_event(
        "Configuration Validated",
        stage="pipeline",
        runId=run_id,
        publicationBoundary=boundary_text,
        boundaryKey=bkey,
        editionCount=len(requested),
    )
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
            log_event(
                "Edition Lock Acquired",
                stage="lock",
                runId=run_id,
                publicationBoundary=boundary_text,
                boundaryKey=bkey,
                slug=slug,
                publicationKey=PublicationIdentity.from_boundary(
                    boundary, slug, run_id=run_id
                ).publication_key,
            )
        except DuplicateEditionError as err:
            announce(
                f"  [{err}] {slug} {format_iso_utc(boundary)}",
                "Edition Lock Acquired",
                stage="lock",
                runId=run_id,
                slug=slug,
                skipped=True,
                reason=str(err),
            )
            continue
        locks[slug] = lock
    if not locks:
        log_event(
            "Pipeline Completed",
            stage="pipeline",
            runId=run_id,
            publicationBoundary=boundary_text,
            boundaryKey=bkey,
            exitCode=0,
            publishedCount=0,
        )
        _write_hourly_review_report(run_id, boundary)
        return 0

    if not structured_logs_enabled():
        print(f"\nBalVoi:60 hourly pipeline — {format_iso_utc(boundary)}\n")
    try:
        try:
            selected, selection = _freeze_selection(
                boundary, run_id, settings["story_cooldown_minutes"]
            )
        except Exception as err:
            log_exception(
                "Edition Failed",
                err,
                stage="failed_fetch",
                runId=run_id,
                publicationBoundary=boundary_text,
                boundaryKey=bkey,
            )
            for slug, lock in locks.items():
                record_status(
                    run_id=run_id,
                    boundary=format_iso_utc(boundary),
                    slug=slug,
                    stage="failed_fetch",
                    error=f"{type(err).__name__}: {err}",
                )
                lock.release()
            log_event(
                "Pipeline Completed",
                stage="pipeline",
                runId=run_id,
                publicationBoundary=boundary_text,
                boundaryKey=bkey,
                exitCode=2,
            )
            _write_hourly_review_report(run_id, boundary)
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
            log_event(
                "Pipeline Completed",
                stage="pipeline",
                runId=run_id,
                publicationBoundary=boundary_text,
                boundaryKey=bkey,
                exitCode=2,
                reason="empty_selection",
            )
            _write_hourly_review_report(run_id, boundary)
            return 2
        for slug in locks:
            record_status(
                run_id=run_id,
                boundary=format_iso_utc(boundary),
                slug=slug,
                stage="selection",
                story_ids=selection["orderedStoryIds"],
            )
        log_event(
            "English Transform Started",
            stage="transform",
            runId=run_id,
            publicationBoundary=boundary_text,
            boundaryKey=bkey,
            storyCount=len(selected),
        )
        transform_started = time.monotonic()
        english = transform_stories_english(selected, "balvoi60-global")
        log_event(
            "English Transform Completed",
            stage="transform",
            runId=run_id,
            publicationBoundary=boundary_text,
            boundaryKey=bkey,
            storyCount=len(english),
            elapsedMs=int((time.monotonic() - transform_started) * 1000),
        )
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
            log_event(
                "Pipeline Completed",
                stage="pipeline",
                runId=run_id,
                publicationBoundary=boundary_text,
                boundaryKey=bkey,
                exitCode=2,
                reason="empty_english",
            )
            _write_hourly_review_report(run_id, boundary)
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
        exit_code = 0 if published else 1
        log_event(
            "Pipeline Completed",
            stage="pipeline",
            runId=run_id,
            publicationBoundary=boundary_text,
            boundaryKey=bkey,
            exitCode=exit_code,
            publishedCount=published,
        )
        _write_hourly_review_report(run_id, boundary)
        return exit_code
    except Exception as err:
        log_exception(
            "Pipeline Completed",
            err,
            stage="pipeline",
            runId=run_id,
            publicationBoundary=boundary_text,
            boundaryKey=bkey,
        )
        for lock in locks.values():
            lock.release()
        _write_hourly_review_report(run_id, boundary)
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
            "at/after :45 → next hour :00; before :45 → current hour :00"
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

    run_id = args.run_id or canonical_run_id(boundary)
    try:
        code = run_pipeline(run_id, slugs, args.dry_run, boundary=boundary)
        sys.exit(code)
    except Exception as err:
        print(f"\nPipeline failed: {err}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
