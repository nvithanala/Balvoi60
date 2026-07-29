"""Megaphone remote reconciliation by exact ``externalId`` (T-M2-004).

Paginates the podcast episodes collection using Megaphone Apiary patterns already
targeted by this repository (``per_page``, optional ``page``, RFC-5988 ``Link``,
``X-Page`` / ``X-Per-Page`` / ``X-Total``). Recovers a local T-M2-001 publication
result when Megaphone already created the episode.

Does not implement resume state machines, claim redesign, or AWS services.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from balvoi.dates import format_iso_utc
from pipeline.errors import PublishRejectedError
from pipeline.lib.logging_utils import log_event
from pipeline.lib.publication_identity import PublicationIdentity

# Matches existing ``publish_episode`` list call.
DEFAULT_PER_PAGE = 100
# Hard stop against runaway pagination (Apiary max per_page is 500).
MAX_PAGES = 500

_LINK_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*rel="?next"?', re.IGNORECASE)


class MegaphoneReconciliationError(PublishRejectedError):
    """Remote reconciliation failed; create POST must not proceed."""


@dataclass(frozen=True)
class ReconciliationOutcome:
    """Result of scanning Megaphone for an exact ``externalId`` match."""

    matched: bool
    episode: dict[str, Any] | None = None
    pages_fetched: int = 0


def episode_rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    """Normalize list envelopes used by existing Megaphone client/discover code."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("episodes", "data", "items", "results"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        # Ambiguous object without a known collection key — not a conclusive page.
        raise MegaphoneReconciliationError(
            "Megaphone episode list response is malformed: expected a list or "
            "object with episodes/data"
        )
    raise MegaphoneReconciliationError(
        f"Megaphone episode list response has unsupported type {type(payload).__name__}"
    )


def parse_link_next(link_header: str | None) -> str | None:
    """Return the RFC-5988 ``rel=next`` URL from a Megaphone ``Link`` header."""
    if not link_header or not str(link_header).strip():
        return None
    match = _LINK_NEXT_RE.search(str(link_header))
    if not match:
        return None
    return match.group(1).strip() or None


def _page_from_url(url: str) -> int | None:
    try:
        values = parse_qs(urlparse(url).query).get("page") or []
        if not values:
            return None
        return int(values[0])
    except (TypeError, ValueError):
        return None


def _header_int(headers: Any, name: str) -> int | None:
    raw = None
    try:
        raw = headers.get(name) if headers is not None else None
    except Exception:
        return None
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


def exact_external_id_matches(
    rows: list[dict[str, Any]], external_id: str
) -> list[dict[str, Any]]:
    """Return rows whose ``externalId`` exactly equals ``external_id``."""
    target = str(external_id or "")
    return [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("externalId") or "") == target
    ]


def validate_remote_episode(
    episode: dict[str, Any],
    *,
    identity: PublicationIdentity,
    external_id: str,
) -> dict[str, Any]:
    """Ensure a remote match can populate the T-M2-001 result schema."""
    if not isinstance(episode, dict):
        raise MegaphoneReconciliationError("Remote Megaphone episode is not an object")
    episode_id = str(episode.get("id") or "").strip()
    if not episode_id:
        raise MegaphoneReconciliationError(
            "Remote Megaphone episode is missing id; refusing to reconcile"
        )
    remote_external = str(episode.get("externalId") or "")
    if remote_external != str(external_id):
        raise MegaphoneReconciliationError(
            f"Remote externalId mismatch: got {remote_external!r} "
            f"expected {external_id!r}"
        )
    if identity.external_id != str(external_id):
        raise MegaphoneReconciliationError(
            "PublicationIdentity.external_id does not match reconciliation target"
        )
    if identity.publication_key != identity.external_id:
        raise MegaphoneReconciliationError(
            "publicationKey/externalId identity invariant broken"
        )
    return episode


