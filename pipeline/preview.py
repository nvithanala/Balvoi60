"""Real, publication-isolated generation of all BalVoi:60 language previews."""

from __future__ import annotations

import concurrent.futures
import hashlib
import html
import json
import os
import shutil
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from balvoi.config import is_english
from balvoi.dates import article_ownership_window, format_iso_utc
from balvoi.paths import storage_root
from pipeline.config_loader import edition_by_slug
from pipeline.errors import AudioValidationError, LocalizationError, MergeError
from pipeline.lib import concurrency, elevenlabs_client, openai_client
from pipeline.lib.story_history import recently_used_story_ids
from pipeline.stages.assemble_episode import assemble_episode
from pipeline.stages.fetch_articles import fetch_articles
from pipeline.stages.merge_audio import (
    duration_seconds,
    merge_segments,
    validate_publishable_audio,
)
from pipeline.stages.select_stories import select_stories
from pipeline.stages.synthesize import render_segments
from pipeline.stages.transform_stories import (
    headlines_segment,
    localize_stories,
    transform_stories_english,
)

LANGUAGE_FILENAMES = {
    "en": "English",
    "es": "Spanish",
    "pt": "Portuguese",
    "fr": "French",
    "de": "German",
    "ar": "Arabic",
    "ru": "Russian",
    "tr": "Turkish",
}
PREVIEW_STATUSES = {
    "preview_ready",
    "preview_failed_translation",
    "preview_failed_synthesis",
    "preview_failed_merge",
    "preview_failed_validation",
}


def _json_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_state(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "files": {}}
    files = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
    return {
        "exists": True,
        "files": {
            str(item): {
                "mtimeUtc": datetime.fromtimestamp(item.stat().st_mtime, UTC).isoformat(),
                "size": item.stat().st_size,
                "sha256": _sha256_file(item),
            }
            for item in files
        },
    }


def _dir_fingerprint(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "mtimeUtc": None, "fileCount": 0}
    files = [item for item in path.rglob("*") if item.is_file()]
    newest = max((item.stat().st_mtime for item in files), default=path.stat().st_mtime)
    return {
        "exists": True,
        "mtimeUtc": datetime.fromtimestamp(newest, UTC).isoformat(),
        "fileCount": len(files),
    }


