"""Megaphone episode publication with remote duplicate verification."""

from __future__ import annotations

import ipaddress
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from balvoi.dates import format_iso_utc, parse_iso_datetime
from pipeline.errors import PublishRejectedError
from pipeline.lib.edition_lock import edition_was_published
from pipeline.lib.storage_paths import get_storage_paths

BASE_URL = "https://cms.megaphone.fm/api"
ALL_SLUGS = ("en", "es", "pt", "fr", "de", "ar", "ru", "tr")

# Documented Apiary Episode.episodeType enum.
API_EPISODE_TYPES = frozenset({"full", "trailer", "bonus"})

# Documented Apiary Episode.explicit enum (strings — not booleans).
API_EXPLICIT_VALUES = frozenset({"no", "clean", "yes"})

# Non-explicit production default maps to documented ``no``.
PRODUCTION_EPISODE_TYPE = "full"
PRODUCTION_EXPLICIT = "no"

PLACEHOLDER_HOSTS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "example.com",
        "www.example.com",
        "podcast.example.com",
    }
)


def enabled() -> bool:
    return os.environ.get("MEGAPHONE_ENABLED", "").strip().lower() == "true"


def create_as_draft() -> bool:
    """Whether Create Episode should set draft=true.

    Controlled by ``MEGAPHONE_CREATE_AS_DRAFT``. Default ``true`` for the first
    controlled test (unset or empty → draft).
    """
    raw = os.environ.get("MEGAPHONE_CREATE_AS_DRAFT", "true").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def _env_for_slug(name: str, slug: str) -> str:
    """Prefer per-slug env var, then shared org-level fallback."""
    specific = os.environ.get(f"{name}_{slug.upper()}", "").strip()
    if specific:
        return specific
    return os.environ.get(name, "").strip()


def resolve_megaphone_config(slug: str) -> tuple[str, str, str]:
    """Return ``(token, network_id, podcast_id)`` for one edition slug."""
    token = _env_for_slug("MEGAPHONE_API_TOKEN", slug)
    network = _env_for_slug("MEGAPHONE_NETWORK_ID", slug)
    podcast = os.environ.get(f"MEGAPHONE_PODCAST_ID_{slug.upper()}", "").strip()
    return token, network, podcast


def _config(slug: str) -> tuple[str, str, str]:
    token, network, podcast = resolve_megaphone_config(slug)
    if not all((token, network, podcast)):
        raise PublishRejectedError(f"Missing Megaphone account configuration for language {slug}")
    return token, network, podcast


def publication_key(slug: str, boundary: datetime) -> str:
    """Deterministic local/remote publication identity: balvoi60:<slug>:<boundary_utc>."""
    from pipeline.lib.publication_identity import publication_key as _publication_key

    return _publication_key(slug, boundary)


def _host_is_private_or_local(host: str) -> bool:
    """True for localhost / loopback / literal private IPs (no DNS lookup)."""
    hostname = (host or "").strip().lower().rstrip(".")
    if not hostname:
        return True
    if hostname in PLACEHOLDER_HOSTS or hostname.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def public_base_url_issues(public_base: str | None = None) -> list[str]:
    """Return validation failures for PUBLIC_BASE_URL (no HTTP)."""
    raw = (
        public_base
        if public_base is not None
        else os.environ.get("PUBLIC_BASE_URL", "")
    ).strip().rstrip("/")
    issues: list[str] = []
    if not raw:
        issues.append(
            "PUBLIC_BASE_URL is empty. Set it to the real public HTTPS origin that "
            "serves generated MP3 files (expected media URL shape: "
            "{PUBLIC_BASE_URL}/episodes/{run_id}/{slug}.mp3)."
        )
        return issues
    parsed = urlparse(raw)
    if parsed.scheme != "https":
        issues.append(
            "PUBLIC_BASE_URL must be the real public HTTPS origin serving the "
            "generated MP3 (scheme must be https)."
        )
    host = (parsed.hostname or "").lower()
    if not host:
        issues.append("PUBLIC_BASE_URL is missing a hostname")
        return issues
    if host in PLACEHOLDER_HOSTS or host.endswith(".example.com") or host.endswith(".example.org"):
        issues.append(
            f"PUBLIC_BASE_URL host is a placeholder ({host}). "
            "Do not use example.com / podcast.example.com / localhost. "
            "Set PUBLIC_BASE_URL to the real public HTTPS origin that serves "
            "{PUBLIC_BASE_URL}/episodes/{run_id}/{slug}.mp3."
        )
    elif _host_is_private_or_local(host):
        issues.append(
            f"PUBLIC_BASE_URL must not be localhost, loopback, or a private/non-public "
            f"address (got host={host}). Megaphone must be able to fetch the MP3 "
            "from the public internet."
        )
    return issues


