"""Structured logging helpers for the BalVoi:60 pipeline (T-M1-005).

Additive only. When ``BALVOI_STRUCTURED_LOGS`` is not true, structured emitters
are no-ops so existing ``print`` behavior remains the operator-visible path.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|authorization|password|secret|credential)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)((?:api[_-]?key|token|bearer|authorization)\s*[:=]\s*)([^\s\"']+)"
)

_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("balvoi_log_context", default={})
_CONFIGURED = False
_CONFIG_LOCK = threading.Lock()

# Canonical optional context keys (omitted when missing / empty).
CONTEXT_KEYS = (
    "runId",
    "publicationBoundary",
    "boundaryKey",
    "publicationKey",
    "slug",
    "language",
    "editionId",
    "stage",
    "event",
    "elapsedMs",
    "worker",
    "processId",
)


def structured_logs_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return True when ``BALVOI_STRUCTURED_LOGS`` is an explicit true value."""
    env = environ if environ is not None else os.environ
    raw = str(env.get("BALVOI_STRUCTURED_LOGS", "") or "").strip().lower()
    return raw in {"true", "1", "yes", "on"}


def redact_secrets(value: Any) -> Any:
    """Redact secret-looking keys/values from structures destined for logs."""
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if _SECRET_KEY_RE.search(str(key)):
                out[str(key)] = "***"
            else:
                out[str(key)] = redact_secrets(item)
        return out
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE_RE.sub(r"\1***", value)
    return value


def _clean_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        cleaned[key] = value
    return redact_secrets(cleaned)


class JsonFormatter(logging.Formatter):
    """One JSON object per log line (stack traces under ``exception``)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "logger": record.name,
        }
        extras = getattr(record, "balvoi_fields", None)
        if isinstance(extras, dict):
            payload.update(extras)
        if record.getMessage() and "event" not in payload:
            payload["message"] = record.getMessage()
        elif record.getMessage() and payload.get("message") is None:
            # Prefer explicit message field; keep event as the stable name.
            if "message" not in payload:
                payload["message"] = record.getMessage()
        if record.exc_info:
            payload["exception"] = redact_secrets(
                self.formatException(record.exc_info).strip()
            )
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))


def get_logger(name: str = "balvoi.pipeline") -> logging.Logger:
    """Return a process logger; configure JSON stdout handler once when enabled."""
    global _CONFIGURED
    logger = logging.getLogger(name)
    if not structured_logs_enabled():
        return logger
    with _CONFIG_LOCK:
        if not _CONFIGURED:
            handler = logging.StreamHandler()
            handler.setFormatter(JsonFormatter())
            root = logging.getLogger("balvoi")
            # Avoid duplicate handlers on reload.
            if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
                root.addHandler(handler)
            root.setLevel(logging.INFO)
            root.propagate = False
            _CONFIGURED = True
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)
    return logger


def reset_logging_for_tests() -> None:
    """Clear configuration state (tests only)."""
    global _CONFIGURED
    with _CONFIG_LOCK:
        _CONFIGURED = False
        root = logging.getLogger("balvoi")
        for handler in list(root.handlers):
            root.removeHandler(handler)
        _CONTEXT.set({})


@contextmanager
def bind_context(**fields: Any) -> Iterator[dict[str, Any]]:
    """Temporarily bind structured context fields for the current task/thread."""
    current = dict(_CONTEXT.get())
    merged = {**current, **{k: v for k, v in fields.items() if v is not None and v != ""}}
    token = _CONTEXT.set(merged)
    try:
        yield dict(merged)
    finally:
        _CONTEXT.reset(token)


def current_context() -> dict[str, Any]:
    return dict(_CONTEXT.get())


def log_event(
    event: str,
    *,
    level: int = logging.INFO,
    stage: str | None = None,
    message: str | None = None,
    logger: logging.Logger | None = None,
    exc_info: bool | BaseException | None = False,
    **fields: Any,
) -> None:
    """Emit one structured event when the feature flag is enabled; otherwise no-op."""
    if not structured_logs_enabled():
        return
    log = logger or get_logger()
    payload = _clean_fields(
        {
            **current_context(),
            **fields,
            "event": event,
            "stage": stage if stage is not None else fields.get("stage"),
            "message": message,
            "processId": fields.get("processId") or os.getpid(),
            "worker": fields.get("worker") or threading.current_thread().name,
        }
    )
    # Drop keys that are still empty after merge.
    payload = {k: v for k, v in payload.items() if v is not None and v != ""}
    exc: bool | tuple | None
    if exc_info is True:
        exc = True
    elif isinstance(exc_info, BaseException):
        exc = (type(exc_info), exc_info, exc_info.__traceback__)
    else:
        exc = None
    log.log(level, message or event, extra={"balvoi_fields": payload}, exc_info=exc)


def log_exception(
    event: str,
    err: BaseException,
    *,
    stage: str | None = None,
    message: str | None = None,
    **fields: Any,
) -> None:
    """Log an exception with structured metadata (``logger.exception`` equivalent)."""
    log_event(
        event,
        level=logging.ERROR,
        stage=stage,
        message=message or f"{type(err).__name__}: {err}",
        errorType=type(err).__name__,
        errorMessage=redact_secrets(str(err)),
        exc_info=err,
        **fields,
    )


def announce(
    legacy_message: str,
    event: str,
    *,
    stage: str | None = None,
    **fields: Any,
) -> None:
    """Print when structured logs are off; emit structured JSON when on."""
    if structured_logs_enabled():
        log_event(event, stage=stage, message=legacy_message.strip(), **fields)
    else:
        print(legacy_message)
