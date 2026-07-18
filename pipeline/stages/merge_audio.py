"""Merge audio segments with ffmpeg."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from pipeline.errors import AudioValidationError, MergeError
from pipeline.lib.concurrency import slot


def _require_executable(name: str) -> None:
    if shutil.which(name) is None:
        raise MergeError(f"{name} executable is required")


def merge_segments(segment_paths: list[Path], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    valid = [p for p in segment_paths if p.exists() and p.stat().st_size > 0]

    if not valid:
        raise MergeError("No non-empty audio segments are available to merge")
    _require_executable("ffmpeg")

    list_file = output_path.parent / "concat_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for p in valid:
            safe = str(p.resolve()).replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{safe}'\n")

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        str(output_path),
    ]
    try:
        with slot("merge"):
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.SubprocessError as err:
        raise MergeError(f"ffmpeg execution failed: {type(err).__name__}") from err
    finally:
        list_file.unlink(missing_ok=True)

    if result.returncode != 0:
        output_path.unlink(missing_ok=True)
        raise MergeError(f"ffmpeg failed with exit code {result.returncode}")
    if not output_path.exists():
        raise MergeError("ffmpeg reported success but did not create output")
    if output_path.stat().st_size <= 0:
        output_path.unlink(missing_ok=True)
        raise MergeError("ffmpeg produced empty output")
    return output_path


def duration_seconds(mp3_path: Path) -> int:
    if not mp3_path.exists() or mp3_path.stat().st_size == 0:
        raise MergeError("Merged audio is missing or empty")
    _require_executable("ffprobe")
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(mp3_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise MergeError(f"ffprobe failed with exit code {result.returncode}")
        duration = int(float(result.stdout.strip()))
        if duration <= 0:
            raise MergeError("ffprobe returned a non-positive duration")
        return duration
    except (ValueError, subprocess.SubprocessError) as err:
        raise MergeError(f"Unable to determine audio duration: {type(err).__name__}") from err


def validate_publishable_audio(
    mp3_path: Path,
    duration: int,
    minimum_seconds: int | None = None,
) -> int:
    """Validate the sole runtime rule: final audio must be at least ten minutes."""
    minimum = minimum_seconds or int(os.environ.get("MIN_PUBLISH_DURATION_SECONDS", "600"))
    if not mp3_path.exists():
        raise AudioValidationError(f"Merged audio does not exist: {mp3_path}")
    size = mp3_path.stat().st_size
    if size <= 0:
        raise AudioValidationError(f"Merged audio is empty: {mp3_path}")
    if duration < minimum:
        raise AudioValidationError(f"Merged audio duration {duration}s is below minimum {minimum}s")
    return size
