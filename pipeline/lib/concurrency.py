"""Bounded external-work concurrency and lightweight run metrics."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager

_lock = threading.Lock()
_limits = {"translation": 4, "tts": 3, "merge": 2}
_semaphores = {name: threading.BoundedSemaphore(value) for name, value in _limits.items()}
_active = {name: 0 for name in _limits}
_peak = {name: 0 for name in _limits}
_elapsed: dict[str, list[float]] = {name: [] for name in _limits}


def configure(*, translation: int, tts: int, merge: int) -> None:
    values = {"translation": translation, "tts": tts, "merge": merge}
    with _lock:
        for name, value in values.items():
            _limits[name] = value
            _semaphores[name] = threading.BoundedSemaphore(value)
            _active[name] = 0
            _peak[name] = 0
            _elapsed[name] = []


@contextmanager
def slot(kind: str):
    semaphore = _semaphores[kind]
    started = time.monotonic()
    semaphore.acquire()
    with _lock:
        _active[kind] += 1
        _peak[kind] = max(_peak[kind], _active[kind])
    try:
        yield
    finally:
        elapsed = time.monotonic() - started
        with _lock:
            _active[kind] -= 1
            _elapsed[kind].append(elapsed)
        semaphore.release()


def snapshot() -> dict:
    with _lock:
        synthesis = _elapsed["tts"]
        return {
            "peakConcurrentTranslationRequests": _peak["translation"],
            "peakConcurrentTtsRequests": _peak["tts"],
            "averageSynthesisSeconds": (
                round(sum(synthesis) / len(synthesis), 3) if synthesis else 0.0
            ),
        }
