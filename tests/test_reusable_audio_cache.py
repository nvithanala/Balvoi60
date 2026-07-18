"""Tests for reusable ElevenLabs audio cache."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.lib import reusable_audio_cache as cache
from pipeline.lib.elevenlabs_client import DEFAULT_VOICE_SETTINGS, MODEL_ID


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    return tmp_path


def _payload(**overrides):
    base = cache.build_cache_payload(
        edition_id="balvoi60-en",
        language="en",
        anchor_name="Madison Ray",
        voice_id="FyrYFW3P9GUxA348YGWu",
        segment_type="welcome_back",
        variant_id=2,
        text="Welcome back.",
    )
    base.update(overrides)
    return base


def test_same_payload_same_cache_key(storage: Path) -> None:
    a = _payload()
    b = _payload()
    assert cache.compute_cache_key(a) == cache.compute_cache_key(b)


def test_text_change_invalidates_key(storage: Path) -> None:
    a = cache.compute_cache_key(_payload())
    b = cache.compute_cache_key(_payload(text="Welcome back again."))
    assert a != b


def test_voice_change_invalidates_key(storage: Path) -> None:
    a = cache.compute_cache_key(_payload())
    b = cache.compute_cache_key(_payload(voice_id="other-voice-id"))
    assert a != b


def test_model_and_settings_change_invalidate_key(storage: Path) -> None:
    base = cache.compute_cache_key(_payload())
    other_model = cache.compute_cache_key(_payload(model_id="eleven_turbo_v2"))
    other_settings = cache.compute_cache_key(
        _payload(voice_settings={**DEFAULT_VOICE_SETTINGS, "stability": 0.9})
    )
    assert base != other_model
    assert base != other_settings


def test_missing_file_is_cache_miss(storage: Path) -> None:
    payload = _payload()
    assert cache.lookup(payload) is None


def test_lookup_is_read_only_and_can_use_alternate_roots(storage: Path, tmp_path: Path) -> None:
    payload = _payload()
    production = storage / "audio_assets" / "reusable"
    preview = tmp_path / "preview-reusable"
    assert not production.exists()
    assert cache.lookup(payload, roots=[preview, production]) is None
    assert not production.exists()
    assert not preview.exists()

    written = cache.save_cached_audio(payload, b"preview-bytes", root=preview)
    assert written.is_relative_to(preview)
    assert cache.lookup(payload, roots=[preview, production]) == written
    assert not production.exists()


def test_incomplete_tmp_never_valid(storage: Path) -> None:
    payload = _payload()
    key = cache.compute_cache_key(payload)
    mp3_path, sidecar_path = cache.cache_paths(payload, key)

    tmp = mp3_path.with_name(mp3_path.name + ".tmp")
    tmp.write_bytes(b"incomplete")
    assert cache.lookup(payload) is None
    assert not cache.is_valid_hit(tmp, sidecar_path, key)

    # Finished MP3 without sidecar is still a miss.
    mp3_path.write_bytes(b"fake-audio")
    assert cache.lookup(payload) is None

    # Sidecar alone without matching finished MP3 content key miss after delete.
    mp3_path.unlink()
    sidecar_path.write_text(json.dumps({"cache_key": key}), encoding="utf-8")
    assert cache.lookup(payload) is None


def test_save_and_lookup_hit(storage: Path) -> None:
    payload = _payload()
    path = cache.save_cached_audio(payload, b"\xff\xfb fake mp3")
    assert path.exists()
    assert path.stat().st_size > 0
    hit = cache.lookup(payload)
    assert hit == path
    sidecar = path.with_suffix(".json")
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    assert meta["cache_key"] == cache.compute_cache_key(payload)
    assert meta["model_id"] == MODEL_ID
    assert meta["text"] == "Welcome back."


def test_primary_anchors_include_all_shifts_skip_null_secondary() -> None:
    edition = {
        "id": "balvoi60-en",
        "slug": "en",
        "voiceShifts": [
            {
                "primary": {"name": "Madison Ray", "voiceId": "v1"},
                "secondary": {"name": "Elena Hart", "voiceId": None},
            },
            {
                "primary": {"name": "Grant Whitman", "voiceId": "v2"},
                "secondary": {"name": "Nolan Pierce", "voiceId": None},
            },
            {
                "primary": {"name": "Sarah", "voiceId": "v3"},
                "secondary": {"name": "Claire Rowan", "voiceId": None},
            },
            {
                # Null primary must be skipped.
                "primary": {"name": "Broken", "voiceId": None},
                "secondary": {"name": "Also Broken", "voiceId": "should-not-use"},
            },
        ],
    }
    anchors = cache.primary_anchors_for_edition(edition)
    names = [a["name"] for a in anchors]
    assert names == ["Madison Ray", "Grant Whitman", "Sarah"]
    assert all(a["voiceId"] for a in anchors)


def test_prerender_jobs_cover_all_primary_anchors(storage: Path) -> None:
    edition = {
        "id": "balvoi60-en",
        "slug": "en",
        "voiceShifts": [
            {"primary": {"name": "Madison Ray", "voiceId": "v1"}, "secondary": {"voiceId": None}},
            {"primary": {"name": "Grant Whitman", "voiceId": "v2"}, "secondary": {"voiceId": None}},
            {"primary": {"name": "Sarah", "voiceId": "v3"}, "secondary": {"voiceId": None}},
        ],
    }
    segments_doc = {
        "welcome": {"en": ["Welcome to BalVoi."]},
        "started": {"en": ["Let's get started."]},
        "right_back": {"en": ["We'll be right back."]},
        "welcome_back": {"en": ["Welcome back."]},
        "thank_you": {"en": ["Thank you."]},
    }
    # Prerecorded right_back present → TTS right_back jobs skipped.
    assets_doc = {"balvoi60-en": {"right_back": ["English/Right Back/x.mp3"]}}

    jobs = cache.reusable_variant_jobs(edition, segments_doc, assets_doc)
    anchors = {j["anchor_name"] for j in jobs}
    assert anchors == {"Madison Ray", "Grant Whitman", "Sarah"}
    assert all(j["segment_type"] != "right_back" for j in jobs)
    # 3 anchors × (welcome+started+welcome_back+thank_you) = 12
    assert len(jobs) == 12


def test_prerender_includes_right_back_without_assets(storage: Path) -> None:
    edition = {
        "id": "balvoi60-en",
        "slug": "en",
        "voiceShifts": [
            {"primary": {"name": "Madison Ray", "voiceId": "v1"}},
        ],
    }
    segments_doc = {
        "welcome": {"en": ["Welcome."]},
        "started": {"en": []},
        "right_back": {"en": ["Be right back."]},
        "welcome_back": {"en": []},
        "thank_you": {"en": []},
    }
    jobs = cache.reusable_variant_jobs(edition, segments_doc, assets_doc={})
    types = {j["segment_type"] for j in jobs}
    assert "right_back" in types
    assert "welcome" in types
