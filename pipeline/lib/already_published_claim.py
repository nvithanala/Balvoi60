"""Recover claims when local publish reports already_published with Megaphone proof.

Tier 1.2: prevents successful Megaphone creates from becoming terminal FAILED claims
when ``publish_run`` raises already_published after a crash/retry.

Does not implement claim leases, failed-claim reopening, or Megaphone API calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pipeline.errors import PublishRejectedError
from pipeline.lib.logging_utils import log_event
from pipeline.lib.megaphone_publication_result import (
    MegaphonePublicationResultError,
    load_publication_result,
    validate_publication_result,
)
from pipeline.lib.publication_identity import PublicationIdentity

REASON_ALREADY_PUBLISHED = "already_published"


@dataclass(frozen=True)
class CanonicalPublishProof:
    """Validated canonical Megaphone publication result for claim completion."""

    result: dict[str, Any]
    episode_id: str


def is_already_published_error(err: BaseException) -> bool:
    """True only for structured ``PublishRejectedError(reason=already_published)``."""
    return (
        isinstance(err, PublishRejectedError)
        and getattr(err, "reason", None) == REASON_ALREADY_PUBLISHED
    )


def load_canonical_publish_proof(
    identity: PublicationIdentity,
) -> CanonicalPublishProof | None:
    """Return validated Megaphone proof for ``identity``, or ``None`` if missing/invalid.

    Uses only ``manifests/megaphone_publications/`` via ``load_publication_result``.
    """
    record = load_publication_result(identity)
    if record is None:
        return None
    try:
        validated = validate_publication_result(record, identity=identity)
    except MegaphonePublicationResultError:
        return None
    episode_id = str(validated.get("megaphoneEpisodeId") or "").strip()
    if not episode_id:
        return None
    return CanonicalPublishProof(result=validated, episode_id=episode_id)


def try_recover_already_published_claim(
    identity: PublicationIdentity,
    err: BaseException,
) -> CanonicalPublishProof | None:
    """If ``err`` is structured already_published and canonical proof exists, return it.

    Callers must own the acquired claim and call ``complete_claim`` themselves.
    Does not mutate claim state.
    """
    if not is_already_published_error(err):
        return None

    log_event(
        "Already Published Detected",
        stage="publish",
        publicationKey=identity.publication_key,
        runId=identity.run_id,
        slug=identity.edition_slug,
    )

    proof = load_canonical_publish_proof(identity)
    if proof is None:
        log_event(
            "Already Published Canonical Result Missing",
            stage="publish",
            publicationKey=identity.publication_key,
            runId=identity.run_id,
            slug=identity.edition_slug,
            failClosed=True,
        )
        return None

    log_event(
        "Already Published Canonical Result Validated",
        stage="publish",
        publicationKey=identity.publication_key,
        runId=identity.run_id,
        slug=identity.edition_slug,
        megaphoneEpisodeId=proof.episode_id,
    )
    return proof
