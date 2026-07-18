"""Megaphone episode publication with remote duplicate verification."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import requests

from balvoi.dates import format_iso_utc
from pipeline.errors import PublishRejectedError
from pipeline.lib.edition_lock import edition_was_published

BASE_URL = "https://cms.megaphone.fm/api"


def enabled() -> bool:
    return os.environ.get("MEGAPHONE_ENABLED", "").strip().lower() == "true"


def _config(slug: str) -> tuple[str, str, str]:
    suffix = slug.upper()
    token = os.environ.get(f"MEGAPHONE_API_TOKEN_{suffix}", "").strip()
    network = os.environ.get(f"MEGAPHONE_NETWORK_ID_{suffix}", "").strip()
    podcast = os.environ.get(f"MEGAPHONE_PODCAST_ID_{suffix}", "").strip()
    if not all((token, network, podcast)):
        raise PublishRejectedError(f"Missing Megaphone account configuration for language {slug}")
    return token, network, podcast


def publish_episode(
    *,
    boundary: datetime,
    slug: str,
    title: str,
    summary: str,
    audio_path: Path,
    public_audio_url: str,
) -> dict | None:
    """Create one episode unless the boundary/language exists locally or remotely."""
    if not enabled():
        return None
    token, network, podcast = _config(slug)
    endpoint = f"{BASE_URL}/networks/{network}/podcasts/{podcast}/episodes"
    headers = {"Authorization": f'Token token="{token}"', "Accept": "application/json"}
    external_id = f"balvoi60:{format_iso_utc(boundary)}:{slug}"

    if edition_was_published(boundary, slug):
        raise PublishRejectedError("Megaphone upload rejected: already_published")
    try:
        existing = requests.get(
            endpoint,
            headers=headers,
            params={"per_page": 100},
            timeout=45,
        )
        existing.raise_for_status()
        rows = existing.json()
        if isinstance(rows, dict):
            rows = rows.get("episodes") or rows.get("data") or []
        if any(row.get("externalId") == external_id for row in rows if isinstance(row, dict)):
            raise PublishRejectedError("Megaphone upload rejected: already_published")
        response = requests.post(
            endpoint,
            headers={**headers, "Content-Type": "application/json"},
            json={
                "title": title,
                "cleanTitle": title,
                "summary": summary,
                "pubdate": format_iso_utc(boundary),
                "mediaFileUrl": public_audio_url,
                "externalId": external_id,
            },
            timeout=90,
        )
        response.raise_for_status()
        payload = response.json()
    except PublishRejectedError:
        raise
    except (requests.RequestException, ValueError) as err:
        raise PublishRejectedError(f"Megaphone publication failed: {type(err).__name__}") from err
    if not isinstance(payload, dict) or not payload.get("id"):
        raise PublishRejectedError("Megaphone publication returned no episode ID")
    return {"id": payload["id"], "externalId": external_id}
