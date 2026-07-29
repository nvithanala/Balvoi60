"""Canonical publication claims (T-M2-002).

Ownership records keyed by ``PublicationIdentity.publication_key``. The first
process to create a claim owns that publication; later processes must not publish.

Does not implement DynamoDB, recovery workflows, or Megaphone API changes.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from balvoi.dates import format_iso_utc
from pipeline.lib.logging_utils import log_event
from pipeline.lib.publication_identity import PublicationIdentity
from pipeline.lib.storage_paths import get_storage_paths

SCHEMA_VERSION = 1

ClaimStatus = Literal["acquired", "completed", "failed"]
STATUS_ACQUIRED: ClaimStatus = "acquired"
STATUS_COMPLETED: ClaimStatus = "completed"
STATUS_FAILED: ClaimStatus = "failed"
_VALID_STATUSES = frozenset({STATUS_ACQUIRED, STATUS_COMPLETED, STATUS_FAILED})


class PublicationClaimError(ValueError):
    """Claim payload is missing or invalid."""


@dataclass(frozen=True)
class ClaimAcquireResult:
    """Outcome of attempting to own a publication."""

    acquired: bool
    already_owned: bool
    claim: dict[str, Any] | None = None
    reason: str | None = None


def claim_path(publication_key: str) -> Path:
    return get_storage_paths().publication_claim_path(publication_key)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_replace_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def build_claim(
    identity: PublicationIdentity,
    *,
    status: ClaimStatus = STATUS_ACQUIRED,
    created_at: str | None = None,
    updated_at: str | None = None,
    owner_process_id: int | None = None,
    owner_thread: str | None = None,
) -> dict[str, Any]:
    now = created_at or _now_iso()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "publicationKey": identity.publication_key,
        "runId": identity.run_id,
        "boundaryKey": identity.boundary_key,
        "publicationBoundary": format_iso_utc(identity.boundary),
        "slug": identity.edition_slug,
        "ownerProcessId": owner_process_id if owner_process_id is not None else os.getpid(),
        "ownerThread": owner_thread or threading.current_thread().name,
        "status": status,
        "createdAt": now,
        "updatedAt": updated_at or now,
    }


def validate_claim(
    record: dict[str, Any] | None,
    *,
    identity: PublicationIdentity | None = None,
) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise PublicationClaimError("claim must be an object")
    if record.get("schemaVersion") != SCHEMA_VERSION:
        raise PublicationClaimError(
            f"unsupported schemaVersion {record.get('schemaVersion')!r}"
        )
    pub_key = str(record.get("publicationKey") or "").strip()
    run_id = str(record.get("runId") or "").strip()
    slug = str(record.get("slug") or "").strip().lower()
    status = str(record.get("status") or "").strip().lower()
    if not pub_key:
        raise PublicationClaimError("publicationKey missing")
    if not run_id:
        raise PublicationClaimError("runId missing")
    if not slug:
        raise PublicationClaimError("slug missing")
    if status not in _VALID_STATUSES:
        raise PublicationClaimError(f"unsupported status {status!r}")
    if identity is not None:
        if pub_key != identity.publication_key:
            raise PublicationClaimError(
                f"publicationKey mismatch: stored={pub_key!r} "
                f"expected={identity.publication_key!r}"
            )
        if slug != identity.edition_slug:
            raise PublicationClaimError(
                f"slug mismatch: stored={slug!r} expected={identity.edition_slug!r}"
            )
    return record


def read_claim(publication_key: str) -> dict[str, Any] | None:
    path = claim_path(publication_key)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def claim_exists(publication_key: str) -> bool:
    return claim_path(publication_key).is_file()


def create_claim(identity: PublicationIdentity) -> ClaimAcquireResult:
    """Atomically acquire ownership for ``identity.publication_key``.

    Same ``runId`` may re-enter idempotently. A different owner is rejected.
    """
    path = claim_path(identity.publication_key)
    log_event(
        "Claim Lookup Started",
        stage="claim",
        publicationKey=identity.publication_key,
        runId=identity.run_id,
        slug=identity.edition_slug,
        path=str(path),
    )

    existing_raw = read_claim(identity.publication_key)
    if existing_raw is None and path.is_file():
        log_event(
            "Invalid Claim",
            stage="claim",
            publicationKey=identity.publication_key,
            runId=identity.run_id,
            slug=identity.edition_slug,
            errorMessage="unreadable or corrupt claim file",
        )
        return ClaimAcquireResult(
            acquired=False,
            already_owned=True,
            claim=None,
            reason="invalid_claim",
        )
    if existing_raw is not None:
        try:
            existing = validate_claim(existing_raw, identity=identity)
        except PublicationClaimError as err:
            log_event(
                "Invalid Claim",
                stage="claim",
                publicationKey=identity.publication_key,
                runId=identity.run_id,
                slug=identity.edition_slug,
                errorMessage=str(err),
            )
            # Corrupt/mismatched claim must not be treated as ownership by us.
            return ClaimAcquireResult(
                acquired=False,
                already_owned=True,
                claim=existing_raw if isinstance(existing_raw, dict) else None,
                reason="invalid_claim",
            )
        log_event(
            "Claim Found",
            stage="claim",
            publicationKey=identity.publication_key,
            runId=identity.run_id,
            slug=identity.edition_slug,
            ownerRunId=existing.get("runId"),
            status=existing.get("status"),
        )
        existing_status = str(existing.get("status") or "")
        same_owner = str(existing.get("runId") or "") == identity.run_id
        # Same run may re-enter only while still acquired (in-flight retry).
        # Terminal statuses block all further publication attempts (no recovery).
        if same_owner and existing_status == STATUS_ACQUIRED:
            return ClaimAcquireResult(
                acquired=True, already_owned=False, claim=existing, reason="idempotent"
            )
        log_event(
            "Claim Already Owned",
            stage="claim",
            publicationKey=identity.publication_key,
            runId=identity.run_id,
            slug=identity.edition_slug,
            ownerRunId=existing.get("runId"),
            status=existing_status,
        )
        return ClaimAcquireResult(
            acquired=False,
            already_owned=True,
            claim=existing,
            reason="already_owned",
        )

    log_event(
        "Claim Not Found",
        stage="claim",
        publicationKey=identity.publication_key,
        runId=identity.run_id,
        slug=identity.edition_slug,
    )

    payload = build_claim(identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
    except FileExistsError:
        # Lost the race — re-read and classify.
        raced = read_claim(identity.publication_key)
        try:
            existing = validate_claim(raced, identity=identity) if raced else None
        except PublicationClaimError:
            existing = None
        if existing and str(existing.get("runId") or "") == identity.run_id:
            if str(existing.get("status") or "") == STATUS_ACQUIRED:
                return ClaimAcquireResult(
                    acquired=True, already_owned=False, claim=existing, reason="idempotent"
                )
        log_event(
            "Claim Creation Failed",
            stage="claim",
            publicationKey=identity.publication_key,
            runId=identity.run_id,
            slug=identity.edition_slug,
            reason="race_lost",
        )
        log_event(
            "Claim Already Owned",
            stage="claim",
            publicationKey=identity.publication_key,
            runId=identity.run_id,
            slug=identity.edition_slug,
            ownerRunId=(existing or {}).get("runId") if existing else None,
        )
        return ClaimAcquireResult(
            acquired=False,
            already_owned=True,
            claim=existing,
            reason="already_owned",
        )
    except OSError as err:
        log_event(
            "Claim Creation Failed",
            stage="claim",
            publicationKey=identity.publication_key,
            runId=identity.run_id,
            slug=identity.edition_slug,
            errorType=type(err).__name__,
            errorMessage=str(err),
        )
        return ClaimAcquireResult(
            acquired=False, already_owned=False, claim=None, reason="create_failed"
        )

    log_event(
        "Claim Created",
        stage="claim",
        publicationKey=identity.publication_key,
        runId=identity.run_id,
        slug=identity.edition_slug,
        status=STATUS_ACQUIRED,
    )
    return ClaimAcquireResult(
        acquired=True, already_owned=False, claim=payload, reason="created"
    )


def complete_claim(identity: PublicationIdentity) -> dict[str, Any] | None:
    """Mark an owned claim as successfully completed."""
    return _update_claim_status(identity, STATUS_COMPLETED, event="Claim Completed")


def fail_claim(identity: PublicationIdentity, *, error: str | None = None) -> dict[str, Any] | None:
    """Record failure after a claim was acquired (no recovery)."""
    return _update_claim_status(
        identity,
        STATUS_FAILED,
        event="Claim Failed",
        extra={"errorMessage": error} if error else None,
    )


def _update_claim_status(
    identity: PublicationIdentity,
    status: ClaimStatus,
    *,
    event: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    path = claim_path(identity.publication_key)
    raw = read_claim(identity.publication_key)
    if raw is None:
        log_event(
            "Claim Not Found",
            stage="claim",
            publicationKey=identity.publication_key,
            runId=identity.run_id,
            slug=identity.edition_slug,
        )
        return None
    try:
        claim = validate_claim(raw, identity=identity)
    except PublicationClaimError as err:
        log_event(
            "Invalid Claim",
            stage="claim",
            publicationKey=identity.publication_key,
            runId=identity.run_id,
            slug=identity.edition_slug,
            errorMessage=str(err),
        )
        return None
    if str(claim.get("runId") or "") != identity.run_id:
        log_event(
            "Claim Already Owned",
            stage="claim",
            publicationKey=identity.publication_key,
            runId=identity.run_id,
            slug=identity.edition_slug,
            ownerRunId=claim.get("runId"),
        )
        return None
    updated = dict(claim)
    updated["status"] = status
    updated["updatedAt"] = _now_iso()
    if extra:
        updated.update(extra)
    _atomic_replace_json(path, updated)
    log_event(
        event,
        stage="claim",
        publicationKey=identity.publication_key,
        runId=identity.run_id,
        slug=identity.edition_slug,
        status=status,
    )
    return updated


def release_claim(identity: PublicationIdentity) -> bool:
    """Delete a claim owned by this run.

    Not used on the happy path (completed claims remain for duplicate detection).
    Available for operator/tests only.
    """
    path = claim_path(identity.publication_key)
    raw = read_claim(identity.publication_key)
    if raw is None:
        return False
    try:
        claim = validate_claim(raw, identity=identity)
    except PublicationClaimError:
        return False
    if str(claim.get("runId") or "") != identity.run_id:
        return False
    path.unlink(missing_ok=True)
    return True
