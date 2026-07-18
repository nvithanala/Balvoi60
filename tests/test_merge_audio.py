from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.errors import MergeError
from pipeline.stages import merge_audio


def _segment(tmp_path: Path) -> Path:
    path = tmp_path / "segment.mp3"
    path.write_bytes(b"segment")
    return path


def test_ffmpeg_missing_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(merge_audio.shutil, "which", lambda _name: None)
    with pytest.raises(MergeError, match="ffmpeg"):
        merge_audio.merge_segments([_segment(tmp_path)], tmp_path / "out.mp3")


def test_ffmpeg_nonzero_exit_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(merge_audio.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        merge_audio.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stderr="failed"),
    )
    with pytest.raises(MergeError, match="exit code 1"):
        merge_audio.merge_segments([_segment(tmp_path)], tmp_path / "out.mp3")


def test_missing_merged_output_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(merge_audio.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        merge_audio.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stderr=""),
    )
    with pytest.raises(MergeError, match="did not create"):
        merge_audio.merge_segments([_segment(tmp_path)], tmp_path / "out.mp3")


def test_empty_merged_output_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(merge_audio.shutil, "which", lambda name: name)

    def run(command, **_kwargs):
        Path(command[-1]).write_bytes(b"")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(merge_audio.subprocess, "run", run)
    with pytest.raises(MergeError, match="empty"):
        merge_audio.merge_segments([_segment(tmp_path)], tmp_path / "out.mp3")


def test_duration_probe_failure_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = _segment(tmp_path)
    monkeypatch.setattr(merge_audio.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        merge_audio.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""),
    )
    with pytest.raises(MergeError, match="ffprobe"):
        merge_audio.duration_seconds(audio)


def test_valid_merge_and_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "out.mp3"
    monkeypatch.setattr(merge_audio.shutil, "which", lambda name: name)

    def run(command, **_kwargs):
        if command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"merged")
            return SimpleNamespace(returncode=0, stderr="")
        return SimpleNamespace(returncode=0, stdout="601.5")

    monkeypatch.setattr(merge_audio.subprocess, "run", run)
    merge_audio.merge_segments([_segment(tmp_path)], output)
    assert merge_audio.duration_seconds(output) == 601
    assert merge_audio.validate_publishable_audio(output, 601) == len(b"merged")
