"""T-M2-002 publication claim tests."""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest

from pipeline.lib import logging_utils as lu
from pipeline.lib.publication_claim import (
    SCHEMA_VERSION,
    STATUS_ACQUIRED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    PublicationClaimError,
    build_claim,
    claim_exists,
    claim_path,
    complete_claim,
    create_claim,
    fail_claim,
    read_claim,
    release_claim,
    validate_claim,
)
from pipeline.lib.publication_identity import PublicationIdentity

BOUNDARY = datetime(2026, 7, 22, 18, 0, 0, tzinfo=UTC)
OTHER_BOUNDARY = datetime(2026, 7, 22, 19, 0, 0, tzinfo=UTC)
RUN_ID = "2026-07-22T18-00-00Z"
OTHER_RUN = "2026-07-22T18-00-00Z-other"


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.delenv("BALVOI_STRUCTURED_LOGS", raising=False)
    lu.reset_logging_for_tests()
    return tmp_path


def _identity(
    slug: str = "en", *, boundary=BOUNDARY, run_id: str = RUN_ID
) -> PublicationIdentity:
    return PublicationIdentity.from_boundary(boundary, slug, run_id=run_id)


def test_create_claim(storage: Path) -> None:
    identity = _identity()
    result = create_claim(identity)
    assert result.acquired is True
    assert result.already_owned is False
    assert result.reason == "created"
    assert claim_exists(identity.publication_key)
    raw = read_claim(identity.publication_key)
    assert raw is not None
    validated = validate_claim(raw, identity=identity)
    assert validated["schemaVersion"] == SCHEMA_VERSION
    assert validated["publicationKey"] == identity.publication_key
    assert validated["runId"] == RUN_ID
    assert validated["boundaryKey"] == identity.boundary_key
    assert validated["slug"] == "en"
    assert validated["status"] == STATUS_ACQUIRED
    assert "ownerProcessId" in validated
    assert "ownerThread" in validated
    assert "createdAt" in validated
    assert "updatedAt" in validated
    assert claim_path(identity.publication_key).is_file()
    assert "publication_claims" in str(claim_path(identity.publication_key))


def test_duplicate_claim_rejected(storage: Path) -> None:
    first = create_claim(_identity())
    assert first.acquired
    second = create_claim(_identity(run_id=OTHER_RUN))
    assert second.acquired is False
    assert second.already_owned is True
    assert second.reason == "already_owned"
    assert second.claim is not None
    assert second.claim["runId"] == RUN_ID


def test_claim_lookup(storage: Path) -> None:
    identity = _identity()
    assert read_claim(identity.publication_key) is None
    assert claim_exists(identity.publication_key) is False
    create_claim(identity)
    assert claim_exists(identity.publication_key) is True
    loaded = read_claim(identity.publication_key)
    assert loaded is not None
    assert loaded["publicationKey"] == identity.publication_key


def test_invalid_claim(storage: Path) -> None:
    with pytest.raises(PublicationClaimError, match="object"):
        validate_claim(None)  # type: ignore[arg-type]
    with pytest.raises(PublicationClaimError, match="schemaVersion"):
        validate_claim(
            {
                "schemaVersion": 99,
                "publicationKey": "x",
                "runId": "r",
                "slug": "en",
                "status": "acquired",
            }
        )
    with pytest.raises(PublicationClaimError, match="status"):
        validate_claim(
            {
                "schemaVersion": 1,
                "publicationKey": "k",
                "runId": "r",
                "slug": "en",
                "status": "pending",
            }
        )
    identity = _identity()
    create_claim(identity)
    other = _identity("es")
    raw = read_claim(identity.publication_key)
    with pytest.raises(PublicationClaimError, match="publicationKey mismatch"):
        validate_claim(raw, identity=other)


