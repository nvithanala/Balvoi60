from __future__ import annotations

import pytest

from pipeline.errors import ConfigurationError
from pipeline.lib.config_validation import scheduler_enabled


def test_scheduler_enabled_true() -> None:
    assert scheduler_enabled({"SCHEDULER_ENABLED": "true"}) is True


def test_scheduler_enabled_false() -> None:
    assert scheduler_enabled({"SCHEDULER_ENABLED": "false"}) is False


def test_legacy_cron_enabled_warns() -> None:
    with pytest.warns(DeprecationWarning):
        assert scheduler_enabled({"CRON_ENABLED": "true"}) is True


def test_conflicting_scheduler_variables_are_rejected() -> None:
    with pytest.warns(DeprecationWarning), pytest.raises(ConfigurationError, match="conflicts"):
        scheduler_enabled({"SCHEDULER_ENABLED": "true", "CRON_ENABLED": "false"})


def test_scheduler_defaults_disabled() -> None:
    assert scheduler_enabled({}) is False
