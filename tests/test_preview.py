from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipeline import preview
from pipeline.config_loader import get_voice_for_edition
from pipeline.errors import LocalizationError
from pipeline.stages.assemble_episode import assemble_episode


def test_assemble_episode_uses_publication_boundary_for_voice() -> None:
    edition = {
        "id": "balvoi60-en",
        "slug": "en",
        "name": "BalVoi:60 News",
        "editionName": "Five Eyes Edition",
        "language": "English",
        "locale": "en-US",
        "city": "New York City",
        "timezone": "UTC",
        "voiceShifts": [
            {
                "start": "00:01",
                "end": "08:00",
                "primary": {"name": "Madison Ray", "voiceId": "voice-night"},
            },
            {
                "start": "08:01",
                "end": "16:00",
                "primary": {"name": "Grant Whitman", "voiceId": "voice-day"},
            },
            {
                "start": "16:01",
                "end": "24:00",
                "primary": {"name": "Sarah", "voiceId": "voice-evening"},
            },
        ],
    }
    boundary = datetime(2026, 7, 17, 11, 0, tzinfo=UTC)
    expected = get_voice_for_edition(edition, when=boundary)
    manifest = assemble_episode(
        edition,
        [{"id": "story-1", "broadcastScript": "Hello world.", "primer": "Hello."}],
        "preview-run",
        headlines_text="Hello.",
        when=boundary,
    )
    assert manifest["voice"]["voiceId"] == expected["voiceId"]
    assert "11:00 AM" in manifest["segments"][1]["text"]


def test_preview_manifest_hash_is_stable() -> None:
    boundary = datetime(2026, 7, 17, 11, 0, tzinfo=UTC)
    window_start = datetime(2026, 7, 17, 9, 51, tzinfo=UTC)
    window_end = datetime(2026, 7, 17, 10, 51, tzinfo=UTC)
    selected = [
        {
            "id": "story-1",
            "title": "Markets rise",
            "url": "https://example.com/1",
            "source": "BalVoi",
        }
    ]
    first = preview._manifest_payload(
        run_id="preview-1",
        boundary=boundary,
        window_start=window_start,
        window_end=window_end,
        selected=selected,
        decisions=[],
        selected_at=datetime(2026, 7, 17, 10, 51, tzinfo=UTC),
    )
    second = preview._manifest_payload(
        run_id="preview-1",
        boundary=boundary,
        window_start=window_start,
        window_end=window_end,
        selected=selected,
        decisions=[],
        selected_at=datetime(2026, 7, 17, 10, 51, tzinfo=UTC),
    )
    assert first["manifestHash"] == second["manifestHash"]
    assert first["orderedStoryIds"] == ["story-1"]


def test_preview_production_state_detects_history_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    history = manifests / "history.json"
    history.write_text("[]", encoding="utf-8")
    before = preview.production_state()
    history.write_text('[{"slug":"en"}]', encoding="utf-8")
    after = preview.production_state()
    assert not preview._state_unchanged(before, after, "history")


def test_preview_run_rejects_megaphone_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("MEGAPHONE_ENABLED", "true")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("CRON_ENABLED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with pytest.raises(RuntimeError, match="MEGAPHONE_ENABLED"):
        preview.run_preview(
            run_id="preview-unsafe",
            boundary=datetime(2026, 7, 17, 11, 0, tzinfo=UTC),
            edition_slugs=["en"],
            settings={
                "language_workers": 1,
                "translation_workers": 1,
                "tts_workers": 1,
                "merge_workers": 1,
                "story_cooldown_minutes": 360,
                "minimum_publish_seconds": 600,
            },
        )


def test_preview_run_rejects_missing_openai_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("MEGAPHONE_ENABLED", "false")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("CRON_ENABLED", "false")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        preview.run_preview(
            run_id="preview-no-openai",
            boundary=datetime(2026, 7, 17, 11, 0, tzinfo=UTC),
            edition_slugs=["en"],
            settings={
                "language_workers": 1,
                "translation_workers": 1,
                "tts_workers": 1,
                "merge_workers": 1,
                "story_cooldown_minutes": 360,
                "minimum_publish_seconds": 600,
            },
        )


def test_preview_language_failure_status_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    edition = {
        "slug": "es",
        "language": "Spanish",
        "name": "BalVoi:60 Noticias",
        "locale": "es-MX",
        "timezone": "America/Mexico_City",
        "city": "Mexico City",
        "editionName": "Edición Latinoamérica",
        "id": "balvoi60-es",
        "voiceShifts": [
            {
                "start": "00:01",
                "end": "24:00",
                "primary": {"name": "Alonso", "voiceId": "voice-es"},
            }
        ],
    }

    def boom(*_args, **_kwargs):
        raise LocalizationError("translation failed")

    monkeypatch.setattr(preview, "localize_stories", boom)
    result = preview._language_result(
        edition,
        [{"id": "story-1", "title": "One", "broadcastScript": "Hello", "primer": "Hello"}],
        run_id="preview-x",
        preview_dir=tmp_path,
        boundary=datetime(2026, 7, 17, 11, 0, tzinfo=UTC),
        manifest_hash="abc",
        minimum_seconds=600,
    )
    assert result["status"] == "preview_failed_translation"
    assert "published" not in result["status"]
    metadata = json.loads((tmp_path / "metadata" / "Spanish.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "preview_failed_translation"
