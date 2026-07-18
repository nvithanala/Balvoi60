"""ElevenLabs TTS client."""

from __future__ import annotations

import os
import random
import threading
import time
from pathlib import Path

import requests

from pipeline.lib.concurrency import slot

MODEL_ID = "eleven_multilingual_v2"
OUTPUT_FORMAT = "mp3_44100_128"
DEFAULT_VOICE_SETTINGS = {
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.3,
    "use_speaker_boost": True,
}
_metrics_lock = threading.Lock()
_metrics = {"requests": 0, "retries": 0, "rateLimitResponses": 0}


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def reset_metrics() -> None:
    with _metrics_lock:
        _metrics.update(requests=0, retries=0, rateLimitResponses=0)


def metrics_snapshot() -> dict[str, int]:
    with _metrics_lock:
        return dict(_metrics)


def synthesize(text: str, voice_id: str, out_path: Path) -> Path:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY not set")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    body = {
        "text": text,
        "model_id": MODEL_ID,
        "voice_settings": dict(DEFAULT_VOICE_SETTINGS),
    }

    for attempt in range(1, 5):
        with _metrics_lock:
            _metrics["requests"] += 1
            if attempt > 1:
                _metrics["retries"] += 1
        try:
            with slot("tts"):
                res = requests.post(
                    url,
                    headers=headers,
                    json=body,
                    params={"output_format": OUTPUT_FORMAT},
                    timeout=120,
                )
        except requests.RequestException as err:
            if attempt == 4:
                raise RuntimeError(f"ElevenLabs request failed: {type(err).__name__}") from err
            time.sleep((2 ** (attempt - 1)) + random.uniform(0, 0.5))
            continue
        if res.status_code == 429 or 500 <= res.status_code < 600:
            if res.status_code == 429:
                with _metrics_lock:
                    _metrics["rateLimitResponses"] += 1
            if attempt == 4:
                raise RuntimeError(f"ElevenLabs unavailable after retries ({res.status_code})")
            retry_after = res.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else 2 ** (attempt - 1)
            time.sleep(delay + random.uniform(0, 0.5))
            continue
        if not res.ok:
            raise RuntimeError(f"ElevenLabs request rejected ({res.status_code})")
        if not res.content:
            raise RuntimeError("ElevenLabs returned empty audio")
        _atomic_write_bytes(out_path, res.content)
        return out_path
    raise RuntimeError("ElevenLabs TTS failed after retries")
