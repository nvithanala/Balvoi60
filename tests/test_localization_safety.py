from __future__ import annotations

from unittest.mock import Mock

import pytest
import requests

from pipeline.errors import LocalizationError
from pipeline.lib import openai_client
from pipeline.stages.transform_stories import localize_stories


def _bedrock_response(text: str, *, status: int = 200) -> Mock:
    response = Mock()
    response.status_code = status
    response.raise_for_status.return_value = None
    response.json.return_value = {"data": {"text": text}}
    return response


def test_missing_api_key_rejects_non_english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BALVOI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LocalizationError, match="BALVOI_API_KEY"):
        openai_client.translate("English source", "Spanish")


def test_translation_api_exception_is_localization_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BALVOI_API_KEY", "test-key")
    monkeypatch.setattr(
        openai_client.requests,
        "post",
        Mock(side_effect=requests.Timeout("timeout")),
    )
    with pytest.raises(LocalizationError, match="localization failed"):
        openai_client.translate("English source", "Arabic")


def test_empty_translation_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BALVOI_API_KEY", "test-key")
    monkeypatch.setattr(
        openai_client.requests, "post", Mock(return_value=_bedrock_response(""))
    )
    with pytest.raises(LocalizationError):
        openai_client.translate("English source", "French")


def test_english_does_not_require_translation_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BALVOI_API_KEY", raising=False)
    assert openai_client.translate("English source", "English") == "English source"


def test_failed_language_never_becomes_english_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BALVOI_API_KEY", raising=False)
    english = [{"id": "1", "broadcastScript": "Hello", "primer": "Hi"}]
    with pytest.raises(LocalizationError):
        localize_stories(english, "Spanish")


def test_bedrock_payload_and_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BALVOI_API_KEY", "ng-secret")
    monkeypatch.setenv("BALVOI_API_URL", "https://api.staging.newsgenie.ai")
    post = Mock(return_value=_bedrock_response("Hola"))
    monkeypatch.setattr(openai_client.requests, "post", post)
    assert openai_client.translate("Hello", "Spanish") == "Hola"
    assert post.call_args.args[0] == "https://api.staging.newsgenie.ai/bedrock/prompt"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer ng-secret"
    assert post.call_args.kwargs["json"] == {
        "prompt": post.call_args.kwargs["json"]["prompt"],
        "data": "Hello",
    }
    assert "Spanish" in post.call_args.kwargs["json"]["prompt"]
