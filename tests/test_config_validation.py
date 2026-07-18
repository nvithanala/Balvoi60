from __future__ import annotations

import pytest

from pipeline.errors import ConfigurationError
from pipeline.lib.config_validation import validate_pipeline_config


@pytest.fixture(autouse=True)
def _hourly_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BALVOI_ARTICLE_WINDOW_MINUTES", "60")
    monkeypatch.delenv("CRON_ENABLED", raising=False)
    monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)


def test_dry_run_does_not_require_production_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("BALVOI_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    settings = validate_pipeline_config(["en", "es"], dry_run=True)
    assert settings["article_window_minutes"] == 60


def test_non_hourly_article_window_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BALVOI_ARTICLE_WINDOW_MINUTES", "30")
    with pytest.raises(ConfigurationError, match="must be 60"):
        validate_pipeline_config(["en"], dry_run=True)


def test_real_non_english_run_requires_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BALVOI_API_KEY", "test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        validate_pipeline_config(["es"], dry_run=False)