def test_corrupt_claim(storage: Path) -> None:
    identity = _identity()
    path = claim_path(identity.publication_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    assert read_claim(identity.publication_key) is None
    assert claim_exists(identity.publication_key) is True
    result = create_claim(identity)
    assert result.acquired is False
    assert result.already_owned is True
    assert result.reason == "invalid_claim"


def test_completion_update(storage: Path) -> None:
    identity = _identity()
    create_claim(identity)
    updated = complete_claim(identity)
    assert updated is not None
    assert updated["status"] == STATUS_COMPLETED
    assert read_claim(identity.publication_key)["status"] == STATUS_COMPLETED
    # Terminal claim blocks further acquisition (including same run).
    again = create_claim(identity)
    assert again.acquired is False
    assert again.already_owned is True


def test_failure_update(storage: Path) -> None:
    identity = _identity()
    create_claim(identity)
    failed = fail_claim(identity, error="boom")
    assert failed is not None
    assert failed["status"] == STATUS_FAILED
    assert failed.get("errorMessage") == "boom"
    blocked = create_claim(_identity(run_id=OTHER_RUN))
    assert blocked.already_owned is True


def test_atomic_creation(storage: Path) -> None:
    """Only one concurrent creator wins; others with different runIds are blocked."""
    barrier = threading.Barrier(8)
    results: list = []

    def worker(idx: int) -> None:
        barrier.wait()
        identity = _identity(run_id=f"{RUN_ID}-worker-{idx}")
        results.append(create_claim(identity))

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(worker, i) for i in range(8)]
        for fut in futures:
            fut.result()

    winners = [r for r in results if r.acquired and r.reason == "created"]
    owners = [r for r in results if r.acquired]
    losers = [r for r in results if r.already_owned]
    assert len(winners) == 1
    assert len(owners) == 1
    assert len(losers) == 7
    assert claim_exists(_identity().publication_key)
    owner_run = read_claim(_identity().publication_key)["runId"]
    assert owner_run.startswith(f"{RUN_ID}-worker-")
    assert sum(1 for r in results if r.claim and r.claim.get("runId") == owner_run and r.acquired) == 1


def test_different_languages_same_boundary(storage: Path) -> None:
    en = create_claim(_identity("en"))
    es = create_claim(_identity("es"))
    assert en.acquired and es.acquired
    assert en.claim["publicationKey"] != es.claim["publicationKey"]
    assert claim_exists(_identity("en").publication_key)
    assert claim_exists(_identity("es").publication_key)


def test_same_language_different_boundaries(storage: Path) -> None:
    a = create_claim(_identity("en", boundary=BOUNDARY))
    b = create_claim(
        _identity("en", boundary=OTHER_BOUNDARY, run_id="2026-07-22T19-00-00Z")
    )
    assert a.acquired and b.acquired
    assert a.claim["publicationKey"] != b.claim["publicationKey"]


def test_idempotent_same_run_while_acquired(storage: Path) -> None:
    identity = _identity()
    first = create_claim(identity)
    second = create_claim(identity)
    assert first.acquired and second.acquired
    assert second.reason == "idempotent"


def test_release_claim(storage: Path) -> None:
    identity = _identity()
    create_claim(identity)
    assert release_claim(identity) is True
    assert claim_exists(identity.publication_key) is False
    assert release_claim(identity) is False


def test_structured_logging(storage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BALVOI_STRUCTURED_LOGS", "true")
    lu.reset_logging_for_tests()
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(lu.JsonFormatter())
    root = logging.getLogger("balvoi")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    root.propagate = False
    lu._CONFIGURED = True

    identity = _identity()
    create_claim(identity)
    create_claim(_identity(run_id=OTHER_RUN))
    complete_claim(identity)

    text = stream.getvalue()
    for event in (
        "Claim Lookup Started",
        "Claim Not Found",
        "Claim Created",
        "Claim Found",
        "Claim Already Owned",
        "Claim Completed",
    ):
        assert event in text, f"missing {event} in {text!r}"


def test_invalid_claim_logged_and_blocks(
    storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BALVOI_STRUCTURED_LOGS", "true")
    lu.reset_logging_for_tests()
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(lu.JsonFormatter())
    root = logging.getLogger("balvoi")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    root.propagate = False
    lu._CONFIGURED = True

    identity = _identity()
    path = claim_path(identity.publication_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "publicationKey": identity.publication_key,
                "runId": RUN_ID,
                "slug": "en",
                "status": "not-a-real-status",
            }
        ),
        encoding="utf-8",
    )
    result = create_claim(identity)
    assert result.acquired is False
    assert result.reason == "invalid_claim"
    assert "Invalid Claim" in stream.getvalue()


def test_build_claim_shape() -> None:
    payload = build_claim(_identity())
    assert payload["status"] == STATUS_ACQUIRED
    validate_claim(payload)