def require_public_base_url(public_base: str | None = None) -> str:
    """Return a validated PUBLIC_BASE_URL or raise PublishRejectedError."""
    raw = (
        public_base
        if public_base is not None
        else os.environ.get("PUBLIC_BASE_URL", "")
    ).strip().rstrip("/")
    issues = public_base_url_issues(raw)
    if issues:
        raise PublishRejectedError("; ".join(issues))
    return raw


def build_episode_create_payload(
    *,
    boundary: datetime,
    slug: str,
    title: str,
    summary: str,
    public_audio_url: str,
    draft: bool | None = None,
) -> dict:
    """Exact JSON body sent by ``publish_episode`` to Create Episode (no HTTP).

    ``explicit`` uses the documented Apiary string enum (``no``|``clean``|``yes``),
    not a boolean. Non-explicit production episodes use ``no``.
    """
    episode_type = PRODUCTION_EPISODE_TYPE
    explicit = PRODUCTION_EXPLICIT
    if episode_type not in API_EPISODE_TYPES:
        raise PublishRejectedError(f"Invalid episodeType for Megaphone API: {episode_type!r}")
    if explicit not in API_EXPLICIT_VALUES:
        raise PublishRejectedError(f"Invalid explicit for Megaphone API: {explicit!r}")
    draft_flag = create_as_draft() if draft is None else bool(draft)
    return {
        "title": title,
        "cleanTitle": title,
        "summary": summary,
        "pubdate": format_iso_utc(boundary),
        "mediaFileUrl": public_audio_url,
        "externalId": publication_key(slug, boundary),
        "episodeType": episode_type,
        "explicit": explicit,
        "draft": draft_flag,
    }


def production_episode_title(
    edition: dict,
    stories: list[dict],
    boundary: datetime,
) -> str:
    """Fixed hourly edition title from the publication boundary (UTC).

    Does not use story titles, wall clock, or the article window.
    ``edition`` / ``stories`` are retained for call-site compatibility.
    """
    _ = (edition, stories)
    hour = boundary.astimezone(UTC).replace(second=0, microsecond=0)
    # Portable hour without leading zero (%-I / %#I differ by platform).
    clock = hour.strftime("%I:%M %p").lstrip("0")
    return f"BalVoi:60 — {clock} UTC News"


def production_episode_summary(
    edition: dict,
    stories: list[dict],
    boundary: datetime,
    *,
    existing_summary: str | None = None,
) -> str:
    """Build a non-empty episode summary from frozen story titles / existing metadata.

    Prefer an existing production summary when present. Otherwise list original
    article titles in selection order — never refetch or rewrite with OpenAI.
    """
    _ = (edition, boundary)
    reused = str(existing_summary or "").strip()
    if reused:
        return reused

    titles = [str(story.get("title") or "").strip() for story in stories]
    titles = [t for t in titles if t]
    if not titles:
        raise PublishRejectedError(
            "Episode summary cannot be empty: frozen selection has no story titles"
        )
    lines = ["This edition covers:", ""]
    lines.extend(f"{index}. {title}" for index, title in enumerate(titles, start=1))
    summary = "\n".join(lines)
    if not summary.strip():
        raise PublishRejectedError("Episode summary cannot be empty")
    return summary


def production_public_audio_url(*, public_base: str, run_id: str, slug: str) -> str:
    """Same media URL shape used by production before ``publish_episode``."""
    return f"{public_base.rstrip('/')}/episodes/{run_id}/{slug}.mp3"


def local_episode_mp3_path(run_id: str, slug: str) -> Path:
    return get_storage_paths().episode_mp3(run_id, slug)


def selection_path(run_id: str) -> Path:
    return get_storage_paths().run(run_id).selection_path


def english_stories_path(run_id: str) -> Path:
    return get_storage_paths().run(run_id).english_stories_path


def published_episode_manifest_path(run_id: str, slug: str) -> Path:
    return get_storage_paths().published_episode_manifest(run_id, slug)


def production_selection_manifest_path(
    *,
    run_id: str | None = None,
    boundary: datetime | None = None,
) -> Path:
    """Hourly pipeline frozen selection: ``manifests/selection/<boundary_key>.json``."""
    from pipeline.lib.edition_lock import boundary_key

    paths = get_storage_paths()
    if run_id:
        return paths.production_selection_manifest(run_id=run_id)
    if boundary is not None:
        return paths.production_selection_manifest(boundary_key=boundary_key(boundary))
    raise ValueError("run_id or boundary is required")


