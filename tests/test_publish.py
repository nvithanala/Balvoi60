from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipeline.errors import PublishRejectedError
from pipeline.stages.publish import publish_run
from tests.helpers import SAMPLE_EDITION

BOUNDARY = datetime(2026, 7, 8, 11, 0, tzinfo=UTC)


def _publish(audio: Path) -> dict:
    manifest = {
        "voice": {"name": "Anchor"},
        "storyIds": ["story-1"],
        "picks": {},
    }
    stories = [{"id": "story-1", "title": "Headline"}]
    return publish_run(
        "2026-07-08T11-00-00Z",
        SAMPLE_EDITION,
        manifest,
        audio,
        600,
        stories,
        publication_boundary=BOUNDARY,
    )


def test_missing_audio_rejected_without_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    with pytest.raises(PublishRejectedError):
        _publish(tmp_path / "missing.mp3")
    assert not (tmp_path / "storage" / "manifests").exists()


def test_zero_byte_audio_rejected_without_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    audio = tmp_path / "empty.mp3"
    audio.write_bytes(b"")
    with pytest.raises(PublishRejectedError):
        _publish(audio)
    assert not (tmp_path / "storage" / "manifests").exists()


def test_valid_audio_updates_public_metadata_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / "storage"
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    audio = tmp_path / "valid.mp3"
    audio.write_bytes(b"audio")
    episode = _publish(audio)
    history = json.loads((storage / "manifests" / "history.json").read_text())
    assert history == [episode]
    assert json.loads((storage / "manifests" / "latest.json").read_text())["en"] == episode
    assert (storage / "manifests" / "runs" / f"{episode['id']}.json").exists()
