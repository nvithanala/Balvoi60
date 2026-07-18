from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from server import scheduler


def test_scheduler_triggers_at_processing_minute() -> None:
    assert scheduler.TRIGGER_MINUTE == 51
    assert scheduler.TRIGGER_MINUTE == scheduler.PROCESSING_TRIGGER_MINUTE


def test_scheduler_at_1051_passes_1100_boundary(monkeypatch) -> None:
    calls = []
    monkeypatch.setenv("PIPELINE_EDITIONS", "en,es")
    monkeypatch.setattr(
        scheduler.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(returncode=0),
    )
    scheduler._run_pipeline(datetime(2026, 7, 17, 10, 51, tzinfo=UTC))
    command = calls[0][0]
    assert command[command.index("--boundary") + 1] == "2026-07-17T11:00:00Z"
    assert command[command.index("--editions") + 1] == "en,es"


def test_scheduler_at_2351_passes_next_day_boundary(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        scheduler.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(returncode=0),
    )
    scheduler._run_pipeline(datetime(2026, 7, 17, 23, 51, tzinfo=UTC))
    command = calls[0][0]
    assert command[command.index("--boundary") + 1] == "2026-07-18T00:00:00Z"
