from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from balvoi.dates import format_iso_utc
from pipeline.errors import DuplicateEditionError
from pipeline.lib.edition_lock import EditionLock

BOUNDARY = datetime(2026, 7, 8, 11, 0, tzinfo=UTC)


def test_two_attempts_for_same_boundary_are_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    first = EditionLock(BOUNDARY, "en")
    second = EditionLock(BOUNDARY, "en")
    first.acquire()
    with pytest.raises(DuplicateEditionError, match="duplicate_blocked"):
        second.acquire()
    first.release()


def test_manual_and_scheduled_collision_share_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    scheduled = EditionLock(BOUNDARY, "es")
    manual = EditionLock(BOUNDARY, "es")
    scheduled.acquire()
    with pytest.raises(DuplicateEditionError):
        manual.acquire()
    scheduled.release()


def test_retry_after_failed_run_is_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    failed = EditionLock(BOUNDARY, "fr")
    failed.acquire()
    failed.release()
    retry = EditionLock(BOUNDARY, "fr")
    retry.acquire()
    retry.release()


def test_retry_after_success_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    (manifests / "history.json").write_text(
        json.dumps(
            [
                {
                    "slug": "de",
                    "publicationBoundary": format_iso_utc(BOUNDARY),
                    "durationSeconds": 600,
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(DuplicateEditionError, match="already_published"):
        EditionLock(BOUNDARY, "de").acquire()


def test_stale_lock_is_reclaimed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    lock = EditionLock(BOUNDARY, "ar", stale_seconds=1)
    lock.path.parent.mkdir(parents=True)
    lock.path.write_text(
        json.dumps(
            {
                "token": "dead",
                "createdAt": time.time() - 10,
                "boundary": format_iso_utc(BOUNDARY),
            }
        ),
        encoding="utf-8",
    )
    lock.acquire()
    lock.release()


def test_separate_languages_same_boundary_are_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    en = EditionLock(BOUNDARY, "en")
    tr = EditionLock(BOUNDARY, "tr")
    en.acquire()
    tr.acquire()
    en.release()
    tr.release()
