"""Hourly publish review report (one JSON per publication boundary).

Written under ``storage/manifests/reports/<run_id>.json`` and indexed in
``storage/manifests/hourly_report.json`` so operators can review what published
(and why not) after a scheduler stretch.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from balvoi.dates import format_iso_utc, parse_iso_datetime
from pipeline.config_loader import edition_by_slug
from pipeline.lib.megaphone_client import ALL_SLUGS, production_episode_title
from pipeline.lib.storage_paths import get_storage_paths

_PUBLISHED_STAGES = frozenset({"published"})
_ROLLUP_LIMIT = 48
_DEFAULT_SLUGS = tuple(ALL_SLUGS)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _description_from_headlines(headlines: list[Any]) -> str:
    titles = [str(h).strip() for h in headlines if str(h).strip()]
    if not titles:
        return ""
    lines = ["This edition covers:", ""]
    lines.extend(f"{i}. {title}" for i, title in enumerate(titles, start=1))
    return "\n".join(lines)


def _default_episode_title(boundary: datetime) -> str:
    return production_episode_title({}, [], boundary)


def build_hourly_report(
    *,
    run_id: str,
    boundary: datetime | str,
    edition_slugs: list[str] | None = None,
) -> dict[str, Any]:
    """Assemble a review report for one publication hour across all editions.

    Always includes every slug in ``ALL_SLUGS`` (en, es, pt, fr, de, ar, ru, tr)
    unless ``edition_slugs`` is explicitly provided. Each language gets published
    details or a failure ``reason``.
    """
    if isinstance(boundary, str):
        parsed = parse_iso_datetime(boundary)
        if parsed is None:
            raise ValueError(f"invalid boundary: {boundary!r}")
        boundary_dt = parsed
    else:
        boundary_dt = boundary
    boundary_dt = boundary_dt.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    boundary_text = format_iso_utc(boundary_dt)

    slugs = list(edition_slugs) if edition_slugs else list(_DEFAULT_SLUGS)
    # Keep canonical order; drop unknowns; never silently shrink to one language.
    ordered = [s for s in _DEFAULT_SLUGS if s in slugs]
    ordered.extend(s for s in slugs if s not in ordered)

    paths = get_storage_paths()
    editions: dict[str, Any] = {}
    published_count = 0
    failed_count = 0

    for slug in ordered:
        status = _read_json(paths.edition_status_path(run_id, slug), {})
        episode = _read_json(paths.published_episode_manifest(run_id, slug), {})
        stage = str(status.get("stage") or "").strip()
        error = str(status.get("error") or "").strip()
        is_published = stage in _PUBLISHED_STAGES and bool(episode)

        edition_meta = edition_by_slug(slug) or {}
        podcast_title = str(
            episode.get("editionName")
            or edition_meta.get("editionName")
            or edition_meta.get("name")
            or slug
        ).strip()
        episode_title = _default_episode_title(boundary_dt)
        anchor = str(episode.get("anchor") or "").strip()
        description = _description_from_headlines(list(episode.get("headlines") or []))
        if not description and status.get("storyIds"):
            description = f"storyIds={len(status.get('storyIds') or [])}"

        row: dict[str, Any] = {
            "slug": slug,
            "published": is_published,
            "publicationBoundary": boundary_text,
            "stage": stage or None,
            "podcastTitle": podcast_title,
            "episodeTitle": episode_title,
            "anchor": anchor or None,
            "description": description or None,
            "publishedAt": status.get("publishedAt") or episode.get("timestamp"),
            "durationSeconds": status.get("durationSeconds") or episode.get("durationSeconds"),
            "audioUrl": episode.get("audioUrl"),
            "megaphoneEpisodeId": status.get("megaphoneEpisodeId"),
            "publicAudioUrl": status.get("publicAudioUrl"),
            "reason": None,
        }

        if is_published:
            published_count += 1
        else:
            failed_count += 1
            if error:
                row["reason"] = error
            elif stage:
                row["reason"] = f"not published (last stage: {stage})"
            else:
                row["reason"] = "not published (no status recorded)"

        editions[slug] = row

    return {
        "generatedAt": datetime.now(UTC).isoformat(),
        "runId": run_id,
        "publicationBoundary": boundary_text,
        "languages": ordered,
        "editions": editions,
        "summary": {
            "editionCount": len(ordered),
            "publishedCount": published_count,
            "notPublishedCount": failed_count,
        },
    }


def write_hourly_report(
    *,
    run_id: str,
    boundary: datetime | str,
    edition_slugs: list[str] | None = None,
) -> Path:
    """Write per-hour report JSON (all languages) and refresh the rolling index."""
    report = build_hourly_report(
        run_id=run_id,
        boundary=boundary,
        edition_slugs=edition_slugs,
    )
    paths = get_storage_paths()
    reports_dir = paths.manifests_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{run_id}.json"
    _atomic_json(path, report)

    index_path = paths.manifests_root / "hourly_report.json"
    index = _read_json(index_path, {"hours": []})
    if not isinstance(index, dict):
        index = {"hours": []}
    hours = [h for h in list(index.get("hours") or []) if isinstance(h, dict)]
    hours = [h for h in hours if h.get("runId") != run_id]
    hours.insert(
        0,
        {
            "runId": run_id,
            "publicationBoundary": report["publicationBoundary"],
            "generatedAt": report["generatedAt"],
            "reportPath": f"manifests/reports/{run_id}.json",
            "languages": report["languages"],
            "summary": report["summary"],
        },
    )
    index = {
        "updatedAt": report["generatedAt"],
        "hours": hours[:_ROLLUP_LIMIT],
    }
    _atomic_json(index_path, index)
    return path
