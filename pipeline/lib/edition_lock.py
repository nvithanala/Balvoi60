"""Atomic per-boundary/language duplicate prevention."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

from balvoi.dates import format_iso_utc, parse_iso_datetime
from balvoi.paths import storage_root
from pipeline.errors import DuplicateEditionError


def boundary_key(boundary: datetime) -> str:
    return format_iso_utc(boundary).replace(":", "-")


def edition_was_published(boundary: datetime, slug: str) -> bool:
    expected = format_iso_utc(boundary)
    history_path = storage_root() / "manifests" / "history.json"
    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return any(
        row.get("slug") == slug
        and row.get("publicationBoundary") == expected
        and row.get("durationSeconds", 0) >= 600
        for row in history
    )


@dataclass
class EditionLock:
    boundary: datetime
    slug: str
    stale_seconds: int = 7200

    def __post_init__(self) -> None:
        key = boundary_key(self.boundary)
        self.path = storage_root() / "locks" / f"{key}-{self.slug}.lock"
        self.token = uuid.uuid4().hex
        self.acquired = False

    def acquire(self) -> None:
        if edition_was_published(self.boundary, self.slug):
            raise DuplicateEditionError("already_published")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "token": self.token,
                "pid": os.getpid(),
                "createdAt": time.time(),
                "boundary": format_iso_utc(self.boundary),
                "slug": self.slug,
            }
        ).encode()
        for attempt in range(2):
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
                self.acquired = True
                return
            except FileExistsError as err:
                if attempt == 0 and self._is_stale():
                    self.path.unlink(missing_ok=True)
                    continue
                raise DuplicateEditionError("duplicate_blocked") from err
        raise DuplicateEditionError("duplicate_blocked")

    def _is_stale(self) -> bool:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            created = float(data.get("createdAt", 0))
            boundary = parse_iso_datetime(data.get("boundary"))
            return (
                time.time() - created > self.stale_seconds
                and boundary is not None
                and time.time() - boundary.timestamp() > self.stale_seconds
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return True

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("token") == self.token:
                self.path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass
        self.acquired = False
