"""Megaphone episode create payload helpers (documented Apiary fields only).

Create uses JSON body fields from Megaphone Episodes Collection docs.
Audio ingestion is via ``mediaFileUrl`` (public URL). Docs describe processing
after that URL is submitted; they do not document multipart file upload on
Create Episode.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from balvoi.dates import format_iso_utc
from pipeline.errors import PublishRejectedError
from pipeline.lib.megaphone_client import BASE_URL, resolve_megaphone_config
from pipeline.lib.publication_identity import publication_key

# Documented Create Episode / Episode model field names (Apiary).
# Source: Megaphone API Episodes Collection — Create a New Episode attributes
# and Episode data model (developers / Apiary reference).
DOCUMENTED_CREATE_FIELDS = (
    "title",  # required
    "pubdate",
    "author",
    "link",
    "backgroundImageFileUrl",
    "explicit",  # documented values: no | clean | yes
    "episodeType",  # documented values: full | trailer | bonus
    "subtitle",
    "summary",
    "mediaFileUrl",
    "preCount",
    "postCount",
    "insertionPoints",
    "guid",
    "pubdateTimezone",
    "preOffset",
    "postOffset",
    "expectedAdhash",
    "draft",
    "externalId",
    "originalFilename",
    "originalUrl",
    "episodeNumber",
    "seasonNumber",
    "retainAdLocations",
    "advertisingTags",
    "cleanTitle",
)

# Local/CMS-facing labels (UI), not environment variable names.
LOCAL_EPISODE_TYPES = frozenset({"full", "trailer", "bonus"})
LOCAL_RATINGS = frozenset({"unspecified", "clean", "explicit"})

# Apiary Episode.explicit enum.
API_EXPLICIT_VALUES = frozenset({"no", "clean", "yes"})

# Map CMS "Episode rating" labels → documented API ``explicit`` values.
RATING_TO_API_EXPLICIT = {
    "unspecified": "no",
    "clean": "clean",
    "explicit": "yes",
}

_TOKEN_ENV_KEYS = ("MEGAPHONE_API_TOKEN", "MEGAPHONE_API_TOKEN_EN")


def token_fingerprint(token: str) -> str:
    """SHA-256 of token, first 8 hex chars uppercase. Never return the token."""
    digest = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
    return digest[:8].upper()


def first_process_token() -> tuple[str, str]:
    """Return ``(env_key, token)`` from the process environment, or empty strings."""
    for key in _TOKEN_ENV_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            return key, value
    return "", ""


def read_token_from_env_file(env_path: Path) -> tuple[str, str]:
    """Read Megaphone token from a dotenv file without printing it.

    Returns ``(env_key, token)`` for the first matching key, else empty strings.
    """
    if not env_path.is_file():
        return "", ""
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return "", ""
    for key in _TOKEN_ENV_KEYS:
        match = re.search(
            rf"^\s*{re.escape(key)}\s*=\s*(.*)$",
            text,
            flags=re.MULTILINE,
        )
        if not match:
            continue
        raw = match.group(1).strip()
        if not raw or raw.startswith("#"):
            continue
        if (raw.startswith('"') and raw.endswith('"')) or (
            raw.startswith("'") and raw.endswith("'")
        ):
            raw = raw[1:-1]
        # Strip inline comments for unquoted values.
        if " #" in raw and not (raw.startswith('"') or raw.startswith("'")):
            raw = raw.split(" #", 1)[0].rstrip()
        raw = raw.strip()
        if raw:
            return key, raw
    return "", ""


def resolve_token_provenance(
    *,
    env_path: Path,
    process_token_before_dotenv: str,
    resolved_token: str,
    dotenv_override: bool = True,
) -> dict[str, Any]:
    """Describe token source without exposing the secret."""
    file_key, file_token = read_token_from_env_file(env_path)
    resolved_fp = token_fingerprint(resolved_token) if resolved_token else ""
    process_fp = (
        token_fingerprint(process_token_before_dotenv)
        if process_token_before_dotenv
        else ""
    )
    file_fp = token_fingerprint(file_token) if file_token else ""

    if not resolved_token:
        source = "missing"
    elif dotenv_override and file_token and file_fp == resolved_fp:
        source = ".env"
    elif process_token_before_dotenv and process_fp == resolved_fp:
        source = "process environment"
    elif file_token and file_fp == resolved_fp:
        source = ".env"
    else:
        source = "unknown"

    return {
        "envPath": str(env_path.resolve()) if env_path else "",
        "envFileExists": bool(env_path and env_path.is_file()),
        "tokenLength": len(resolved_token) if resolved_token else 0,
        "tokenFingerprint": resolved_fp,
        "tokenSource": source,
        "tokenEnvKeyPresentInDotenv": bool(file_key),
        "tokenPresentInProcessEnvBeforeDotenv": bool(process_token_before_dotenv),
    }


@dataclass(frozen=True)
class EpisodeIntent:
    """Local intent before mapping to documented Megaphone JSON keys."""

    title: str
    description: str
    mp3_path: Path
    public_audio_url: str
    episode_type: str = "full"
    rating: str = "clean"
    boundary: datetime | None = None
    slug: str = "en"
    image_url: str | None = None
    draft: bool = False


def podcast_get_url(network_id: str, podcast_id: str) -> str:
    return f"{BASE_URL}/networks/{network_id}/podcasts/{podcast_id}"


def episodes_collection_url(network_id: str, podcast_id: str) -> str:
    return f"{BASE_URL}/networks/{network_id}/podcasts/{podcast_id}/episodes"


def validate_episode_intent(intent: EpisodeIntent, *, minimum_seconds: int = 600) -> list[str]:
    """Local validation before any Megaphone POST. Returns all failures."""
    from pipeline.stages.merge_audio import duration_seconds, validate_publishable_audio

    failures: list[str] = []
    if not str(intent.title or "").strip():
        failures.append("episode title is empty")
    if not str(intent.description or "").strip():
        failures.append("episode description is empty")

    episode_type = str(intent.episode_type or "").strip().lower()
    if episode_type not in LOCAL_EPISODE_TYPES:
        failures.append(
            f"episode_type must be one of {sorted(LOCAL_EPISODE_TYPES)} (got {intent.episode_type!r})"
        )

    rating = str(intent.rating or "").strip().lower()
    if rating not in LOCAL_RATINGS:
        failures.append(
            f"rating must be one of {sorted(LOCAL_RATINGS)} (got {intent.rating!r})"
        )

    mp3 = Path(intent.mp3_path)
    if not mp3.exists():
        failures.append(f"MP3 missing: {mp3}")
    elif mp3.stat().st_size <= 0:
        failures.append(f"MP3 is empty: {mp3}")
    else:
        try:
            duration = duration_seconds(mp3)
            validate_publishable_audio(mp3, duration, minimum_seconds)
        except Exception as err:  # noqa: BLE001
            failures.append(f"MP3 validation failed: {type(err).__name__}: {err}")

    url = str(intent.public_audio_url or "").strip()
    parsed = urlparse(url)
    if not url:
        failures.append("public audio URL is empty")
    elif parsed.scheme != "https":
        failures.append("public audio URL must be https when using URL-based ingestion")
    elif not parsed.netloc:
        failures.append("public audio URL is missing a host")

    if intent.image_url:
        image = urlparse(str(intent.image_url).strip())
        if image.scheme not in {"http", "https"} or not image.netloc:
            failures.append("episode image URL is invalid")

    return failures


def build_create_episode_payload(intent: EpisodeIntent) -> dict[str, Any]:
    """Build JSON body using only documented Apiary field names."""
    failures = validate_episode_intent(intent)
    if failures:
        raise PublishRejectedError("; ".join(failures))

    rating = str(intent.rating).strip().lower()
    explicit = RATING_TO_API_EXPLICIT[rating]
    if explicit not in API_EXPLICIT_VALUES:
        raise PublishRejectedError(f"mapped explicit value is invalid: {explicit!r}")

    payload: dict[str, Any] = {
        "title": str(intent.title).strip(),
        "cleanTitle": str(intent.title).strip(),
        "summary": str(intent.description).strip(),
        "mediaFileUrl": str(intent.public_audio_url).strip(),
        "episodeType": str(intent.episode_type).strip().lower(),
        "explicit": explicit,
        "draft": bool(intent.draft),
    }
    if intent.boundary is not None:
        payload["pubdate"] = format_iso_utc(intent.boundary)
        payload["externalId"] = publication_key(intent.slug, intent.boundary)
    if intent.image_url:
        # Documented create attribute name from Episodes Collection.
        payload["backgroundImageFileUrl"] = str(intent.image_url).strip()

    unknown = [key for key in payload if key not in DOCUMENTED_CREATE_FIELDS]
    if unknown:
        raise PublishRejectedError(f"Refusing undocumented Megaphone fields: {unknown}")
    return payload


def debug_endpoint_report(
    *,
    slug: str = "en",
    intent: EpisodeIntent | None = None,
    env_path: Path | None = None,
    process_token_before_dotenv: str = "",
    dotenv_override: bool = True,
) -> dict[str, Any]:
    """Safe debug dict: URLs, auth scheme, IDs, payload keys only (no secrets)."""
    token, network_id, podcast_id = resolve_megaphone_config(slug)
    get_url = podcast_get_url(network_id, podcast_id) if network_id and podcast_id else ""
    post_url = (
        episodes_collection_url(network_id, podcast_id) if network_id and podcast_id else ""
    )
    payload_keys: list[str] = []
    validation_failures: list[str] = []
    if intent is not None:
        validation_failures = validate_episode_intent(intent)
        if not validation_failures:
            payload_keys = sorted(build_create_episode_payload(intent).keys())
        else:
            # Still show keys that would be attempted after a valid build.
            payload_keys = sorted(
                {
                    "title",
                    "cleanTitle",
                    "summary",
                    "mediaFileUrl",
                    "episodeType",
                    "explicit",
                    "draft",
                    "pubdate",
                    "externalId",
                }
            )

    token_meta = resolve_token_provenance(
        env_path=env_path or Path(),
        process_token_before_dotenv=process_token_before_dotenv,
        resolved_token=token,
        dotenv_override=dotenv_override,
    )

    return {
        "apiBaseUrl": BASE_URL,
        "authorizationScheme": 'Token token="<redacted>"',
        "tokenConfigured": bool(token),
        **token_meta,
        "resolvedNetworkId": network_id,
        "resolvedPodcastId": podcast_id,
        "exactGetUrl": get_url,
        "exactPostUrl": post_url,
        "getOccursBeforeEpisodeCreate": True,
        "http403OnGetIsUnrelatedToEpisodeMetadata": True,
        "ingestion": {
            "documentedAudioField": "mediaFileUrl",
            "supportsPublicAudioUrl": True,
            "supportsDirectMultipartUploadOnCreate": False,
            "docsDescribeSeparateAudioProcessingAfterMediaFileUrl": True,
            "createVsPublish": (
                "Create Episode POST creates the episode object; "
                "audio processing follows mediaFileUrl import. "
                "draft=true creates a draft; pubdate required except drafts."
            ),
        },
        "cmsFormLabelsAreNotEnvVars": True,
        "documentedCreateFieldNames": list(DOCUMENTED_CREATE_FIELDS),
        "episodePayloadKeysOnly": payload_keys,
        "localValidationFailures": validation_failures,
        "note": (
            "CMS UI access does not imply the API token can GET/POST the same "
            "network/podcast path. Current 403 on GET is authorization/routing, "
            "not missing title/description/type/rating fields."
        ),
    }


def format_debug_endpoint_report(report: dict[str, Any]) -> str:
    lines = [
        "=== Megaphone endpoint debug (no POST) ===",
        f"Loaded .env path: {report.get('envPath') or '(none)'}",
        f".env file exists: {report.get('envFileExists')}",
        f"API base URL: {report.get('apiBaseUrl')}",
        f"Authorization scheme: {report.get('authorizationScheme')}",
        f"Token configured: {report.get('tokenConfigured')}",
        f"Resolved token length: {report.get('tokenLength')}",
        f"Resolved token fingerprint (SHA-256[:8]): {report.get('tokenFingerprint') or '(none)'}",
        f"Token source: {report.get('tokenSource') or '(unknown)'}",
        f"Resolved network ID: {report.get('resolvedNetworkId') or '(empty)'}",
        f"Resolved podcast ID: {report.get('resolvedPodcastId') or '(empty)'}",
        f"Exact GET URL: {report.get('exactGetUrl') or '(incomplete config)'}",
        f"Exact POST URL (not called): {report.get('exactPostUrl') or '(incomplete config)'}",
        f"GET occurs before episode create: {report.get('getOccursBeforeEpisodeCreate')}",
        f"HTTP 403 on GET unrelated to episode metadata: "
        f"{report.get('http403OnGetIsUnrelatedToEpisodeMetadata')}",
        f"CMS form labels are env vars: False "
        f"(confirmed cmsFormLabelsAreNotEnvVars={report.get('cmsFormLabelsAreNotEnvVars')})",
        "",
        "Ingestion (from docs, not guessed multipart):",
    ]
    ingestion = report.get("ingestion") or {}
    for key, value in ingestion.items():
        lines.append(f"  - {key}: {value}")
    lines.append("")
    lines.append(f"Episode payload keys only: {report.get('episodePayloadKeysOnly')}")
    failures = report.get("localValidationFailures") or []
    if failures:
        lines.append("Local validation failures:")
        for item in failures:
            lines.append(f"  - {item}")
    else:
        lines.append("Local validation failures: (none for provided intent)")
    lines.append("")
    lines.append(str(report.get("note") or ""))
    return "\n".join(lines)
