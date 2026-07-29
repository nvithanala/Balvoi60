"""Canonical UTC scheduler: process at :45, publish for the next hour's :00."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime

from balvoi.dates import (
    PROCESSING_TRIGGER_MINUTE,
    format_iso_utc,
    publication_boundary,
)
from balvoi.paths import ROOT
from pipeline.lib.logging_utils import announce, log_event, log_exception, structured_logs_enabled
from pipeline.lib.publication_identity import boundary_key

# Processing starts when the ownership window closes (:45); publication is next :00.
TRIGGER_MINUTE = PROCESSING_TRIGGER_MINUTE


def _run_pipeline(now: datetime | None = None) -> None:
    processing_started = (now or datetime.now(UTC)).astimezone(UTC)
    boundary = publication_boundary(processing_started)
    editions = os.environ.get("PIPELINE_EDITIONS", "en")
    boundary_text = format_iso_utc(boundary)
    bkey = boundary_key(boundary)
    log_event(
        "Scheduler Tick",
        stage="scheduler",
        publicationBoundary=boundary_text,
        boundaryKey=bkey,
        processingStarted=format_iso_utc(processing_started),
        editions=editions,
    )
    log_event(
        "Boundary Calculated",
        stage="scheduler",
        publicationBoundary=boundary_text,
        boundaryKey=bkey,
    )
    announce(
        "[scheduler] processing trigger "
        f"{format_iso_utc(processing_started)} → publication boundary "
        f"{boundary_text} ({editions})...",
        "Pipeline Spawned",
        stage="scheduler",
        publicationBoundary=boundary_text,
        boundaryKey=bkey,
        editions=editions,
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pipeline",
            "--editions",
            editions,
            "--boundary",
            boundary_text,
        ],
        cwd=str(ROOT),
    )
    announce(
        f"[scheduler] pipeline finished (exit {result.returncode})",
        "Pipeline Exit Code",
        stage="scheduler",
        publicationBoundary=boundary_text,
        boundaryKey=bkey,
        exitCode=result.returncode,
    )


def _loop() -> None:
    last_slot: tuple[int, int] | None = None
    while True:
        now = datetime.now(UTC)
        slot = (now.hour, now.minute)
        if now.minute == TRIGGER_MINUTE and now.second < 30 and slot != last_slot:
            last_slot = slot
            try:
                _run_pipeline(now)
            except Exception as err:  # keep the scheduler alive on failures
                log_exception("Scheduler Exception", err, stage="scheduler")
                if not structured_logs_enabled():
                    print(f"[scheduler] run error: {err}")
        time.sleep(15)


def start_scheduler() -> threading.Thread:
    thread = threading.Thread(target=_loop, name="balvoi-scheduler", daemon=True)
    thread.start()
    editions = os.environ.get("PIPELINE_EDITIONS", "en")
    announce(
        f"[scheduler] active: process at UTC :{TRIGGER_MINUTE:02d}, "
        f"publish at next :00 (editions: {editions})",
        "Scheduler Started",
        stage="scheduler",
        editions=editions,
    )
    return thread


if __name__ == "__main__":
    start_scheduler()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        if structured_logs_enabled():
            log_event("Scheduler Stopped", stage="scheduler")
        else:
            print("\n[scheduler] stopped")