def _stories_from_selection_payload(payload: dict, *, slug: str) -> list[dict]:
    selected = list(payload.get("selectedArticles") or [])
    if selected:
        return selected
    editions = payload.get("editions") or {}
    if isinstance(editions, dict):
        for key in (slug, "en", "balvoi60-global"):
            block = editions.get(key)
            if isinstance(block, dict):
                stories = list(block.get("stories") or [])
                if stories:
                    return stories
            elif isinstance(block, list) and block:
                return list(block)
    return []


def load_frozen_stories(run_id: str, *, slug: str = "en") -> tuple[list[dict], str]:
    """Load stories only from frozen run artifacts (never fetch remotely)."""
    stories_file = english_stories_path(run_id)
    if stories_file.is_file():
        raw = json.loads(stories_file.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return list(raw), str(stories_file)
        stories = list(raw.get("stories") or [])
        return stories, str(stories_file)

    run_sel = selection_path(run_id)
    if run_sel.is_file():
        selection = json.loads(run_sel.read_text(encoding="utf-8"))
        stories = _stories_from_selection_payload(selection, slug=slug)
        return stories, str(run_sel)

    prod_sel = production_selection_manifest_path(run_id=run_id)
    if prod_sel.is_file():
        selection = json.loads(prod_sel.read_text(encoding="utf-8"))
        stories = _stories_from_selection_payload(selection, slug=slug)
        return stories, str(prod_sel)

    return [], (
        f"missing (expected {stories_file} or {run_sel} or {prod_sel})"
    )


def frozen_selection_exists(run_id: str, *, boundary: datetime | None = None) -> tuple[bool, str]:
    candidates = [
        selection_path(run_id),
        english_stories_path(run_id),
        production_selection_manifest_path(run_id=run_id),
    ]
    if boundary is not None:
        candidates.append(production_selection_manifest_path(boundary=boundary))
    for path in candidates:
        if path.is_file():
            return True, str(path)
    return False, str(candidates[0])


def load_existing_episode_summary(run_id: str, slug: str) -> str | None:
    """Reuse published episode metadata when present (headlines → summary seed)."""
    path = published_episode_manifest_path(run_id, slug)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for key in ("summary", "description"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    headlines = [str(h).strip() for h in (data.get("headlines") or []) if str(h).strip()]
    if headlines:
        name = str(data.get("name") or "BalVoi:60").strip()
        boundary = str(data.get("publicationBoundary") or "").strip()
        overview = "; ".join(headlines[:8])
        if boundary:
            return f"{name} — {boundary} UTC. This hour's briefing covers: {overview}."
        return f"{name}. This hour's briefing covers: {overview}."
    return None


def evaluate_run_ready_for_megaphone(
    *,
    run_id: str,
    slug: str,
    edition: dict,
    boundary: datetime | None = None,
    check_media_url: bool = False,
    media_timeout: float = 30.0,
) -> dict[str, Any]:
    """Local readiness report for an existing run (no Megaphone HTTP)."""
    run_dir = get_storage_paths().run(run_id).run_root
    sel_exists, sel_path = frozen_selection_exists(run_id, boundary=boundary)
    stories, stories_source = load_frozen_stories(run_id, slug=slug)
    mp3 = local_episode_mp3_path(run_id, slug)
    mp3_exists = mp3.is_file()
    mp3_size = mp3.stat().st_size if mp3_exists else 0

    resolved_boundary = boundary
    state_path = get_storage_paths().run(run_id).state_path
    if resolved_boundary is None and state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            resolved_boundary = parse_iso_datetime(str(state.get("publicationBoundary") or ""))
        except (OSError, json.JSONDecodeError, TypeError):
            resolved_boundary = None
    if resolved_boundary is None:
        # Production run_id form: YYYY-MM-DDTHH-MM-SSZ
        try:
            resolved_boundary = datetime.strptime(run_id, "%Y-%m-%dT%H-%M-%SZ").replace(
                tzinfo=UTC
            )
        except ValueError:
            resolved_boundary = None

    existing_summary = load_existing_episode_summary(run_id, slug)
    # If selection has no story dicts but published headlines exist, reuse those
    # titles only for summary/title composition (still requires frozen selection file).
    if not stories and existing_summary:
        manifest = published_episode_manifest_path(run_id, slug)
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                headlines = [
                    str(h).strip() for h in (data.get("headlines") or []) if str(h).strip()
                ]
                if headlines:
                    stories = [{"title": h} for h in headlines]
                    stories_source = f"manifest-headlines:{manifest}"
            except (OSError, json.JSONDecodeError):
                pass

    title = ""
    summary = ""
    summary_error = ""
    if stories and resolved_boundary is not None:
        title = production_episode_title(edition, stories, resolved_boundary)
        try:
            summary = production_episode_summary(
                edition,
                stories,
                resolved_boundary,
                existing_summary=existing_summary,
            )
        except PublishRejectedError as err:
            summary_error = str(err)

    public_issues = public_base_url_issues()
    public_base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    media_url = (
        production_public_audio_url(public_base=public_base, run_id=run_id, slug=slug)
        if public_base
        else ""
    )

    run_exists = (
        run_dir.is_dir()
        or sel_exists
        or mp3_exists
        or published_episode_manifest_path(run_id, slug).is_file()
    )
    checks: list[tuple[str, bool, str]] = [
        ("run exists", run_exists, run_id),
        ("frozen selection exists", sel_exists, sel_path),
        ("story count > 0", len(stories) > 0, str(len(stories))),
        ("MP3 exists", mp3_exists, str(mp3)),
        ("MP3 non-empty", mp3_size > 0, str(mp3_size)),
        ("title non-empty", bool(str(title).strip()), title or "(empty)"),
        ("summary non-empty", bool(str(summary).strip()), summary or summary_error or "(empty)"),
        (
            "public base URL is not a placeholder",
            not public_issues,
            "; ".join(public_issues) if public_issues else public_base,
        ),
    ]

    media_probe: dict[str, Any] | None = None
    if check_media_url:
        if media_url and not public_issues:
            media_probe = probe_media_file_url(media_url, timeout=media_timeout)
            reachable = media_probe.get("ok") is True
            checks.append(
                (
                    "media URL reachable",
                    reachable,
                    (
                        f"HTTP {media_probe.get('status')} "
                        f"type={media_probe.get('contentType')} "
                        f"length={media_probe.get('contentLength')} "
                        f"redirects={media_probe.get('redirected')}"
                    ),
                )
            )
        else:
            checks.append(
                (
                    "media URL reachable",
                    False,
                    "skipped: PUBLIC_BASE_URL invalid or media URL empty",
                )
            )

    payload: dict | None = None
    payload_error = ""
    can_build = (
        resolved_boundary is not None
        and title.strip()
        and summary.strip()
        and media_url
        and not public_issues
        and len(stories) > 0
        and mp3_exists
        and mp3_size > 0
        and sel_exists
    )
    if can_build:
        try:
            payload = build_episode_create_payload(
                boundary=resolved_boundary,
                slug=slug,
                title=title,
                summary=summary,
                public_audio_url=media_url,
            )
        except PublishRejectedError as err:
            payload_error = str(err)

    if payload is not None:
        checks.append(("episodeType present", "episodeType" in payload, str(payload.get("episodeType"))))
        checks.append(("explicit present", "explicit" in payload, str(payload.get("explicit"))))
        checks.append(("draft present", "draft" in payload, str(payload.get("draft"))))
        try:
            json.dumps(payload)
            checks.append(("payload JSON valid", True, "ok"))
        except (TypeError, ValueError) as err:
            checks.append(("payload JSON valid", False, str(err)))
    else:
        checks.append(("episodeType present", False, payload_error or "payload not built"))
        checks.append(("explicit present", False, payload_error or "payload not built"))
        checks.append(("draft present", False, payload_error or "payload not built"))
        checks.append(("payload JSON valid", False, payload_error or "payload not built"))

    ready = all(ok for _name, ok, _detail in checks)
    return {
        "runId": run_id,
        "slug": slug,
        "boundary": format_iso_utc(resolved_boundary) if resolved_boundary else None,
        "storiesSource": stories_source,
        "storyCount": len(stories),
        "stories": stories,
        "mp3Path": str(mp3),
        "mp3Size": mp3_size,
        "title": title,
        "summary": summary,
        "publicBaseUrl": public_base,
        "mediaFileUrl": media_url,
        "publicBaseIssues": public_issues,
        "payload": payload,
        "checks": checks,
        "ready": ready,
        "mediaProbe": media_probe,
        "existingSummaryReused": bool(existing_summary and summary == existing_summary),
        "draft": create_as_draft(),
        "megaphonePostCalled": False,
    }


def require_run_ready_for_megaphone(
    *,
    run_id: str,
    slug: str,
    edition: dict,
    boundary: datetime,
    audio_path: Path,
    title: str,
    summary: str,
    public_audio_url: str,
) -> None:
    """Raise PublishRejectedError when the run is incomplete for Megaphone create."""
    failures: list[str] = []
    sel_ok, sel_path = frozen_selection_exists(run_id, boundary=boundary)
    if not sel_ok:
        failures.append(f"frozen story selection missing for run {run_id} ({sel_path})")
    stories, _source = load_frozen_stories(run_id, slug=slug)
    if len(stories) <= 0:
        # Allow in-memory publish when selection file exists but stories were
        # already transformed; still require non-empty title/summary below.
        # Prefer disk stories; if empty, fail closed.
        failures.append(f"story count is zero for run {run_id}")
    mp3 = Path(audio_path)
    if not mp3.is_file():
        failures.append(f"final English MP3 missing: {mp3}")
    elif mp3.stat().st_size <= 0:
        failures.append(f"final English MP3 is empty: {mp3}")
    if not str(title or "").strip():
        failures.append("episode title is empty")
    if not str(summary or "").strip():
        failures.append("episode summary is empty")
    failures.extend(public_base_url_issues())
    parsed = urlparse(str(public_audio_url or ""))
    if parsed.scheme != "https" or not parsed.netloc:
        failures.append(
            "mediaFileUrl must be a public HTTPS URL of the form "
            f"{{PUBLIC_BASE_URL}}/episodes/{run_id}/{slug}.mp3"
        )
    if failures:
        raise PublishRejectedError(
            "Megaphone create rejected — incomplete run: " + "; ".join(failures)
        )


def probe_media_file_url(url: str, *, timeout: float = 30.0) -> dict[str, Any]:
    """HEAD/GET the public media URL only (never Megaphone)."""
    result: dict[str, Any] = {
        "url": url,
        "ok": False,
        "status": None,
        "contentType": None,
        "contentLength": None,
        "redirected": False,
        "finalUrl": url,
        "method": None,
        "error": None,
    }
    try:
        head = requests.head(url, timeout=timeout, allow_redirects=True)
        result["method"] = "HEAD"
        result["status"] = head.status_code
        result["contentType"] = (head.headers.get("Content-Type") or "").split(";")[0].strip()
        result["contentLength"] = head.headers.get("Content-Length")
        result["redirected"] = bool(head.history)
        result["finalUrl"] = str(head.url)
        # Some hosts reject HEAD; fall back to streamed GET.
        if head.status_code >= 400 or head.status_code == 405:
            head.close()
            get = requests.get(url, timeout=timeout, stream=True, allow_redirects=True)
            result["method"] = "GET"
            result["status"] = get.status_code
            result["contentType"] = (get.headers.get("Content-Type") or "").split(";")[0].strip()
            result["contentLength"] = get.headers.get("Content-Length")
            result["redirected"] = bool(get.history)
            result["finalUrl"] = str(get.url)
            get.close()
        else:
            head.close()
        status = int(result["status"] or 0)
        result["ok"] = 200 <= status < 400
    except requests.RequestException as err:
        result["error"] = f"{type(err).__name__}: {err}"
    return result


# Hosts that reject HEAD should be rechecked with a streamed GET (no full download).
_HEAD_UNSUPPORTED_STATUSES = frozenset({405, 501})
DEFAULT_MEDIA_VERIFY_TIMEOUT = 30.0


def verify_media_file_url(
    url: str,
    *,
    timeout: float = DEFAULT_MEDIA_VERIFY_TIMEOUT,
) -> dict[str, Any]:
    """Ensure ``mediaFileUrl`` is publicly reachable before Megaphone create (T-M2-003).

    Prefers HEAD; falls back to streamed GET when HEAD is unsupported. Accepts only
    final HTTP 200. Raises ``PublishRejectedError`` on failure. Never POSTs to Megaphone.
    """
    from pipeline.lib.logging_utils import log_event

    text = str(url or "").strip()
    log_event(
        "Media Verification Started",
        stage="upload",
        mediaFileUrl=text or None,
        timeoutSeconds=timeout,
    )
    if not text:
        log_event(
            "Media Verification Failed",
            stage="upload",
            reason="empty_url",
        )
        raise PublishRejectedError("Public media URL is empty")

    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        log_event(
            "Media Verification Failed",
            stage="upload",
            mediaFileUrl=text,
            reason="malformed_url",
        )
        raise PublishRejectedError(f"Public media URL is malformed: {text!r}")

    method = "HEAD"
    status: int | None = None
    final_url = text
    try:
        response = requests.head(text, timeout=timeout, allow_redirects=True)
        method = "HEAD"
        status = int(response.status_code)
        final_url = str(response.url)
        response.close()
        if status in _HEAD_UNSUPPORTED_STATUSES:
            response = requests.get(
                text, timeout=timeout, stream=True, allow_redirects=True
            )
            method = "GET"
            status = int(response.status_code)
            final_url = str(response.url)
            response.close()
    except requests.Timeout as err:
        log_event(
            "Media Verification Timeout",
            stage="upload",
            mediaFileUrl=text,
            method=method,
            timeoutSeconds=timeout,
            errorType=type(err).__name__,
            errorMessage=str(err),
        )
        raise PublishRejectedError(
            f"Public media URL verification timed out after {timeout}s: {text}"
        ) from err
    except requests.RequestException as err:
        log_event(
            "Media Verification Failed",
            stage="upload",
            mediaFileUrl=text,
            method=method,
            errorType=type(err).__name__,
            errorMessage=str(err),
        )
        raise PublishRejectedError(
            f"Public media URL verification failed: {type(err).__name__}: {err}"
        ) from err

    if status != 200:
        reason = "http_4xx" if status is not None and 400 <= status < 500 else (
            "http_5xx" if status is not None and status >= 500 else "http_unexpected"
        )
        log_event(
            "Media Verification Failed",
            stage="upload",
            mediaFileUrl=text,
            finalUrl=final_url,
            method=method,
            httpStatus=status,
            reason=reason,
        )
        raise PublishRejectedError(
            f"Public media URL returned HTTP {status} ({method}): {text}"
        )

    probe = {
        "url": text,
        "ok": True,
        "status": status,
        "method": method,
        "finalUrl": final_url,
    }
    log_event(
        "Media Verification Passed",
        stage="upload",
        mediaFileUrl=text,
        finalUrl=final_url,
        method=method,
        httpStatus=status,
    )
    return probe


def validate_episode_create_payload(payload: dict) -> list[tuple[str, bool, str]]:
    """Local field checks for the production Create Episode body (no HTTP)."""
    checks: list[tuple[str, bool, str]] = []
    title = str(payload.get("title") or "")
    summary = str(payload.get("summary") or "")
    media = str(payload.get("mediaFileUrl") or "")
    external_id = str(payload.get("externalId") or "")
    pubdate = str(payload.get("pubdate") or "")

    checks.append(("title not empty", bool(title.strip()), title or "(empty)"))
    checks.append(("summary not empty", bool(summary.strip()), summary or "(empty)"))
    checks.append(("mediaFileUrl present", bool(media.strip()), media or "(empty)"))
    parsed = urlparse(media)
    checks.append(
        (
            "mediaFileUrl is HTTPS",
            parsed.scheme == "https" and bool(parsed.netloc),
            media or "(empty)",
        )
    )
    checks.append(
        ("externalId present", bool(external_id.strip()), external_id or "(empty)")
    )
    parsed_pub = parse_iso_datetime(pubdate)
    checks.append(
        (
            "pubdate valid ISO-8601",
            parsed_pub is not None,
            pubdate or "(empty)",
        )
    )
    episode_type = payload.get("episodeType")
    checks.append(
        (
            "episodeType present",
            episode_type in API_EPISODE_TYPES,
            str(episode_type),
        )
    )
    explicit = payload.get("explicit")
    checks.append(
        (
            "explicit present",
            explicit in API_EXPLICIT_VALUES,
            str(explicit),
        )
    )
    checks.append(("draft present", "draft" in payload and isinstance(payload.get("draft"), bool), str(payload.get("draft"))))
    serializable = False
    serialize_detail = ""
    try:
        serialize_detail = json.dumps(payload, ensure_ascii=False)
        serializable = True
    except (TypeError, ValueError) as err:
        serialize_detail = f"{type(err).__name__}: {err}"
    checks.append(("payload serializes successfully", serializable, serialize_detail[:200]))
    return checks


def legacy_publication_key(slug: str, boundary: datetime) -> str:
    """Old key order retained only for reading/migrating prior local records."""
    from pipeline.lib.publication_identity import legacy_publication_key as _legacy

    return _legacy(slug, boundary)


def normalize_stored_publication_key(
    value: str | None,
    *,
    slug: str,
    boundary: datetime,
) -> tuple[str, bool]:
    """Return ``(canonical_key, migrated_from_legacy)``."""
    from pipeline.lib.publication_identity import normalize_stored_publication_key as _normalize

    return _normalize(value, slug=slug, boundary=boundary)


def fetch_podcast(slug: str, *, timeout: float = 45) -> dict:
    """GET one podcast show (read-only). Raises on HTTP/config errors."""
    token, network, podcast = _config(slug)
    endpoint = f"{BASE_URL}/networks/{network}/podcasts/{podcast}"
    headers = {"Authorization": f'Token token="{token}"', "Accept": "application/json"}
    response = requests.get(endpoint, headers=headers, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("id"):
        raise PublishRejectedError(f"Megaphone podcast lookup returned no id for {slug}")
    return payload


def publish_episode(
    *,
    boundary: datetime,
    slug: str,
    title: str,
    summary: str,
    audio_path: Path,
    public_audio_url: str,
    run_id: str | None = None,
    draft: bool | None = None,
) -> dict | None:
    """Create one episode unless a local or remote publication already exists.

    Control flow (T-M2-001 / T-M2-003 / T-M2-004):

    1. Load local Megaphone publication result → reuse (no reconcile / verify / POST)
    2. Reconcile remote episodes by exact ``externalId`` (full pagination)
       → persist recovered result and return (no verify / POST)
    3. Verify public ``mediaFileUrl``
    4. POST create episode and persist the result

    ``draft`` defaults to ``create_as_draft()``. Main-path live publish at the
    boundary passes ``draft=False`` (no draft-then-undraft flow).
    """
    if not enabled():
        return None

    from balvoi.dates import publication_delay_seconds
    from pipeline.config_loader import edition_by_slug
    from pipeline.lib.logging_utils import log_event
    from pipeline.lib.megaphone_publication_result import (
        load_publication_result,
        result_as_upload,
        save_publication_result,
    )
    from pipeline.lib.megaphone_reconciliation import (
        find_remote_episodes_by_external_id,
        persist_reconciled_result,
    )
    from pipeline.lib.publication_identity import PublicationIdentity

    identity = PublicationIdentity.from_boundary(boundary, slug, run_id=run_id)
    resolved_run_id = identity.run_id

    existing_result = load_publication_result(identity)
    if existing_result is not None:
        log_event(
            "Megaphone Publish Completed",
            stage="upload",
            publicationKey=identity.publication_key,
            runId=resolved_run_id,
            slug=identity.edition_slug,
            megaphoneEpisodeId=existing_result.get("megaphoneEpisodeId"),
            reused=True,
            publicationDelaySeconds=existing_result.get("publicationDelaySeconds"),
        )
        return result_as_upload(existing_result)

    edition = edition_by_slug(slug) or {"name": "BalVoi:60", "slug": slug}
    # Prefer a guaranteed non-empty summary from frozen selection when caller
    # passed a blank string (should not happen in production paths).
    resolved_summary = str(summary or "").strip()
    if not resolved_summary:
        stories, _ = load_frozen_stories(resolved_run_id, slug=slug)
        existing = load_existing_episode_summary(resolved_run_id, slug)
        resolved_summary = production_episode_summary(
            edition,
            stories,
            boundary,
            existing_summary=existing,
        )

    require_public_base_url()
    require_run_ready_for_megaphone(
        run_id=resolved_run_id,
        slug=slug,
        edition=edition,
        boundary=boundary,
        audio_path=Path(audio_path),
        title=title,
        summary=resolved_summary,
        public_audio_url=public_audio_url,
    )

    token, network, podcast = _config(slug)
    endpoint = f"{BASE_URL}/networks/{network}/podcasts/{podcast}/episodes"
    headers = {"Authorization": f'Token token="{token}"', "Accept": "application/json"}
    draft_flag = create_as_draft() if draft is None else bool(draft)
    body = build_episode_create_payload(
        boundary=boundary,
        slug=slug,
        title=title,
        summary=resolved_summary,
        public_audio_url=public_audio_url,
        draft=draft_flag,
    )
    external_id = body["externalId"]

    if edition_was_published(boundary, slug):
        raise PublishRejectedError("Megaphone upload rejected: already_published")

    # T-M2-004: recover remote create when local artifact is missing.
    remote = find_remote_episodes_by_external_id(
        endpoint=endpoint,
        headers=headers,
        external_id=external_id,
        identity=identity,
    )
    if remote.matched and remote.episode is not None:
        upload = persist_reconciled_result(
            identity,
            episode=remote.episode,
            media_file_url=str(body.get("mediaFileUrl") or public_audio_url),
        )
        log_event(
            "Megaphone Publish Completed",
            stage="upload",
            publicationKey=identity.publication_key,
            runId=resolved_run_id,
            slug=identity.edition_slug,
            megaphoneEpisodeId=upload.get("id"),
            reused=True,
            reconciled=True,
            publicationDelaySeconds=upload.get("publicationDelaySeconds"),
        )
        return upload

    # Exact mediaFileUrl that will be POSTed — fail closed before Megaphone create.
    verify_media_file_url(str(body.get("mediaFileUrl") or public_audio_url))

    try:
        response = requests.post(
            endpoint,
            headers={**headers, "Content-Type": "application/json"},
            json=body,
            timeout=90,
        )
        response.raise_for_status()
        payload = response.json()
    except PublishRejectedError:
        raise
    except (requests.RequestException, ValueError) as err:
        status = getattr(getattr(err, "response", None), "status_code", None)
        detail = type(err).__name__
        if status is not None:
            detail = f"HTTP_{status}"
        raise PublishRejectedError(f"Megaphone publication failed: {detail}") from err
    if not isinstance(payload, dict) or not payload.get("id"):
        raise PublishRejectedError("Megaphone publication returned no episode ID")

    success_at = datetime.now(UTC)
    delay = publication_delay_seconds(boundary, success_at=success_at)
    # Persist before returning so crash-after-POST retries can skip recreate.
    saved = save_publication_result(
        identity,
        megaphone_episode_id=str(payload["id"]),
        media_file_url=public_audio_url,
        megaphone_response=payload if isinstance(payload, dict) else None,
        source="created",
        published_at=success_at,
        publication_delay_seconds=delay,
    )
    return {
        "id": payload["id"],
        "externalId": external_id,
        "reused": False,
        "draft": draft_flag,
        "publishedAt": saved.get("publishedAt"),
        "publicationDelaySeconds": saved.get("publicationDelaySeconds"),
    }


def replace_episode_media(
    *,
    slug: str,
    episode_id: str,
    public_audio_url: str,
    retain_ad_locations: bool = True,
) -> dict:
    """PUT a new ``mediaFileUrl`` onto an existing Megaphone episode.

    Used when local audio is regenerated after the episode was already created
    (same ``externalId`` / episode id — never creates a second episode).
    Verifies the public URL first; never POSTs Create Episode.
    """
    from pipeline.lib.logging_utils import log_event

    episode = str(episode_id or "").strip()
    if not episode:
        raise PublishRejectedError("Megaphone episode id is required to replace media")
    media = str(public_audio_url or "").strip()
    if not media:
        raise PublishRejectedError("Public media URL is empty")

    verify_media_file_url(media)
    token, network, podcast = _config(slug)
    endpoint = (
        f"{BASE_URL}/networks/{network}/podcasts/{podcast}/episodes/{episode}"
    )
    headers = {
        "Authorization": f'Token token="{token}"',
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    body = {
        "mediaFileUrl": media,
        "retainAdLocations": bool(retain_ad_locations),
    }
    log_event(
        "Megaphone Media Replace Started",
        stage="upload",
        slug=slug,
        megaphoneEpisodeId=episode,
        mediaFileUrl=media,
        retainAdLocations=bool(retain_ad_locations),
    )
    try:
        response = requests.put(endpoint, headers=headers, json=body, timeout=90)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as err:
        status = getattr(getattr(err, "response", None), "status_code", None)
        detail = type(err).__name__
        if status is not None:
            detail = f"HTTP_{status}"
        log_event(
            "Megaphone Media Replace Failed",
            stage="upload",
            slug=slug,
            megaphoneEpisodeId=episode,
            mediaFileUrl=media,
            error=detail,
        )
        raise PublishRejectedError(
            f"Megaphone media replace failed: {detail}"
        ) from err
    if not isinstance(payload, dict) or not payload.get("id"):
        raise PublishRejectedError("Megaphone media replace returned no episode ID")
    log_event(
        "Megaphone Media Replace Completed",
        stage="upload",
        slug=slug,
        megaphoneEpisodeId=str(payload.get("id") or episode),
        mediaFileUrl=media,
        audioFileStatus=payload.get("audioFileStatus"),
        audioFileProcessing=payload.get("audioFileProcessing"),
        duration=payload.get("duration"),
    )
    return payload


def set_episode_draft(
    *,
    slug: str,
    episode_id: str,
    draft: bool,
) -> dict:
    """PUT ``draft`` on an existing Megaphone episode (never creates)."""
    from pipeline.lib.logging_utils import log_event

    episode = str(episode_id or "").strip()
    if not episode:
        raise PublishRejectedError("Megaphone episode id is required to update draft")
    token, network, podcast = _config(slug)
    endpoint = (
        f"{BASE_URL}/networks/{network}/podcasts/{podcast}/episodes/{episode}"
    )
    headers = {
        "Authorization": f'Token token="{token}"',
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    body = {"draft": bool(draft)}
    log_event(
        "Megaphone Draft Update Started",
        stage="upload",
        slug=slug,
        megaphoneEpisodeId=episode,
        draft=bool(draft),
    )
    try:
        response = requests.put(endpoint, headers=headers, json=body, timeout=90)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as err:
        status = getattr(getattr(err, "response", None), "status_code", None)
        detail = type(err).__name__
        if status is not None:
            detail = f"HTTP_{status}"
        log_event(
            "Megaphone Draft Update Failed",
            stage="upload",
            slug=slug,
            megaphoneEpisodeId=episode,
            draft=bool(draft),
            error=detail,
        )
        raise PublishRejectedError(
            f"Megaphone draft update failed: {detail}"
        ) from err
    if not isinstance(payload, dict) or not payload.get("id"):
        raise PublishRejectedError("Megaphone draft update returned no episode ID")
    log_event(
        "Megaphone Draft Update Completed",
        stage="upload",
        slug=slug,
        megaphoneEpisodeId=str(payload.get("id") or episode),
        draft=payload.get("draft"),
        status=payload.get("status"),
    )
    return payload

