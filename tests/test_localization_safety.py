from __future__ import annotations

from unittest.mock import Mock

import pytest
import requests

from pipeline.errors import LocalizationError
from pipeline.lib import openai_client
from pipeline.stages.transform_stories import localize_stories


def test_missing_openai_key_rejects_non_english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LocalizationError, match="OPENAI_API_KEY"):
        openai_client.translate("English source", "Spanish")


def test_translation_api_exception_is_localization_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        openai_client.requests,
        "post",
        Mock(side_effect=requests.Timeout("timeout")),
    )
    with pytest.raises(LocalizationError, match="localization failed"):
        openai_client.translate("English source", "Arabic")


def test_empty_translation_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": ""}}]}
    monkeypatch.setattr(openai_client.requests, "post", Mock(return_value=response))
    with pytest.raises(LocalizationError):
        openai_client.translate("English source", "French")


def test_english_does_not_require_translation_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert openai_client.translate("English source", "English") == "English source"


def test_failed_language_never_becomes_english_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    stories = [{"id": "1", "broadcastScript": "English source", "primer": "Headline"}]
    with pytest.raises(LocalizationError):
        localize_stories(stories, "Spanish")