def production_state() -> dict:
    """Snapshot production publication assets and shared generation caches."""
    root = storage_root()
    rss_files = sorted({*root.glob("*.xml"), *(root / "feeds").glob("**/*.xml")})
    return {
        "capturedAt": datetime.now(UTC).isoformat(),
        "liveRss": {
            "exists": bool(rss_files),
            "files": {
                str(path): {
                    "mtimeUtc": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                    "size": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
                for path in rss_files
            },
        },
        "history": _tree_state(root / "manifests" / "history.json"),
        "latest": _tree_state(root / "manifests" / "latest.json"),
        "publicationStatus": _tree_state(root / "manifests" / "status"),
        "locks": _tree_state(root / "locks"),
        "articlesCache": _tree_state(root / "articles" / "latest.json"),
        "productionTtsCache": _dir_fingerprint(root / "cache" / "tts"),
        "productionReusableCache": _dir_fingerprint(root / "audio_assets" / "reusable"),
    }


def _state_unchanged(before: dict, after: dict, key: str) -> bool:
    return before[key].get("files") == after[key].get("files")


def _manifest_payload(
    *,
    run_id: str,
    boundary: datetime,
    window_start: datetime,
    window_end: datetime,
    selected: list[dict],
    decisions: list[dict],
    selected_at: datetime,
) -> dict:
    payload = {
        "previewRunId": run_id,
        "publicationBoundary": format_iso_utc(boundary),
        "articleWindow": {
            "startInclusive": format_iso_utc(window_start),
            "endExclusive": format_iso_utc(window_end),
        },
        "selectionTimestamp": selected_at.isoformat(),
        "orderedStoryIds": [str(story["id"]) for story in selected],
        "stories": [
            {
                "id": str(story["id"]),
                "title": str(story.get("title") or ""),
                "canonicalUrl": str(story.get("url") or ""),
                "sourceName": str(story.get("source") or ""),
            }
            for story in selected
        ],
        "selectionDecisions": decisions,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    payload["manifestHash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def _script_text(manifest: dict) -> str:
    parts = []
    for index, segment in enumerate(manifest["segments"], 1):
        text = str(segment.get("text") or "").strip()
        if text:
            parts.append(f"[{index:02d} {segment['segmentType']}]\n{text}")
        else:
            parts.append(
                f"[{index:02d} {segment['segmentType']} — prerecorded audio]\n"
                f"{segment.get('path', '')}"
            )
    return "\n\n".join(parts) + "\n"


def _artwork_for(edition: dict) -> str:
    if edition.get("artworkUrl"):
        return str(edition["artworkUrl"])
    base = os.environ.get("PODCAST_ARTWORK_BASE_URL", "").rstrip("/")
    return f"{base}/{edition['slug']}.jpg" if base else ""


def _megaphone_destination(slug: str) -> str:
    podcast_id = os.environ.get(f"MEGAPHONE_PODCAST_ID_{slug.upper()}", "").strip()
    return podcast_id or "Not configured"


def _language_result(
    edition: dict,
    english: list[dict],
    *,
    run_id: str,
    preview_dir: Path,
    boundary: datetime,
    manifest_hash: str,
    minimum_seconds: int,
) -> dict:
    slug = edition["slug"]
    language = LANGUAGE_FILENAMES[slug]
    started = time.monotonic()
    stage = "translation"
    timings = {
        "translationSeconds": 0.0,
        "assemblySeconds": 0.0,
        "synthesisSeconds": 0.0,
        "mergeSeconds": 0.0,
        "validationSeconds": 0.0,
    }
    result = {
        "language": language,
        "slug": slug,
        "status": "preview_failed_validation",
        "audioPath": "",
        "fileSize": 0,
        "durationSeconds": 0,
        "storyCount": len(english),
        "manifestHash": manifest_hash,
        "voice": {},
        "translationCompleted": False,
        "mergeCompleted": False,
        "minimumDurationValidation": "not_run",
        "publicationStatus": "PREVIEW — NOT PUBLISHED",
        "error": None,
        "timing": timings,
    }
    try:
        stage_started = time.monotonic()
        stories = english if is_english(edition["language"]) else localize_stories(
            english, edition["language"]
        )
        timings["translationSeconds"] = round(time.monotonic() - stage_started, 3)
        result["translationCompleted"] = True

        stage_started = time.monotonic()
        headlines = headlines_segment(stories)
        episode_manifest = assemble_episode(
            edition,
            stories,
            run_id,
            headlines_text=headlines,
            when=boundary,
        )
        timings["assemblySeconds"] = round(time.monotonic() - stage_started, 3)
        stage = "synthesis"
        result["voice"] = {
            "name": episode_manifest["voice"].get("name"),
            "voiceId": episode_manifest["voice"].get("voiceId"),
            "configuredLanguageCode": slug,
            "locale": edition.get("locale"),
            "elevenLabsModel": elevenlabs_client.MODEL_ID,
            "selectedForBoundary": format_iso_utc(boundary),
        }
        story_order_matches = episode_manifest["storyIds"] == [story["id"] for story in english]
        translated_without_fallback = is_english(edition["language"]) or all(
            str(localized.get("broadcastScript") or "").strip()
            != str(source.get("broadcastScript") or "").strip()
            for localized, source in zip(stories, english, strict=True)
        )
        segment_types = [segment["segmentType"] for segment in episode_manifest["segments"]]
        intro_outro_match = (
            "intro_welcome" in segment_types
            and "intro_dynamic" in segment_types
            and "outro" in segment_types
        )

        script_path = preview_dir / "scripts" / f"{language}.txt"
        script_path.write_text(_script_text(episode_manifest), encoding="utf-8")

        stage_started = time.monotonic()
        preview_cache = preview_dir / "cache"
        segment_paths = render_segments(
            episode_manifest,
            tts_cache_root=preview_cache / "tts",
            reusable_write_root=preview_cache / "reusable",
            reusable_read_roots=[
                preview_cache / "reusable",
                storage_root() / "audio_assets" / "reusable",
            ],
        )
        timings["synthesisSeconds"] = round(time.monotonic() - stage_started, 3)
        required_segments_present = (
            len(segment_paths) == len(episode_manifest["segments"])
            and all(path.exists() and path.stat().st_size > 0 for path in segment_paths)
        )
        zero_byte_segments = [
            str(path) for path in segment_paths if not path.exists() or path.stat().st_size <= 0
        ]
        consecutive_duplicates = [
            str(current)
            for previous, current in zip(segment_paths, segment_paths[1:])
            if previous.resolve() == current.resolve()
        ]
        if not required_segments_present:
            raise AudioValidationError("One or more required audio segments are missing or empty")

        stage = "merge"
        output = preview_dir / "audio" / f"BalVoi60_{language}.mp3"
        stage_started = time.monotonic()
        merge_segments(segment_paths, output)
        timings["mergeSeconds"] = round(time.monotonic() - stage_started, 3)
        result["mergeCompleted"] = True
        result["audioPath"] = str(output)
        result["fileSize"] = output.stat().st_size

        stage = "validation"
        stage_started = time.monotonic()
        duration = duration_seconds(output)
        result["durationSeconds"] = duration
        try:
            validate_publishable_audio(output, duration, minimum_seconds)
            result["minimumDurationValidation"] = "passed"
            result["status"] = "preview_ready"
        except AudioValidationError as err:
            result["minimumDurationValidation"] = "failed"
            result["status"] = "preview_failed_validation"
            result["error"] = str(err)
        timings["validationSeconds"] = round(time.monotonic() - stage_started, 3)

        title = f"{edition['name']} — {format_iso_utc(boundary)}"
        description = " • ".join(str(story.get("title") or "") for story in stories)
        metadata = {
            **result,
            "podcastName": edition["name"],
            "episodeTitle": title,
            "episodeDescription": description,
            "publicationBoundary": format_iso_utc(boundary),
            "storyHeadlines": [str(story.get("title") or "") for story in stories],
            "storyIds": [str(story["id"]) for story in stories],
            "audioFilename": output.name,
            "artwork": _artwork_for(edition),
            "megaphonePodcastDestination": _megaphone_destination(slug),
            "verification": {
                "configuredVoiceUsed": bool(result["voice"].get("voiceId")),
                "configuredLanguageCode": slug,
                "elevenLabsModel": elevenlabs_client.MODEL_ID,
                "languageEnforcedByTranslatedText": True,
                "introOutroFromLanguageConfiguration": intro_outro_match,
                "noEnglishFallback": translated_without_fallback,
                "storyOrderMatchesManifest": story_order_matches,
                "allRequiredSegmentsPresent": required_segments_present,
                "zeroByteSegments": zero_byte_segments,
                "consecutiveDuplicateSegments": consecutive_duplicates,
            },
        }
        _json_write(preview_dir / "metadata" / f"{language}.json", metadata)
        result["metadata"] = metadata
    except Exception as err:
        if isinstance(err, LocalizationError) or stage == "translation":
            result["status"] = "preview_failed_translation"
        elif isinstance(err, MergeError) or stage == "merge":
            result["status"] = "preview_failed_merge"
        elif stage == "synthesis":
            result["status"] = "preview_failed_synthesis"
        else:
            result["status"] = "preview_failed_validation"
        result["error"] = f"{type(err).__name__}: {err}"
        # Failed editions still get auditable metadata without any publication record.
        _json_write(preview_dir / "metadata" / f"{language}.json", result)
    finally:
        result["timing"]["totalSeconds"] = round(time.monotonic() - started, 3)
    assert result["status"] in PREVIEW_STATUSES
    return result


def _preview_html(preview_dir: Path, results: list[dict]) -> None:
    cards = []
    for result in results:
        metadata = result.get("metadata") or result
        relative_audio = ""
        if result.get("audioPath"):
            relative_audio = Path(result["audioPath"]).relative_to(preview_dir).as_posix()
        artwork = str(metadata.get("artwork") or "")
        image = (
            f'<img src="{html.escape(artwork)}" alt="{html.escape(result["language"])} artwork">'
            if artwork
            else '<div class="no-art">Artwork not configured</div>'
        )
        headlines = "".join(
            f"<li>{html.escape(str(headline))}</li>"
            for headline in metadata.get("storyHeadlines", [])
        )
        audio = (
            f'<audio controls preload="metadata" src="{html.escape(relative_audio)}"></audio>'
            if relative_audio
            else "<p>No audio was generated.</p>"
        )
        cards.append(
            f"""
            <article class="card">
              <div class="badge">PREVIEW — NOT PUBLISHED</div>
              {image}
              <h2>{html.escape(result["language"])}</h2>
              <h3>{html.escape(str(metadata.get("episodeTitle") or ""))}</h3>
              <p>{html.escape(str(metadata.get("episodeDescription") or result.get("error") or ""))}</p>
              <dl>
                <dt>Status</dt><dd>{html.escape(result["status"])}</dd>
                <dt>Runtime</dt><dd>{result.get("durationSeconds", 0)} seconds</dd>
                <dt>Voice</dt><dd>{html.escape(str((result.get("voice") or {}).get("name") or ""))}</dd>
                <dt>Megaphone destination</dt><dd>{html.escape(str(metadata.get("megaphonePodcastDestination") or "Not configured"))}</dd>
              </dl>
              {audio}
              <h4>Selected headlines</h4><ol>{headlines}</ol>
            </article>
            """
        )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BalVoi:60 Preview</title>
<style>
body{{font-family:system-ui,sans-serif;background:#eef2f7;color:#172033;margin:0;padding:24px}}
h1{{text-align:center}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:20px}}
.card{{background:white;border-radius:14px;padding:18px;box-shadow:0 4px 18px #0002}}
.badge{{background:#8b0000;color:white;font-weight:700;padding:7px 10px;border-radius:6px;display:inline-block}}
img,.no-art{{width:100%;height:180px;object-fit:contain;background:#f5f6f8;margin:14px 0}}
.no-art{{display:grid;place-items:center;color:#687386}}audio{{width:100%}}dt{{font-weight:700;float:left;clear:left;margin-right:8px}}
dd{{margin-bottom:5px}}li{{margin-bottom:6px}}
</style></head><body><h1>BalVoi:60 — Local Preview</h1>
<div class="grid">{''.join(cards)}</div></body></html>"""
    (preview_dir / "preview.html").write_text(document, encoding="utf-8")


def run_preview(
    *,
    run_id: str,
    boundary: datetime,
    edition_slugs: list[str],
    settings: dict[str, int],
) -> tuple[int, Path, dict]:
    """Generate real audio while bypassing all publication and status code paths."""
    if os.environ.get("MEGAPHONE_ENABLED", "").strip().lower() == "true":
        raise RuntimeError("Preview safety check failed: MEGAPHONE_ENABLED must be false")
    if os.environ.get("SCHEDULER_ENABLED", "").strip().lower() == "true":
        raise RuntimeError("Preview safety check failed: SCHEDULER_ENABLED must be false")
    if os.environ.get("CRON_ENABLED", "").strip().lower() == "true":
        raise RuntimeError("Preview safety check failed: CRON_ENABLED must be false")
    if os.environ.get("BALVOI_ALLOW_DEMO_ARTICLES", "").strip().lower() == "true":
        raise RuntimeError("Preview safety check failed: demo articles must be disabled")
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise RuntimeError(
            "Preview safety check failed: OPENAI_API_KEY is required for real translations"
        )

    root = storage_root()
    preview_dir = root / "previews" / run_id
    if preview_dir.exists():
        raise FileExistsError(f"Preview run already exists: {preview_dir}")
    safety_before = production_state()
    for subdirectory in ("scripts", "audio", "metadata", "logs"):
        (preview_dir / subdirectory).mkdir(parents=True, exist_ok=True)

    boundary = boundary.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    window_start, window_end = article_ownership_window(boundary)
    print(f"Preview run ID: {run_id}")
    print(f"Publication boundary: {format_iso_utc(boundary)}")
    print(
        "Article ownership window: "
        f"[{format_iso_utc(window_start)}, {format_iso_utc(window_end)})"
    )

    total_started = time.monotonic()
    concurrency.configure(
        translation=settings["translation_workers"],
        tts=settings["tts_workers"],
        merge=settings["merge_workers"],
    )
    openai_client.reset_metrics()
    elevenlabs_client.reset_metrics()

    fetch_started = time.monotonic()
    articles = fetch_articles(
        window_start,
        window_end,
        cache_path=preview_dir / "logs" / "fetched-articles.json",
    )
    fetch_seconds = round(time.monotonic() - fetch_started, 3)

    selection_started = time.monotonic()
    decisions: list[dict] = []
    selected = select_stories(
        articles,
        "balvoi60-global",
        exclude_ids=recently_used_story_ids(settings["story_cooldown_minutes"]),
        record=decisions,
        window_start=window_start,
        window_end_exclusive=window_end,
    )
    selection_seconds = round(time.monotonic() - selection_started, 3)
    if not selected:
        raise RuntimeError("No unique real stories were selected from the completed ownership window")
    selection_timestamp = datetime.now(UTC)
    manifest = _manifest_payload(
        run_id=run_id,
        boundary=boundary,
        window_start=window_start,
        window_end=window_end,
        selected=selected,
        decisions=decisions,
        selected_at=selection_timestamp,
    )
    _json_write(preview_dir / "manifest.json", manifest)

    english_started = time.monotonic()
    english = transform_stories_english(selected, "balvoi60-global")
    english_seconds = round(time.monotonic() - english_started, 3)
    if not english or [str(story["id"]) for story in english] != manifest["orderedStoryIds"]:
        raise RuntimeError("English transformation did not preserve the frozen manifest story set")

    result_lock = threading.Lock()
    results: list[dict] = []

    def process(slug: str) -> dict:
        result = _language_result(
            edition_by_slug(slug),
            english,
            run_id=run_id,
            preview_dir=preview_dir,
            boundary=boundary,
            manifest_hash=manifest["manifestHash"],
            minimum_seconds=settings["minimum_publish_seconds"],
        )
        with result_lock:
            results.append(result)
        print(f"  [{result['status']}] {result['language']}")
        return result

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=settings["language_workers"],
        thread_name_prefix="balvoi-preview",
    ) as executor:
        futures = [executor.submit(process, slug) for slug in edition_slugs]
        for future in concurrent.futures.as_completed(futures):
            future.result()

    order = {slug: index for index, slug in enumerate(edition_slugs)}
    results.sort(key=lambda item: order[item["slug"]])
    total_seconds = round(time.monotonic() - total_started, 3)
    slowest_language = max(results, key=lambda item: item["timing"]["totalSeconds"])
    stage_values = [
        (f"{result['language']}:{stage}", seconds)
        for result in results
        for stage, seconds in result["timing"].items()
        if stage != "totalSeconds"
    ]
    slowest_stage, slowest_stage_seconds = max(stage_values, key=lambda item: item[1])
    external_metrics = {
        "openai": openai_client.metrics_snapshot(),
        "elevenLabs": elevenlabs_client.metrics_snapshot(),
        "concurrency": concurrency.snapshot(),
    }
    timing = {
        "fetchSeconds": fetch_seconds,
        "selectionSeconds": selection_seconds,
        "englishTransformationSeconds": english_seconds,
        "languages": {result["language"]: result["timing"] for result in results},
        "totalElapsedSeconds": total_seconds,
        "slowestLanguage": {
            "language": slowest_language["language"],
            "seconds": slowest_language["timing"]["totalSeconds"],
        },
        "slowestStage": {"stage": slowest_stage, "seconds": slowest_stage_seconds},
        "peakOpenAIConcurrency": external_metrics["concurrency"][
            "peakConcurrentTranslationRequests"
        ],
        "peakElevenLabsConcurrency": external_metrics["concurrency"][
            "peakConcurrentTtsRequests"
        ],
        "retryCounts": {
            "openai": external_metrics["openai"]["retries"],
            "elevenLabs": external_metrics["elevenLabs"]["retries"],
        },
        "rateLimitResponses": {
            "openai": external_metrics["openai"]["rateLimitResponses"],
            "elevenLabs": external_metrics["elevenLabs"]["rateLimitResponses"],
        },
        "requestCounts": {
            "openai": external_metrics["openai"]["requests"],
            "elevenLabs": external_metrics["elevenLabs"]["requests"],
        },
    }
    _json_write(preview_dir / "timing.json", timing)
    _preview_html(preview_dir, results)

    safety_after = production_state()
    safety = {
        "before": safety_before,
        "after": safety_after,
        "megaphoneUploadsAttempted": False,
        "liveRssModified": not _state_unchanged(safety_before, safety_after, "liveRss"),
        "productionHistoryModified": not _state_unchanged(safety_before, safety_after, "history"),
        "productionLatestModified": not _state_unchanged(safety_before, safety_after, "latest"),
        "productionPublicationStatusModified": not _state_unchanged(
            safety_before, safety_after, "publicationStatus"
        ),
        "productionLocksModified": not _state_unchanged(safety_before, safety_after, "locks"),
        "productionArticlesCacheModified": not _state_unchanged(
            safety_before, safety_after, "articlesCache"
        ),
        "productionTtsCacheModified": (
            safety_before["productionTtsCache"] != safety_after["productionTtsCache"]
        ),
        "productionReusableCacheModified": (
            safety_before["productionReusableCache"] != safety_after["productionReusableCache"]
        ),
    }
    summary = {
        "previewRunId": run_id,
        "publicationBoundary": format_iso_utc(boundary),
        "articleWindow": manifest["articleWindow"],
        "selectedStoryCount": len(selected),
        "selectedStoryTitles": [story["title"] for story in manifest["stories"]],
        "manifestHash": manifest["manifestHash"],
        "publicationStatus": "PREVIEW — NOT PUBLISHED",
        "results": [{key: value for key, value in result.items() if key != "metadata"} for result in results],
        "timing": timing,
        "safety": safety,
    }
    _json_write(preview_dir / "preview-summary.json", summary)
    (preview_dir / "logs" / "preview.log").write_text(
        "\n".join(
            [
                f"previewRunId={run_id}",
                f"publicationBoundary={format_iso_utc(boundary)}",
                f"articleWindow=[{format_iso_utc(window_start)}, {format_iso_utc(window_end)})",
                *(f"{result['language']}={result['status']}" for result in results),
                f"totalElapsedSeconds={total_seconds}",
                "megaphoneUploadsAttempted=false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    shutil.copy2(preview_dir / "preview-summary.json", preview_dir / "logs" / "final-summary.json")
    return (0 if all(result["status"] == "preview_ready" for result in results) else 1), preview_dir, summary