def _next_page_request(
    *,
    endpoint: str,
    per_page: int,
    current_page: int,
    rows: list[dict[str, Any]],
    headers: Any,
    request_url: str,
) -> tuple[str, dict[str, Any] | None] | None:
    """Decide the next GET, or ``None`` when the collection is exhausted.

    Prefer Apiary ``Link: rel=next``. Fall back to ``X-Page`` / ``X-Total`` /
    ``X-Per-Page``, then to a full-page heuristic matching ``per_page``.
    """
    link_next = parse_link_next(
        headers.get("Link") if headers is not None else None
    )
    if link_next:
        return link_next, None

    x_page = _header_int(headers, "X-Page")
    x_per = _header_int(headers, "X-Per-Page")
    x_total = _header_int(headers, "X-Total")
    if x_page is not None and x_total is not None:
        size = x_per if x_per is not None and x_per > 0 else per_page
        if x_page * size >= x_total or len(rows) == 0:
            return None
        next_page = x_page + 1
        return endpoint, {"per_page": per_page, "page": next_page}

    # No conclusive pagination metadata: a short/empty page ends the scan.
    if len(rows) < per_page:
        return None
    # Full page without metadata — advance page (documented query param).
    next_page = current_page + 1
    if next_page == current_page:
        raise MegaphoneReconciliationError(
            "Megaphone pagination stalled: page did not advance"
        )
    return endpoint, {"per_page": per_page, "page": next_page}


def find_remote_episodes_by_external_id(
    *,
    endpoint: str,
    headers: dict[str, str],
    external_id: str,
    identity: PublicationIdentity,
    per_page: int = DEFAULT_PER_PAGE,
    timeout: float = 45.0,
    http_get=None,
) -> ReconciliationOutcome:
    """Paginate until exact matches are fully known or the collection ends."""
    get = http_get or requests.get
    target = str(external_id or "").strip()
    if not target:
        raise MegaphoneReconciliationError("externalId is required for reconciliation")

    log_event(
        "Megaphone Reconciliation Started",
        stage="upload",
        publicationKey=identity.publication_key,
        runId=identity.run_id,
        slug=identity.edition_slug,
        boundaryKey=identity.boundary_key,
        publicationBoundary=format_iso_utc(identity.boundary),
        externalId=target,
        perPage=per_page,
    )

    matches: list[dict[str, Any]] = []
    pages_fetched = 0
    seen_keys: set[str] = set()
    url = endpoint
    params: dict[str, Any] | None = {"per_page": per_page, "page": 1}
    current_page = 1

    try:
        while True:
            if pages_fetched >= MAX_PAGES:
                raise MegaphoneReconciliationError(
                    f"Megaphone pagination exceeded safety limit ({MAX_PAGES} pages)"
                )
            request_key = f"{url}|{sorted((params or {}).items())}"
            if request_key in seen_keys:
                log_event(
                    "Megaphone Reconciliation Failed",
                    stage="upload",
                    publicationKey=identity.publication_key,
                    runId=identity.run_id,
                    slug=identity.edition_slug,
                    externalId=target,
                    reason="repeated_page",
                    page=current_page,
                )
                raise MegaphoneReconciliationError(
                    "Megaphone pagination repeated the same page/cursor; refusing to continue"
                )
            seen_keys.add(request_key)

            try:
                response = get(url, headers=headers, params=params, timeout=timeout)
                response.raise_for_status()
                payload = response.json()
            except MegaphoneReconciliationError:
                raise
            except (requests.RequestException, ValueError) as err:
                status = getattr(getattr(err, "response", None), "status_code", None)
                detail = type(err).__name__
                if status is not None:
                    detail = f"HTTP_{status}"
                log_event(
                    "Megaphone Reconciliation Failed",
                    stage="upload",
                    publicationKey=identity.publication_key,
                    runId=identity.run_id,
                    slug=identity.edition_slug,
                    externalId=target,
                    errorType=type(err).__name__,
                    errorMessage=str(err),
                    httpStatus=status,
                )
                raise MegaphoneReconciliationError(
                    f"Megaphone reconciliation list failed: {detail}"
                ) from err

            try:
                rows = episode_rows_from_payload(payload)
            except MegaphoneReconciliationError as err:
                log_event(
                    "Megaphone Reconciliation Failed",
                    stage="upload",
                    publicationKey=identity.publication_key,
                    runId=identity.run_id,
                    slug=identity.edition_slug,
                    externalId=target,
                    reason="malformed_page",
                    errorMessage=str(err),
                    page=current_page,
                )
                raise

            pages_fetched += 1
            x_page = _header_int(response.headers, "X-Page")
            page_for_log = x_page if x_page is not None else current_page
            log_event(
                "Megaphone Reconciliation Page Fetched",
                stage="upload",
                publicationKey=identity.publication_key,
                runId=identity.run_id,
                slug=identity.edition_slug,
                externalId=target,
                page=page_for_log,
                rowCount=len(rows),
                perPage=per_page,
            )

            page_matches = exact_external_id_matches(rows, target)
            matches.extend(page_matches)
            if len(matches) > 1:
                ids = [str(m.get("id") or "") for m in matches]
                log_event(
                    "Megaphone Reconciliation Duplicate Match",
                    stage="upload",
                    publicationKey=identity.publication_key,
                    runId=identity.run_id,
                    slug=identity.edition_slug,
                    externalId=target,
                    megaphoneEpisodeIds=ids,
                    matchCount=len(matches),
                )
                raise MegaphoneReconciliationError(
                    f"Multiple Megaphone episodes share externalId {target!r}: {ids}"
                )

            nxt = _next_page_request(
                endpoint=endpoint,
                per_page=per_page,
                current_page=current_page,
                rows=rows,
                headers=response.headers,
                request_url=str(getattr(response, "url", url) or url),
            )
            if nxt is None:
                break
            url, params = nxt
            if params and "page" in params:
                current_page = int(params["page"])
            else:
                parsed_page = _page_from_url(url)
                current_page = parsed_page if parsed_page is not None else current_page + 1

    except MegaphoneReconciliationError:
        raise
    except Exception as err:  # noqa: BLE001 — convert unexpected failures
        log_event(
            "Megaphone Reconciliation Failed",
            stage="upload",
            publicationKey=identity.publication_key,
            runId=identity.run_id,
            slug=identity.edition_slug,
            externalId=target,
            errorType=type(err).__name__,
            errorMessage=str(err),
        )
        raise MegaphoneReconciliationError(
            f"Megaphone reconciliation failed: {type(err).__name__}: {err}"
        ) from err

    if not matches:
        log_event(
            "Megaphone Reconciliation No Match",
            stage="upload",
            publicationKey=identity.publication_key,
            runId=identity.run_id,
            slug=identity.edition_slug,
            externalId=target,
            pagesFetched=pages_fetched,
        )
        return ReconciliationOutcome(
            matched=False, episode=None, pages_fetched=pages_fetched
        )

    episode = validate_remote_episode(
        matches[0], identity=identity, external_id=target
    )
    log_event(
        "Megaphone Reconciliation Match Found",
        stage="upload",
        publicationKey=identity.publication_key,
        runId=identity.run_id,
        slug=identity.edition_slug,
        externalId=target,
        megaphoneEpisodeId=str(episode.get("id")),
        pagesFetched=pages_fetched,
    )
    return ReconciliationOutcome(
        matched=True, episode=episode, pages_fetched=pages_fetched
    )


