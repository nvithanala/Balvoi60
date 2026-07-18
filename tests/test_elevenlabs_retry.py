from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from pipeline.lib import elevenlabs_client


def test_elevenlabs_retries_429_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    limited = Mock(status_code=429, headers={"Retry-After": "0"}, ok=False)
    success = Mock(status_code=200, headers={}, ok=True, content=b"audio")
    post = Mock(side_effect=[limited, success])
    monkeypatch.setattr(elevenlabs_client.requests, "post", post)
    monkeypatch.setattr(elevenlabs_client.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(elevenlabs_client.random, "uniform", lambda _a, _b: 0)
    output = tmp_path / "clip.mp3"
    elevenlabs_client.synthesize("hello", "voice", output)
    assert output.read_bytes() == b"audio"
    assert post.call_count == 2


def test_elevenlabs_stops_after_bounded_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    unavailable = Mock(status_code=503, headers={}, ok=False)
    monkeypatch.setattr(
        elevenlabs_client.requests,
        "post",
        Mock(return_value=unavailable),
    )
    monkeypatch.setattr(elevenlabs_client.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(elevenlabs_client.random, "uniform", lambda _a, _b: 0)
    with pytest.raises(RuntimeError, match="after retries"):
        elevenlabs_client.synthesize("hello", "voice", tmp_path / "clip.mp3")
