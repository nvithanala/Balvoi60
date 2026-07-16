"""ElevenLabs TTS client."""

from __future__ import annotations

import os
import time
from pathlib import Path

import requests

MODEL_ID = "eleven_multilingual_v2"
OUTPUT_FORMAT = "mp3_44100_128"


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
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.3,
            "use_speaker_boost": True,
        },
    }

    for attempt in range(1, 4):
        res = requests.post(
            url,
            headers=headers,
            json=body,
            params={"output_format": OUTPUT_FORMAT},
            timeout=120,
        )
        if res.status_code == 429:
            time.sleep(int(res.headers.get("Retry-After", 10)))
            continue
        if not res.ok:
            raise RuntimeError(f"ElevenLabs {res.status_code}: {res.text[:300]}")
        out_path.write_bytes(res.content)
        return out_path
    raise RuntimeError("ElevenLabs TTS failed after retries")