def persist_reconciled_result(
    identity: PublicationIdentity,
    *,
    episode: dict[str, Any],
    media_file_url: str,
) -> dict[str, Any]:
    """Atomically write a T-M2-001 artifact marked as reconciled."""
    from pipeline.lib.megaphone_publication_result import (
        result_as_upload,
        save_publication_result,
    )

    episode_id = str(episode.get("id") or "").strip()
    try:
        record = save_publication_result(
            identity,
            megaphone_episode_id=episode_id,
            media_file_url=media_file_url,
            megaphone_response=episode,
            source="reconciled",
        )
    except PublishRejectedError:
        log_event(
            "Megaphone Reconciliation Failed",
            stage="upload",
            publicationKey=identity.publication_key,
            runId=identity.run_id,
            slug=identity.edition_slug,
            externalId=identity.external_id,
            megaphoneEpisodeId=episode_id or None,
            reason="persist_failed",
        )
        raise
    log_event(
        "Megaphone Reconciliation Result Persisted",
        stage="upload",
        publicationKey=identity.publication_key,
        runId=identity.run_id,
        slug=identity.edition_slug,
        externalId=identity.external_id,
        megaphoneEpisodeId=episode_id,
        source="reconciled",
    )
    upload = result_as_upload(record)
    upload["reconciled"] = True
    return upload
