"""Python port of the production scheduler: fire the pipeline at :25 and :55."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime

from balvoi.paths import ROOT, pipeline_lock

LOCK = pipeline_lock()
TRIGGER_MINUTES = {25, 55}


def _run_pipeline() -> None:
    if LOCK.exists():
        print("[scheduler] pipeline locked — skipping")
        return
    editions = os.environ.get("PIPELINE_EDITIONS", "en")
    print(f"[scheduler] starting pipeline ({editions})...")
    result = subprocess.run(
        [sys.executable, "-m", "pipeline", "--editions", editions],
        cwd=str(ROOT),
    )
    print(f"[scheduler] pipeline finished (exit {result.returncode})")


def _loop() -> None:
    last_slot: tuple[int, int] | None = None
    while True:
        now = datetime.now(UTC)
        slot = (now.hour, now.minute)
        if now.minute in TRIGGER_MINUTES and now.second < 30 and slot != last_slot:
            last_slot = slot
            try:
                _run_pipeline()
            except Exception as err:  # keep the scheduler alive on failures
                print(f"[scheduler] run error: {err}")
        time.sleep(15)


def start_scheduler() -> threading.Thread:
    thread = threading.Thread(target=_loop, name="balvoi-scheduler", daemon=True)
    thread.start()
    editions = os.environ.get("PIPELINE_EDITIONS", "en")
    print(f"[scheduler] active: :25 and :55 each hour (editions: {editions})")
    return thread


if __name__ == "__main__":
    start_scheduler()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n[scheduler] stopped")
