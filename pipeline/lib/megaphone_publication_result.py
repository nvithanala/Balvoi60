"""Canonical Megaphone publication-result persistence (T-M2-001).

Stores the durable record of a successful Megaphone create so retries can skip
a second POST for the same ``publicationKey``.

Does not implement publication claims, remote reconciliation, or DynamoDB.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from balvoi.dates import format_iso_utc
from pipeline.errors import PublishRejectedError
from pipeline.lib.logging_utils import log_event
from pipeline.lib.publication_identity import PublicationIdentity
from pipeline.lib.storage_paths import get_storage_paths

SCHEMA_VERSION = 1
STATUS_CREATED = "created"
SOURCE_CREATED = "created"
SOURCE_RECONCILED = "reconciled"
_VALID_SOURCES = frozenset({SOURCE_CREATED, SOURCE_RECONCILED})


class MegaphonePublicationResultError(ValueError):
    """Persisted Megaphone publication result is missing or invalid."""


def publication_result_path(publication_key: str) -> Path:
    return get_storage_paths().megaphone_publication_result_path(publication_key)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def build_publication_result(
    *,
    identity: PublicationIdentity,
    megaphone_episode_id: str,
    media_file_url: str,
    megaphone_response: dict[str, Any] | None = None,
    status: str = STATUS_CREATED,
    source: str = SOURCE_CREATED,
    created_at: str | None = None,
    published_at: datetime | str | None = None,
    publication_delay_seconds: float | None = None,
) -> dict[str, Any]:
    from balvoi.dates import publication_delay_seconds as delay_fn

    if isinstance(published_at, datetime):
        success_at = published_at.astimezone(UTC)
        published_iso = success_at.isoformat()
    elif published_at:
        published_iso = str(published_at)
        success_at = datetime.fromisoformat(published_iso.replace("Z", "+00:00")).astimezone(
            UTC
        )
    else:
        success_at = datetime.now(UTC)
        published_iso = success_at.isoformat()
    now = created_at or published_iso
    episode_id = str(megaphone_episode_id or "").strip()
    if not episode_id:
        raise MegaphonePublicationResultError("megaphoneEpisodeId is required")
    source_norm = str(source or SOURCE_CREATED).strip().lower() or SOURCE_CREATED
    if source_norm not in _VALID_SOURCES:
        raise MegaphonePublicationResultError(f"unsupported source {source!r}")
    delay = (
        float(publication_delay_seconds)
        if publication_delay_seconds is not None
        else delay_fn(identity.boundary, success_at=success_at)
    )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "publicationKey": identity.publication_key,
        "externalId": identity.external_id,
        "runId": identity.run_id,
        "boundaryKey": identity.boundary_key,
        "publicationBoundary": format_iso_utc(identity.boundary),
        "slug": identity.edition_slug,
        "megaphoneEpisodeId": episode_id,
        "status": status,
        "source": source_norm,
        "createdAt": now,
        "publishedAt": published_iso,
        "publicationDelaySeconds": round(delay, 3),
        "mediaFileUrl": str(media_file_url or "").strip(),
        "megaphoneResponse": {
            "id": episode_id,
            "externalId": identity.external_id,
            **(
                {
                    k: v
                    for k, v in (megaphone_response or {}).items()
                    if k not in {"id", "externalId"}
                }
            ),
        },
    }


def validate_publication_result(
    record: dict[str, Any] | None,
    *,
    identity: PublicationIdentity,
) -> dict[str, Any]:
    """Return a confirmed record or raise ``MegaphonePublicationResultError``."""
    if not isinstance(record, dict):
        raise MegaphonePublicationResultError("publication result must be an object")
    version = record.get("schemaVersion")
    if version != SCHEMA_VERSION:
        raise MegaphonePublicationResultError(
            f"unsupported schemaVersion {version!r} (expected {SCHEMA_VERSION})"
        )
    pub_key = str(record.get("publicationKey") or "").strip()
    external_id = str(record.get("externalId") or "").strip()
    slug = str(record.get("slug") or "").strip().lower()
    episode_id = str(record.get("megaphoneEpisodeId") or "").strip()
    status = str(record.get("status") or "").strip().lower()
    if pub_key != identity.publication_key:
        raise MegaphonePublicationResultError(
            f"publicationKey mismatch: stored={pub_key!r} expected={identity.publication_key!r}"
        )
    if external_id and external_id != identity.external_id:
        raise MegaphonePublicationResultError(
            f"externalId mismatch: stored={external_id!r} expected={identity.external_id!r}"
        )
    if slug != identity.edition_slug:
        raise MegaphonePublicationResultError(
            f"slug mismatch: stored={slug!r} expected={identity.edition_slug!r}"
        )
    if not episode_id:
        raise MegaphonePublicationResultError("megaphoneEpisodeId missing")
    if status not in {STATUS_CREATED, "published"}:
        raise MegaphonePublicationResultError(f"unsupported status {status!r}")
    # Optional T-M2-004 field; absent on T-M2-001 artifacts.
    if "source" in record and record.get("source") is not None:
        source = str(record.get("source") or "").strip().lower()
        if source not in _VALID_SOURCES:
            raise MegaphonePublicationResultError(f"unsupported source {source!r}")
    return record


def load_publication_result(
    identity: PublicationIdentity,
) -> dict[str, Any] | None:
    """Load and validate a persisted result for ``identity``.

    Returns ``None`` when missing. Invalid/corrupt records log and return ``None``
    (caller may create again; remote externalId scan still applies).
    """
    path = publication_result_path(identity.publication_key)
    log_event(
        "Megaphone Result Lookup Started",
        stage="upload",
        publicationKey=identity.publication_key,
        runId=identity.run_id,
        slug=identity.edition_slug,
        path=str(path),
    )
    if not path.is_file():
        log_event(
            "Megaphone Result Not Found",
            stage="upload",
            publicationKey=identity.publication_key,
            runId=identity.run_id,
            slug=identity.edition_slug,
        )
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        record = validate_publication_result(raw, identity=identity)
    except (OSError, json.JSONDecodeError, MegaphonePublicationResultError) as err:
        log_event(
            "Megaphone Result Invalid",
            stage="upload",
            publicationKey=identity.publication_key,
            runId=identity.run_id,
            slug=identity.edition_slug,
            errorType=type(err).__name__,
            errorMessage=str(err),
        )
        return None
    log_event(
        "Megaphone Result Found",
        stage="upload",
        publicationKey=identity.publication_key,
        runId=identity.run_id,
        slug=identity.edition_slug,
        megaphoneEpisodeId=record.get("megaphoneEpisodeId"),
    )
    return record


def save_publication_result(
    identity: PublicationIdentity,
    *,
    megaphone_episode_id: str,
    media_file_url: str,
    megaphone_response: dict[str, Any] | None = None,
    source: str = SOURCE_CREATED,
    published_at: datetime | str | None = None,
    publication_delay_seconds: float | None = None,
) -> dict[str, Any]:
    """Atomically persist a successful Megaphone create or reconcile result."""
    payload = build_publication_result(
        identity=identity,
        megaphone_episode_id=megaphone_episode_id,
        media_file_url=media_file_url,
        megaphone_response=megaphone_response,
        source=source,
        published_at=published_at,
        publication_delay_seconds=publication_delay_seconds,
    )
    path = publication_result_path(identity.publication_key)
    try:
        _atomic_json(path, payload)
    except OSError as err:
        log_event(
            "Megaphone Result Persistence Failed",
            stage="upload",
            publicationKey=identity.publication_key,
            runId=identity.run_id,
            slug=identity.edition_slug,
            errorType=type(err).__name__,
            errorMessage=str(err),
        )
        raise PublishRejectedError(
            f"Megaphone publication result persistence failed: {type(err).__name__}"
        ) from err
    log_event(
        "Megaphone Result Persisted",
        stage="upload",
        publicationKey=identity.publication_key,
        runId=identity.run_id,
        slug=identity.edition_slug,
        megaphoneEpisodeId=payload["megaphoneEpisodeId"],
        source=payload.get("source"),
        publicationDelaySeconds=payload.get("publicationDelaySeconds"),
        path=str(path),
    )
    return payload


def result_as_upload(record: dict[str, Any]) -> dict[str, Any]:
    """Map a persisted record to the ``publish_episode`` return shape."""
    out = {
        "id": record["megaphoneEpisodeId"],
        "externalId": record.get("externalId") or record.get("publicationKey"),
        "reused": True,
        "publishedAt": record.get("publishedAt"),
        "publicationDelaySeconds": record.get("publicationDelaySeconds"),
    }
    if str(record.get("source") or "").strip().lower() == SOURCE_RECONCILED:
        out["reconciled"] = True
    return out
