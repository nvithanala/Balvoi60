from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.audit_published_audio import audit_history


def test_history_audit_reports_missing_zero_and_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    (tmp_path / "episodes" / "run").mkdir(parents=True)
    (tmp_path / "episodes" / "run" / "zero.mp3").write_bytes(b"")
    history = [
        {
            "id": "duplicate",
            "slug": "en",
            "publicationBoundary": "2026-07-08T11:00:00Z",
            "audioUrl": "/episodes/run/missing.mp3",
        },
        {
            "id": "duplicate",
            "slug": "en",
            "publicationBoundary": "2026-07-08T11:00:00Z",
            "audioUrl": "/episodes/run/zero.mp3",
        },
    ]
    path = tmp_path / "history.json"
    path.write_text(json.dumps(history), encoding="utf-8")
    report = audit_history(path)
    assert report["missingAudio"] == 1
    assert report["zeroByteAudio"] == 1
    assert report["duplicateIds"] == ["duplicate"]
    assert report["duplicateBoundaries"] == [
        {"publicationBoundary": "2026-07-08T11:00:00Z", "slug": "en"}
    ]
