"""Merge audio segments with ffmpeg."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def merge_segments(segment_paths: list[Path], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    valid = [p for p in segment_paths if p.exists() and p.stat().st_size > 0]

    if not valid:
        output_path.write_bytes(b"")
        return output_path

    if not _has_ffmpeg():
        # Fallback when ffmpeg is not installed: use longest segment
        best = max(valid, key=lambda p: p.stat().st_size)
        shutil.copy2(best, output_path)
        print(f"  [warn] ffmpeg not found — copied {best.name} as episode placeholder")
        return output_path

    list_file = output_path.parent / "concat_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for p in valid:
            safe = str(p.resolve()).replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{safe}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    list_file.unlink(missing_ok=True)

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[:500]}")
    return output_path


def duration_seconds(mp3_path: Path) -> int:
    if not mp3_path.exists() or mp3_path.stat().st_size == 0:
        return 0
    if not shutil.which("ffprobe"):
        # Rough estimate: 128kbps MP3 ≈ 16KB/s
        return max(1, mp3_path.stat().st_size // 16000)
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(mp3_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return int(float(result.stdout.strip() or 0))
    except (ValueError, subprocess.SubprocessError):
        return 0
