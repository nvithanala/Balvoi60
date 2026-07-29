"""English-only one-shot Megaphone live publish orchestration.

Reuses existing production stage functions from ``pipeline.run`` / stage modules.
Does not start the scheduler and never processes non-English editions.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from balvoi.dates import (
    article_ownership_window,
    format_iso_utc,
    parse_iso_datetime,
    publication_boundary,
    wait_until_publication_boundary,
)
from pipeline.config_loader import edition_by_slug, ensure_storage
from pipeline.errors import (
    AudioValidationError,
    DuplicateEditionError,
    MergeError,
    PublishRejectedError,
)
from pipeline.lib.already_published_claim import (
    try_recover_already_published_claim,
)
from pipeline.lib.config_validation import validate_pipeline_config
from pipeline.lib.edition_lock import EditionLock, edition_was_published
from pipeline.lib.edition_status import record_status
from pipeline.lib.megaphone_client import (
    fetch_podcast,
    load_existing_episode_summary,
    normalize_stored_publication_key,
    production_episode_summary,
    production_episode_title,
    public_base_url_issues,
    publication_key,
    publish_episode,
    resolve_megaphone_config,
)
from pipeline.lib.publication_claim import (
    complete_claim,
    create_claim,
    fail_claim,
)
from pipeline.lib.publication_identity import PublicationIdentity
from pipeline.lib.storage_paths import get_storage_paths
from pipeline.run import _freeze_selection
from pipeline.stages.assemble_episode import assemble_episode
from pipeline.stages.merge_audio import (
    duration_seconds,
    merge_segments,
    validate_publishable_audio,
)
from pipeline.stages.publish import publish_run
from pipeline.stages.synthesize import render_segments
from pipeline.stages.transform_stories import headlines_segment, transform_stories_english

LANGUAGE = "en"
EXPECTED_TITLE_MARKERS = ("five eyes", "balvoi:60")
AUDIO_MIME_PREFIXES = ("audio/", "application/octet-stream")
TERMINAL_SUCCESS = frozenset({"published", "megaphone_created", "processed"})
RETRYABLE_STATUSES = frozenset(
    {
        "failed_generation",
        "failed_validation",
        "failed_public_url",
        "failed_megaphone",
        "audio_fetch_pending",
        "public_url_verified",
        "generated",
        "audio_validated",
        "publishing",
    }
)


@dataclass
class OnceRunContext:
    run_id: str
    boundary: datetime
    language: str = LANGUAGE
    confirm_live_publish: bool = False
    preflight_only: bool = False
    attempt: int = 1
    reuse_audio: bool = False
    started_monotonic: float = field(default_factory=time.monotonic)
    stages: list[dict[str, Any]] = field(default_factory=list)
    status: str = "pending"
    errors: list[str] = field(default_factory=list)
    story_ids: list[str] = field(default_factory=list)
    window_start: str = ""
    window_end: str = ""
    mp3_path: str = ""
    public_url: str = ""
    podcast_title: str = ""
    podcast_id: str = ""
    megaphone_episode_id: str = ""
    duration_seconds: int = 0
    audio_size: int = 0
    retryable: bool = False
    failed_stage: str | None = None
    publication_key: str = ""
    selection: dict[str, Any] = field(default_factory=dict)
    stories: list[dict] = field(default_factory=list)
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def run_dir(self) -> Path:
        return get_storage_paths().run(self.run_id).run_root

    @property
    def report_path(self) -> Path:
        return get_storage_paths().run(self.run_id).report_path

    @property
    def state_path(self) -> Path:
        return get_storage_paths().run(self.run_id).state_path

    @property
    def jsonl_path(self) -> Path:
        return get_storage_paths().run(self.run_id).events_path


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _safe_error(err: BaseException) -> str:
    text = " ".join(str(err).split())
    # Never echo tokens if they somehow appear in exception text.
    text = re.sub(r"(?i)(token[\"'=:\s]+)[^\s\"']+", r"\1***", text)
    return f"{type(err).__name__}: {text}"[:500]


def _log(ctx: OnceRunContext, stage: str, status: str, **extra: Any) -> None:
    elapsed = round(time.monotonic() - ctx.started_monotonic, 3)
    event = {
        "runId": ctx.run_id,
        "boundaryUtc": format_iso_utc(ctx.boundary),
        "articleWindow": {"start": ctx.window_start, "endExclusive": ctx.window_end},
        "stage": stage,
        "status": status,
        "elapsedSeconds": elapsed,
        "attempt": ctx.attempt,
        "publicMp3Url": ctx.public_url or None,
        "megaphoneEpisodeId": ctx.megaphone_episode_id or None,
        "publicationKey": ctx.publication_key or None,
        **extra,
    }
    # Strip secrets if accidentally passed.
    event.pop("token", None)
    event.pop("apiKey", None)
    line = json.dumps(event, ensure_ascii=False)
    print(
        f"[{status}] stage={stage} elapsed={elapsed}s attempt={ctx.attempt}"
        + (f" error={extra.get('error')}" if extra.get("error") else "")
    )
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    with ctx.jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    ctx.stages.append(event)


def _set_status(ctx: OnceRunContext, status: str, *, failed_stage: str | None = None) -> None:
    ctx.status = status
    if failed_stage:
        ctx.failed_stage = failed_stage
        ctx.retryable = status in RETRYABLE_STATUSES or status.startswith("failed_")
    _persist_state(ctx)


def _persist_state(ctx: OnceRunContext) -> None:
    payload = {
        "runId": ctx.run_id,
        "language": ctx.language,
        "publicationBoundary": format_iso_utc(ctx.boundary),
        "publicationKey": ctx.publication_key,
        "status": ctx.status,
        "attempt": ctx.attempt,
        "storyIds": ctx.story_ids,
        "windowStart": ctx.window_start,
        "windowEndExclusive": ctx.window_end,
        "mp3Path": ctx.mp3_path,
        "publicMp3Url": ctx.public_url,
        "podcastTitle": ctx.podcast_title,
        "podcastId": ctx.podcast_id,
        "megaphoneEpisodeId": ctx.megaphone_episode_id,
        "durationSeconds": ctx.duration_seconds,
        "audioSize": ctx.audio_size,
        "retryable": ctx.retryable,
        "failedStage": ctx.failed_stage,
        "updatedAt": datetime.now(UTC).isoformat(),
    }
    _atomic_json(ctx.state_path, payload)
    if ctx.publication_key:
        key_path = get_storage_paths().once_publication_record_path(ctx.publication_key)
        _atomic_json(key_path, payload)


def write_report(ctx: OnceRunContext) -> dict:
    elapsed = round(time.monotonic() - ctx.started_monotonic, 3)
    report = {
        "runId": ctx.run_id,
        "boundary": format_iso_utc(ctx.boundary),
        "articleWindow": {
            "start": ctx.window_start,
            "endExclusive": ctx.window_end,
        },
        "englishMp3Path": ctx.mp3_path,
        "publicMp3Url": ctx.public_url,
        "megaphonePodcastTitle": ctx.podcast_title,
        "megaphonePodcastId": ctx.podcast_id,
        "megaphoneEpisodeId": ctx.megaphone_episode_id,
        "finalStatus": ctx.status,
        "elapsedSeconds": elapsed,
        "retryable": ctx.retryable,
        "failedStage": ctx.failed_stage,
        "publicationKey": ctx.publication_key,
        "attempt": ctx.attempt,
        "errors": ctx.errors,
        "stages": ctx.stages,
    }
    _atomic_json(ctx.report_path, report)
    return report


def print_final_report(report: dict) -> None:
    print("\n========== English Megaphone once-run report ==========")
    print(f"Run ID: {report.get('runId')}")
    print(f"Boundary: {report.get('boundary')}")
    window = report.get("articleWindow") or {}
    print(f"Article window: [{window.get('start')}, {window.get('endExclusive')})")
    print(f"English MP3 path: {report.get('englishMp3Path')}")
    print(f"Public MP3 URL: {report.get('publicMp3Url')}")
    print(f"Megaphone podcast title: {report.get('megaphonePodcastTitle')}")
    print(f"Megaphone podcast ID: {report.get('megaphonePodcastId')}")
    print(f"Megaphone episode ID: {report.get('megaphoneEpisodeId')}")
    print(f"Final status: {report.get('finalStatus')}")
    print(f"Elapsed time: {report.get('elapsedSeconds')}s")
    print(f"Retryable: {report.get('retryable')}")
    print(f"Failed stage, if any: {report.get('failedStage')}")
    print("======================================================\n")


def load_run_state(run_id: str) -> dict:
    path = get_storage_paths().run(run_id).state_path
    return json.loads(path.read_text(encoding="utf-8"))


def claim_publication(publication_key_value: str, run_id: str) -> None:
    """Deprecated. Once-path ownership uses ``publication_claim.create_claim``.

    Kept as a stub so accidental callers fail closed instead of writing
    ``manifests/megaphone_once/*.claim`` ownership files.
    """
    raise DuplicateEditionError(
        "duplicate_blocked: claim_publication is retired; "
        f"use publication_claim.create_claim for {publication_key_value!r} "
        f"(runId={run_id!r})"
    )


def selection_artifact_path(run_id: str) -> Path:
    return get_storage_paths().run(run_id).selection_path


def stories_artifact_path(run_id: str) -> Path:
    return get_storage_paths().run(run_id).english_stories_path


def manifest_artifact_path(run_id: str) -> Path:
    return get_storage_paths().run(run_id).episode_manifest_path


def megaphone_episode_artifact_path(run_id: str) -> Path:
    return get_storage_paths().run(run_id).megaphone_episode_path


def save_selection_artifact(run_id: str, selection: dict) -> Path:
    path = selection_artifact_path(run_id)
    _atomic_json(path, selection)
    return path


def load_selection_artifact(run_id: str) -> dict:
    path = selection_artifact_path(run_id)
    if not path.exists():
        raise RuntimeError(f"Frozen selection missing for run {run_id}: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_megaphone_episode_id(
    ctx: OnceRunContext,
    *,
    episode_id: str,
    external_id: str,
) -> None:
    """Persist Megaphone episode id immediately and atomically after create."""
    ctx.megaphone_episode_id = episode_id
    payload = {
        "megaphoneEpisodeId": episode_id,
        "externalId": external_id,
        "publicationKey": ctx.publication_key,
        "runId": ctx.run_id,
        "savedAt": datetime.now(UTC).isoformat(),
    }
    _atomic_json(megaphone_episode_artifact_path(ctx.run_id), payload)
    ctx.status = "megaphone_created"
    _persist_state(ctx)


def _valid_mp3(path: Path, minimum_seconds: int) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    try:
        duration = duration_seconds(path)
        validate_publishable_audio(path, duration, minimum_seconds)
        return True
    except Exception:
        return False


def _public_base_issues(public_base: str) -> list[str]:
    return public_base_url_issues(public_base)


def verify_public_audio_url(url: str, *, timeout: float = 45) -> None:
    response = requests.get(url, timeout=timeout, stream=True, allow_redirects=True)
    try:
        if response.status_code != 200:
            raise PublishRejectedError(f"Public MP3 URL returned HTTP {response.status_code}")
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type and not any(content_type.startswith(p) for p in AUDIO_MIME_PREFIXES):
            if "html" in content_type or content_type.startswith("text/"):
                raise PublishRejectedError(
                    f"Public MP3 URL returned non-audio Content-Type: {content_type}"
                )
        length_header = response.headers.get("Content-Length")
        if length_header is not None:
            try:
                if int(length_header) <= 0:
                    raise PublishRejectedError("Public MP3 URL Content-Length is zero")
            except ValueError as err:
                raise PublishRejectedError("Public MP3 URL Content-Length is invalid") from err
        else:
            chunk = next(response.iter_content(chunk_size=2048), b"")
            if not chunk:
                raise PublishRejectedError("Public MP3 URL stream was empty")
    finally:
        response.close()


def run_preflight(
    *,
    boundary: datetime,
    language: str = LANGUAGE,
    minimum_seconds: int = 600,
) -> tuple[list[str], dict[str, Any]]:
    """Return ``(failures, details)``. Empty failures means preflight passed."""
    failures: list[str] = []
    details: dict[str, Any] = {}
    if language != LANGUAGE:
        failures.append(f"Only language '{LANGUAGE}' is supported for this once-run")
        return failures, details

    os.environ["SCHEDULER_ENABLED"] = "false"
    os.environ["CRON_ENABLED"] = "false"

    public_base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    failures.extend(_public_base_issues(public_base))
    details["publicBaseUrl"] = public_base

    for executable in ("ffmpeg", "ffprobe"):
        if shutil.which(executable) is None:
            failures.append(f"{executable} executable not found on PATH")

    try:
        ensure_storage()
        probe = get_storage_paths().root / ".megaphone-once-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as err:
        failures.append(f"STORAGE_PATH not writable: {err}")

    if not os.environ.get("BALVOI_API_KEY", "").strip() and os.environ.get(
        "BALVOI_ALLOW_DEMO_ARTICLES", ""
    ).lower() != "true":
        failures.append("BALVOI_API_KEY is required")
    if not os.environ.get("ELEVENLABS_API_KEY", "").strip():
        failures.append("ELEVENLABS_API_KEY is required")

    token, network, podcast = resolve_megaphone_config(language)
    details["podcastId"] = podcast
    details["networkId"] = network
    if not token:
        failures.append("MEGAPHONE_API_TOKEN (or MEGAPHONE_API_TOKEN_EN) is required")
    if not network:
        failures.append("MEGAPHONE_NETWORK_ID (or MEGAPHONE_NETWORK_ID_EN) is required")
    if not podcast:
        failures.append("MEGAPHONE_PODCAST_ID_EN is required")

    expected = os.environ.get("MEGAPHONE_EXPECTED_PODCAST_TITLE_EN", "").strip()
    if token and network and podcast:
        try:
            show = fetch_podcast(language)
            title = str(show.get("title") or "")
            details["podcastTitle"] = title
            details["podcastId"] = str(show.get("id") or podcast)
            if expected:
                if title.strip().lower() != expected.strip().lower():
                    failures.append(
                        f"Megaphone podcast title {title!r} does not match expected {expected!r}"
                    )
            elif not any(marker in title.lower() for marker in EXPECTED_TITLE_MARKERS):
                failures.append(
                    f"Megaphone podcast title {title!r} does not look like the English show "
                    f"(expected markers: {EXPECTED_TITLE_MARKERS})"
                )
        except Exception as err:  # noqa: BLE001 — collect for preflight report
            failures.append(f"Megaphone English podcast lookup failed: {_safe_error(err)}")

    if edition_was_published(boundary, language):
        failures.append(
            f"English boundary {format_iso_utc(boundary)} is already published locally"
        )

    from pipeline.lib.publication_identity import boundary_key as identity_boundary_key

    lock_path = get_storage_paths().lock_path(identity_boundary_key(boundary), language)
    if lock_path.exists():
        failures.append(f"Conflicting English run lock exists: {lock_path.name}")

    key = publication_key(language, boundary)
    # Ownership is enforced by publication_claim.create_claim later.
    # Legacy manifests/megaphone_once/*.claim files are ignored and do not block.

    try:
        validate_pipeline_config([language], dry_run=False)
    except Exception as err:  # noqa: BLE001
        # Megaphone enabled may be false during preflight; ignore only that if other checks pass.
        message = str(err)
        if "MEGAPHONE" in message and os.environ.get("MEGAPHONE_ENABLED", "").lower() != "true":
            pass
        else:
            failures.append(f"Pipeline config validation: {message}")

    details["publicationKey"] = key
    details["minimumPublishSeconds"] = minimum_seconds
    details["boundary"] = format_iso_utc(boundary)
    return failures, details

def run_english_once(
    *,
    new_run: bool = True,
    resume_run_id: str | None = None,
    boundary: datetime | None = None,
    run_id: str | None = None,
    confirm_live_publish: bool = False,
    preflight_only: bool = False,
    attempt: int = 1,
    skip_claim: bool = False,
    freeze_selection_impl: Callable[..., tuple[list, dict]] | None = None,
    publish_episode_impl: Callable[..., dict | None] | None = None,
    verify_public_url_impl: Callable[[str], None] | None = None,
    render_segments_impl: Callable[..., tuple] | None = None,
    merge_segments_impl: Callable[..., None] | None = None,
    transform_impl: Callable[..., list] | None = None,
    assemble_impl: Callable[..., dict] | None = None,
    headlines_impl: Callable[..., str] | None = None,
    wait_for_boundary: bool = True,
) -> tuple[int, dict]:
    """Execute one English production path. Returns ``(exit_code, report)``.

    Modes:
    - ``new_run=True`` (default): fetch/freeze selection, generate MP3, optionally publish.
    - ``resume_run_id=...``: load frozen selection + artifacts; never refetch articles.
    """
    os.environ["SCHEDULER_ENABLED"] = "false"
    os.environ["CRON_ENABLED"] = "false"
    os.environ["PIPELINE_EDITIONS"] = LANGUAGE

    if resume_run_id:
        new_run = False
        run_id = resume_run_id
        prior = load_run_state(run_id)
        boundary = parse_iso_datetime(str(prior.get("publicationBoundary") or ""))
        if boundary is None:
            raise RuntimeError(f"Invalid publicationBoundary on run {run_id}")
        attempt = max(attempt, int(prior.get("attempt") or 1) + 1)
        stored_key, migrated = normalize_stored_publication_key(
            prior.get("publicationKey"),
            slug=LANGUAGE,
            boundary=boundary,
        )
        if migrated:
            prior["publicationKey"] = stored_key
            _atomic_json(get_storage_paths().run(run_id).state_path, prior)
        # Tier 1.1: resume must still acquire canonical ownership (same-run
        # acquired claims are idempotent; terminal claims fail closed).
        # ``skip_claim`` no longer bypasses ownership.
    elif not new_run:
        raise ValueError("resume_run_id is required when new_run=False")

    boundary = (
        (boundary or publication_boundary())
        .astimezone(UTC)
        .replace(minute=0, second=0, microsecond=0)
    )
    if resume_run_id:
        identity = PublicationIdentity.from_existing(
            boundary=boundary,
            edition_slug=LANGUAGE,
            run_id=run_id,
        )
    else:
        identity = PublicationIdentity.from_boundary(
            boundary, LANGUAGE, run_id=run_id
        )
    run_id = identity.run_id
    ctx = OnceRunContext(
        run_id=run_id,
        boundary=boundary,
        confirm_live_publish=confirm_live_publish,
        preflight_only=preflight_only,
        attempt=attempt,
        reuse_audio=False,
        publication_key=identity.publication_key,
    )
    if resume_run_id:
        prior_state = load_run_state(run_id)
        ctx.megaphone_episode_id = str(prior_state.get("megaphoneEpisodeId") or "")
        ctx.podcast_title = str(prior_state.get("podcastTitle") or "")
        ctx.podcast_id = str(prior_state.get("podcastId") or "")
        ep_path = megaphone_episode_artifact_path(run_id)
        if not ctx.megaphone_episode_id and ep_path.exists():
            ep = json.loads(ep_path.read_text(encoding="utf-8"))
            ctx.megaphone_episode_id = str(ep.get("megaphoneEpisodeId") or "")

    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    window_start, window_end = article_ownership_window(boundary)
    ctx.window_start = format_iso_utc(window_start)
    ctx.window_end = format_iso_utc(window_end)
    _log(ctx, "boundary", "ok", boundary=format_iso_utc(boundary), newRun=new_run)
    _log(
        ctx,
        "article_window",
        "ok",
        windowStart=ctx.window_start,
        windowEndExclusive=ctx.window_end,
    )

    settings = validate_pipeline_config([LANGUAGE], dry_run=False)
    minimum_seconds = settings["minimum_publish_seconds"]

    failures, details = run_preflight(
        boundary=boundary,
        language=LANGUAGE,
        minimum_seconds=minimum_seconds,
    )
    if resume_run_id:
        # Lock files from a prior attempt of this run are expected on resume.
        failures = [
            f for f in failures if "Conflicting English run lock" not in f
        ]
    ctx.podcast_title = ctx.podcast_title or str(details.get("podcastTitle") or "")
    ctx.podcast_id = ctx.podcast_id or str(details.get("podcastId") or "")
    if failures:
        ctx.errors.extend(failures)
        _set_status(ctx, "failed_validation", failed_stage="preflight")
        for item in failures:
            _log(ctx, "preflight", "fail", error=item)
        report = write_report(ctx)
        print_final_report(report)
        print("Preflight failures:")
        for item in failures:
            print(f"  - {item}")
        return 1, report

    _log(ctx, "preflight", "ok", podcastTitle=ctx.podcast_title, podcastId=ctx.podcast_id)
    if preflight_only:
        _set_status(ctx, "pending")
        report = write_report(ctx)
        print_final_report(report)
        return 0, report

    # Exactly one English edition — never instantiate other language editions.
    edition = edition_by_slug(LANGUAGE)
    if not edition or edition.get("slug") != LANGUAGE:
        _set_status(ctx, "failed_validation", failed_stage="edition")
        report = write_report(ctx)
        print_final_report(report)
        return 1, report

    claim_held = False
    lock = EditionLock(boundary, LANGUAGE)
    try:
        # Canonical ownership is always required (Tier 1.1). ``skip_claim`` is
        # retained for call-signature compatibility but ignored.
        _ = skip_claim
        _log(
            ctx,
            "claim",
            "started",
            publicationKey=identity.publication_key,
            note="canonical_claim_acquisition_started",
        )
        claim = create_claim(identity)
        if not claim.acquired:
            owner = (claim.claim or {}).get("runId") if claim.claim else None
            status = (claim.claim or {}).get("status") if claim.claim else None
            msg = (
                f"duplicate_blocked:{claim.reason or 'already_owned'}"
                f" ownerRunId={owner!r} status={status!r}"
            )
            _set_status(ctx, "duplicate_blocked", failed_stage="claim")
            ctx.errors.append(msg)
            _log(
                ctx,
                "claim",
                "fail",
                error=msg,
                reason=claim.reason,
                ownerRunId=owner,
                claimStatus=status,
                note="canonical_claim_already_owned",
            )
            report = write_report(ctx)
            print_final_report(report)
            return 1, report
        claim_held = True
        _log(
            ctx,
            "claim",
            "ok",
            reason=claim.reason,
            publicationKey=identity.publication_key,
            note="canonical_claim_acquired",
        )
        lock.acquire()
    except DuplicateEditionError as err:
        if claim_held:
            fail_claim(identity, error=_safe_error(err))
            claim_held = False
            _log(ctx, "claim", "fail", error=_safe_error(err), note="canonical_claim_failed")
        _set_status(ctx, "duplicate_blocked", failed_stage="claim")
        ctx.errors.append(_safe_error(err))
        _log(ctx, "claim", "fail", error=_safe_error(err))
        report = write_report(ctx)
        print_final_report(report)
        return 1, report

    try:
        public_base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
        out = get_storage_paths().episode_mp3(run_id, LANGUAGE)
        ctx.mp3_path = str(out)
        ctx.public_url = f"{public_base}/episodes/{run_id}/{LANGUAGE}.mp3"

        transform = transform_impl or transform_stories_english
        assemble = assemble_impl or assemble_episode
        headlines_fn = headlines_impl or headlines_segment

        if new_run:
            _set_status(ctx, "generating")
            freeze = freeze_selection_impl or _freeze_selection
            selected, selection = freeze(
                boundary, run_id, settings["story_cooldown_minutes"]
            )
            selection = dict(selection)
            selection.setdefault("selectedArticles", selected)
            selection.setdefault(
                "orderedStoryIds", [str(story["id"]) for story in selected]
            )
            save_selection_artifact(run_id, selection)
            ctx.selection = selection
            ctx.story_ids = list(selection.get("orderedStoryIds") or [])
            _log(
                ctx,
                "articles_fetched",
                "ok",
                storyCount=len(selection.get("selectedArticles") or []),
            )
            record_status(
                run_id=run_id,
                boundary=format_iso_utc(boundary),
                slug=LANGUAGE,
                stage="fetch",
                story_ids=ctx.story_ids,
            )
            if not selected:
                raise RuntimeError(
                    "No unique stories in the hourly ownership window "
                    "(including 2-hour lookback fallback)"
                )
            _log(ctx, "stories_selected", "ok", storyIds=ctx.story_ids)
            record_status(
                run_id=run_id,
                boundary=format_iso_utc(boundary),
                slug=LANGUAGE,
                stage="selection",
                story_ids=ctx.story_ids,
            )
            english = transform(selected, "balvoi60-global")
            if not english:
                raise RuntimeError("Selected stories had no usable body text")
            ctx.stories = english
            _atomic_json(stories_artifact_path(run_id), {"stories": english})
            _log(ctx, "english_transform", "ok", storyCount=len(english))
            headlines = headlines_fn(english, language="English")
            manifest = assemble(
                edition, english, run_id, headlines_text=headlines, when=boundary
            )
            ctx.manifest = manifest
            _atomic_json(manifest_artifact_path(run_id), manifest)
            _log(ctx, "assemble", "ok", segmentCount=len(manifest.get("segments") or []))
            render = render_segments_impl or render_segments
            try:
                seg_paths, _stats = render(manifest, dry_run=False)
            except Exception as err:
                raise RuntimeError(f"English TTS/synthesis failed: {err}") from err
            _log(ctx, "tts", "ok", segmentFiles=len(seg_paths))
            merge = merge_segments_impl or merge_segments
            try:
                merge(seg_paths, out)
            except Exception as err:
                raise MergeError(f"English merge failed: {err}") from err
            if not out.exists():
                raise AudioValidationError(f"Missing English MP3 after merge: {out}")
            duration = duration_seconds(out)
            audio_size = validate_publishable_audio(out, duration, minimum_seconds)
            ctx.duration_seconds = duration
            ctx.audio_size = audio_size
            _set_status(ctx, "generated")
            _log(ctx, "merge", "ok", path=str(out), duration=duration, size=audio_size)
        else:
            # Resume: load frozen selection; never refetch the article window.
            selection = load_selection_artifact(run_id)
            selected = list(selection.get("selectedArticles") or [])
            ctx.selection = selection
            ctx.story_ids = list(selection.get("orderedStoryIds") or [])
            _log(
                ctx,
                "selection_loaded",
                "ok",
                storyIds=ctx.story_ids,
                source="run_artifact",
            )
            if not selected:
                raise RuntimeError(f"Frozen selection for run {run_id} has no stories")

            stories_path = stories_artifact_path(run_id)
            if stories_path.exists():
                ctx.stories = list(
                    json.loads(stories_path.read_text(encoding="utf-8")).get("stories") or []
                )
            if not ctx.stories:
                ctx.stories = transform(selected, "balvoi60-global")
                _atomic_json(stories_artifact_path(run_id), {"stories": ctx.stories})

            manifest_path = manifest_artifact_path(run_id)
            if manifest_path.exists():
                ctx.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            else:
                headlines = headlines_fn(ctx.stories, language="English")
                ctx.manifest = assemble(
                    edition,
                    ctx.stories,
                    run_id,
                    headlines_text=headlines,
                    when=boundary,
                )
                _atomic_json(manifest_artifact_path(run_id), ctx.manifest)

            reuse_audio = _valid_mp3(out, minimum_seconds)
            ctx.reuse_audio = reuse_audio
            if reuse_audio:
                duration = duration_seconds(out)
                audio_size = validate_publishable_audio(out, duration, minimum_seconds)
                ctx.duration_seconds = duration
                ctx.audio_size = audio_size
                _set_status(ctx, "audio_validated")
                _log(ctx, "reuse_audio", "ok", path=str(out), duration=duration, size=audio_size)
            else:
                _set_status(ctx, "generating")
                # Regenerate from frozen stories only (no article refetch).
                headlines = headlines_fn(ctx.stories, language="English")
                ctx.manifest = assemble(
                    edition,
                    ctx.stories,
                    run_id,
                    headlines_text=headlines,
                    when=boundary,
                )
                _atomic_json(manifest_artifact_path(run_id), ctx.manifest)
                _log(ctx, "assemble", "ok", segmentCount=len(ctx.manifest.get("segments") or []))
                render = render_segments_impl or render_segments
                try:
                    seg_paths, _stats = render(ctx.manifest, dry_run=False)
                except Exception as err:
                    raise RuntimeError(f"English TTS/synthesis failed: {err}") from err
                _log(ctx, "tts", "ok", segmentFiles=len(seg_paths))
                merge = merge_segments_impl or merge_segments
                try:
                    merge(seg_paths, out)
                except Exception as err:
                    raise MergeError(f"English merge failed: {err}") from err
                if not out.exists():
                    raise AudioValidationError(f"Missing English MP3 after merge: {out}")
                duration = duration_seconds(out)
                audio_size = validate_publishable_audio(out, duration, minimum_seconds)
                ctx.duration_seconds = duration
                ctx.audio_size = audio_size
                _set_status(ctx, "generated")
                _log(ctx, "merge", "ok", path=str(out), duration=duration, size=audio_size)

        _set_status(ctx, "audio_validated")
        _log(
            ctx,
            "audio_validated",
            "ok",
            path=ctx.mp3_path,
            duration=ctx.duration_seconds,
            size=ctx.audio_size,
        )
        record_status(
            run_id=run_id,
            boundary=format_iso_utc(boundary),
            slug=LANGUAGE,
            stage="validation",
            story_ids=ctx.story_ids,
            output_path=ctx.mp3_path,
            audio_size=ctx.audio_size,
            duration=ctx.duration_seconds,
        )

        if wait_for_boundary:
            wait_until_publication_boundary(boundary)
            _log(ctx, "publication_gate", "ok")

        verify = verify_public_url_impl or verify_public_audio_url
        try:
            verify(ctx.public_url)
        except Exception as err:
            _set_status(ctx, "failed_public_url", failed_stage="public_url")
            ctx.errors.append(_safe_error(err))
            _log(ctx, "public_url", "fail", error=_safe_error(err), url=ctx.public_url)
            report = write_report(ctx)
            print_final_report(report)
            return 1, report
        _set_status(ctx, "public_url_verified")
        _log(ctx, "public_url", "ok", url=ctx.public_url)

        if not confirm_live_publish:
            ctx.retryable = True
            ctx.failed_stage = "megaphone_confirm"
            _log(
                ctx,
                "megaphone",
                "blocked",
                error="Missing --confirm-live-publish; stopping before Megaphone create",
            )
            report = write_report(ctx)
            print_final_report(report)
            print("Stopped before Megaphone publish (pass --confirm-live-publish to continue).")
            return 2, report

        stories = ctx.stories
        already_created = bool(ctx.megaphone_episode_id)
        if already_created:
            _log(
                ctx,
                "megaphone_reconcile",
                "ok",
                megaphoneEpisodeId=ctx.megaphone_episode_id,
                note="Using saved episode id; create not called",
            )
            _set_status(ctx, "megaphone_created")
        else:
            os.environ["MEGAPHONE_ENABLED"] = "true"
            _set_status(ctx, "publishing")
            publish_fn = publish_episode_impl or publish_episode
            try:
                upload = publish_fn(
                    boundary=boundary,
                    slug=LANGUAGE,
                    title=production_episode_title(edition, stories, boundary),
                    summary=production_episode_summary(
                        edition,
                        stories,
                        boundary,
                        existing_summary=load_existing_episode_summary(
                            ctx.run_id, LANGUAGE
                        ),
                    ),
                    audio_path=out,
                    public_audio_url=ctx.public_url,
                    run_id=ctx.run_id,
                )
            except Exception as err:
                _set_status(ctx, "failed_megaphone", failed_stage="megaphone")
                ctx.errors.append(_safe_error(err))
                _log(ctx, "megaphone", "fail", error=_safe_error(err))
                report = write_report(ctx)
                print_final_report(report)
                return 1, report

            if not upload or not upload.get("id"):
                _set_status(ctx, "failed_megaphone", failed_stage="megaphone")
                ctx.errors.append("Megaphone create returned no episode id")
                _log(ctx, "megaphone", "fail", error="no episode id")
                report = write_report(ctx)
                print_final_report(report)
                return 1, report

            # Persist episode id immediately before any further processing.
            save_megaphone_episode_id(
                ctx,
                episode_id=str(upload["id"]),
                external_id=str(upload.get("externalId") or ctx.publication_key),
            )
            if str(upload.get("externalId") or "") != ctx.publication_key:
                _log(
                    ctx,
                    "megaphone",
                    "warn",
                    error=(
                        f"externalId mismatch: got {upload.get('externalId')!r} "
                        f"expected {ctx.publication_key!r}"
                    ),
                )
            _log(
                ctx,
                "megaphone",
                "created",
                megaphoneEpisodeId=ctx.megaphone_episode_id,
                externalId=upload.get("externalId"),
                publicationKey=ctx.publication_key,
            )
            # Create success is not local publication success.
            _set_status(ctx, "audio_fetch_pending")
            _log(ctx, "megaphone_ingest", "pending")

        episode = publish_run(
            run_id,
            edition,
            ctx.manifest,
            out,
            ctx.duration_seconds,
            stories,
            publication_boundary=boundary,
            minimum_duration_seconds=minimum_seconds,
        )
        complete_claim(identity)
        claim_held = False
        _log(
            ctx,
            "claim",
            "ok",
            note="canonical_claim_completed",
            publicationKey=identity.publication_key,
        )
        _set_status(ctx, "published")
        ctx.retryable = False
        _log(ctx, "local_publish", "ok", audioUrl=episode.get("audioUrl"))
        record_status(
            run_id=run_id,
            boundary=format_iso_utc(boundary),
            slug=LANGUAGE,
            stage="published",
            story_ids=ctx.story_ids,
            output_path=ctx.mp3_path,
            audio_size=ctx.audio_size,
            duration=ctx.duration_seconds,
            elapsed_seconds=time.monotonic() - ctx.started_monotonic,
        )
        report = write_report(ctx)
        print_final_report(report)
        return 0, report
    except DuplicateEditionError as err:
        if claim_held:
            fail_claim(identity, error=_safe_error(err))
            claim_held = False
            _log(ctx, "claim", "fail", error=_safe_error(err), note="canonical_claim_failed")
        _set_status(ctx, "duplicate_blocked", failed_stage="duplicate")
        ctx.errors.append(_safe_error(err))
        _log(ctx, "duplicate", "fail", error=_safe_error(err))
        report = write_report(ctx)
        print_final_report(report)
        return 1, report
    except (AudioValidationError, MergeError) as err:
        if claim_held:
            fail_claim(identity, error=_safe_error(err))
            claim_held = False
            _log(ctx, "claim", "fail", error=_safe_error(err), note="canonical_claim_failed")
        _set_status(ctx, "failed_validation", failed_stage="validation")
        ctx.errors.append(_safe_error(err))
        _log(ctx, "validation", "fail", error=_safe_error(err))
        report = write_report(ctx)
        print_final_report(report)
        return 1, report
    except Exception as err:
        if claim_held:
            proof = try_recover_already_published_claim(identity, err)
            if proof is not None:
                completed = complete_claim(identity)
                if completed is not None:
                    claim_held = False
                    _log(
                        ctx,
                        "claim",
                        "ok",
                        note="canonical_claim_completed_from_existing_publication",
                        publicationKey=identity.publication_key,
                        megaphoneEpisodeId=proof.episode_id,
                    )
                    if not ctx.megaphone_episode_id:
                        ctx.megaphone_episode_id = proof.episode_id
                    _set_status(ctx, "published")
                    ctx.retryable = False
                    _log(
                        ctx,
                        "local_publish",
                        "ok",
                        note="already_published",
                        megaphoneEpisodeId=proof.episode_id,
                        audioUrl=f"/episodes/{run_id}/{LANGUAGE}.mp3",
                    )
                    record_status(
                        run_id=run_id,
                        boundary=format_iso_utc(boundary),
                        slug=LANGUAGE,
                        stage="published",
                        story_ids=ctx.story_ids,
                        output_path=ctx.mp3_path,
                        audio_size=ctx.audio_size,
                        duration=ctx.duration_seconds,
                        elapsed_seconds=time.monotonic() - ctx.started_monotonic,
                    )
                    report = write_report(ctx)
                    print_final_report(report)
                    return 0, report
                _log(
                    ctx,
                    "claim",
                    "fail",
                    note="already_published_claim_complete_failed",
                    publicationKey=identity.publication_key,
                    megaphoneEpisodeId=proof.episode_id,
                )
            fail_claim(identity, error=_safe_error(err))
            claim_held = False
            _log(ctx, "claim", "fail", error=_safe_error(err), note="canonical_claim_failed")
        stage = "generation"
        status = "failed_generation"
        if "TTS" in str(err) or "synthesis" in str(err).lower():
            stage = "tts"
        _set_status(ctx, status, failed_stage=stage)
        ctx.errors.append(_safe_error(err))
        _log(ctx, stage, "fail", error=_safe_error(err))
        record_status(
            run_id=run_id,
            boundary=format_iso_utc(boundary),
            slug=LANGUAGE,
            stage="failed_validation",
            story_ids=ctx.story_ids,
            error=_safe_error(err),
        )
        report = write_report(ctx)
        print_final_report(report)
        return 1, report
    finally:
        lock.release()


def retry_english_run(
    *,
    run_id: str,
    confirm_live_publish: bool = False,
    publish_episode_impl: Callable[..., dict | None] | None = None,
    verify_public_url_impl: Callable[[str], None] | None = None,
    render_segments_impl: Callable[..., tuple] | None = None,
    merge_segments_impl: Callable[..., None] | None = None,
    transform_impl: Callable[..., list] | None = None,
    assemble_impl: Callable[..., dict] | None = None,
) -> tuple[int, dict]:
    """Resume an existing English once-run without refetching articles."""
    state = load_run_state(run_id)
    if state.get("language") not in (None, LANGUAGE, "en", "English"):
        raise RuntimeError("Retry is English-only")
    status = str(state.get("status") or "")
    if status in {"published", "processed"}:
        report_path = get_storage_paths().run(run_id).report_path
        report = (
            json.loads(report_path.read_text(encoding="utf-8"))
            if report_path.exists()
            else {**state, "finalStatus": status}
        )
        print_final_report(report if "finalStatus" in report else {**state, "finalStatus": status})
        return 0, report if "finalStatus" in report else state

    boundary = parse_iso_datetime(str(state.get("publicationBoundary") or ""))
    if boundary is None:
        raise RuntimeError("Stored publicationBoundary is invalid")
    canonical, migrated = normalize_stored_publication_key(
        state.get("publicationKey"), slug=LANGUAGE, boundary=boundary
    )
    if migrated or state.get("publicationKey") != canonical:
        state["publicationKey"] = canonical
        _atomic_json(get_storage_paths().run(run_id).state_path, state)

    return run_english_once(
        new_run=False,
        resume_run_id=run_id,
        boundary=boundary,
        confirm_live_publish=confirm_live_publish,
        publish_episode_impl=publish_episode_impl,
        verify_public_url_impl=verify_public_url_impl,
        render_segments_impl=render_segments_impl,
        merge_segments_impl=merge_segments_impl,
        transform_impl=transform_impl,
        assemble_impl=assemble_impl,
        wait_for_boundary=False,
    )
