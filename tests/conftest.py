"""Shared pytest fixtures."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 6, 16, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def patch_select_now(monkeypatch: pytest.MonkeyPatch, fixed_now: datetime):
    """Freeze ``select_stories`` wall clock."""

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is None else fixed_now.astimezone(tz)

    import pipeline.stages.select_stories as mod

    monkeypatch.setattr(mod, "datetime", FrozenDatetime)
    return fixed_now


@pytest.fixture(autouse=True)
def _reset_storage_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests isolated from a developer's local ``STORAGE_PATH``."""
    monkeypatch.delenv("STORAGE_PATH", raising=False)
