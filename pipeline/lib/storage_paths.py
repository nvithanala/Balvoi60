"""Canonical storage path builder (T-M1-004).

Preserves the existing on-disk layout exactly. Path calculation never creates
directories; callers must mkdir explicitly.

Does not recreate publication identity — callers supply validated ``run_id`` /
``edition_slug`` / ``boundary_key`` strings.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from balvoi.paths import ROOT

if TYPE_CHECKING:
    from pipeline.lib.settings import AppSettings

_TRAVERSAL_RE = re.compile(r"(^|[\\/])\.\.([\\/]|$)")
_SEP_IN_SEGMENT = re.compile(r"[\\/]")


class StoragePathError(ValueError):
    """Invalid storage path component or escape attempt."""


def resolve_storage_root(configured: str | None = None) -> Path:
    """Resolve storage root from ``STORAGE_PATH`` / settings value (cwd-independent).

    Relative paths are anchored at the repository ``ROOT``, never the process CWD.
    """
    raw = (configured if configured is not None else os.environ.get("STORAGE_PATH", "storage"))
    text = str(raw or "storage").strip() or "storage"
    path = Path(text)
    if path.is_absolute():
        return path
    return ROOT / path


def _reject_unsafe_segment(name: str, *, label: str) -> str:
    text = str(name or "").strip()
    if not text:
        raise StoragePathError(f"{label} is required")
    if text in {".", ".."}:
        raise StoragePathError(f"{label} must not be '.' or '..'")
    if _TRAVERSAL_RE.search(text) or ".." in text:
        raise StoragePathError(f"{label} must not contain path traversal")
    if _SEP_IN_SEGMENT.search(text):
        raise StoragePathError(f"{label} must not contain path separators")
    if Path(text).is_absolute():
        raise StoragePathError(f"{label} must not be an absolute path")
    # Windows drive / UNC style
    if len(text) >= 2 and text[1] == ":":
        raise StoragePathError(f"{label} must not be an absolute path")
    if text.startswith(("\\\\", "//")):
        raise StoragePathError(f"{label} must not be an absolute path")
    return text


def validate_run_id(run_id: str) -> str:
    """Validate a run_id for path use (does not require canonical production format)."""
    return _reject_unsafe_segment(run_id, label="run_id")


def validate_edition_slug(slug: str) -> str:
    """Validate/normalize edition slug via publication identity (no key recreation)."""
    from pipeline.lib.publication_identity import normalize_edition_slug

    return normalize_edition_slug(slug)


def validate_boundary_key(key: str) -> str:
    return _reject_unsafe_segment(key, label="boundary_key")


def _strip_win_extended_prefix(path: Path) -> Path:
    """Normalize Windows ``\\\\?\\`` extended paths for stable comparisons."""
    text = str(path)
    if text.startswith("\\\\?\\"):
        return Path(text[4:])
    if text.startswith("//?/"):
        return Path(text[4:])
    return path


def _ensure_within_root(root: Path, candidate: Path) -> Path:
    """Return ``candidate`` if it cannot escape ``root`` when resolved."""
    root_resolved = _strip_win_extended_prefix(root.resolve())
    candidate_resolved = _strip_win_extended_prefix(candidate.resolve(strict=False))
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError as err:
        raise StoragePathError(
            f"path escapes storage root: {candidate} (root={root})"
        ) from err
    return candidate


@dataclass(frozen=True)
class StoragePaths:
    """Top-level storage layout under the configured root."""

    root: Path

    @classmethod
    def from_env(cls) -> StoragePaths:
        return cls(root=resolve_storage_root())

    @classmethod
    def from_settings(cls, settings: AppSettings) -> StoragePaths:
        return cls(root=resolve_storage_root(settings.storage_path))

    @classmethod
    def from_root(cls, root: Path | str) -> StoragePaths:
        return cls(root=Path(root))

    @property
    def runs_root(self) -> Path:
        return self.root / "runs"

    @property
    def episodes_root(self) -> Path:
        return self.root / "episodes"

    @property
    def manifests_root(self) -> Path:
        return self.root / "manifests"

    @property
    def locks_root(self) -> Path:
        return self.root / "locks"

    @property
    def previews_root(self) -> Path:
        return self.root / "previews"

    @property
    def reusable_audio_root(self) -> Path:
        return self.root / "audio_assets" / "reusable"

    @property
    def tts_cache_root(self) -> Path:
        return self.root / "cache" / "tts"

    @property
    def articles_root(self) -> Path:
        return self.root / "articles"

    @property
    def logs_root(self) -> Path:
        return self.root / "logs"

    @property
    def history_path(self) -> Path:
        return self.manifests_root / "history.json"

    @property
    def latest_path(self) -> Path:
        return self.manifests_root / "latest.json"

    @property
    def status_aggregate_path(self) -> Path:
        """Legacy/global status file written by ``publish_run``."""
        return self.manifests_root / "status.json"

    @property
    def articles_cache_path(self) -> Path:
        return self.articles_root / "latest.json"

    @property
    def pipeline_lock_path(self) -> Path:
        return self.root / ".pipeline.lock"

    def run(self, run_id: str) -> RunPaths:
        return RunPaths(storage=self, run_id=validate_run_id(run_id))

    def preview(self, run_id: str) -> PreviewPaths:
        return PreviewPaths(storage=self, run_id=validate_run_id(run_id))

    def production_selection_manifest(self, *, run_id: str | None = None, boundary_key: str | None = None) -> Path:
        """Hourly frozen selection: ``manifests/selection/<boundary_key>.json``.

        Authoritative for the main ``pipeline.run`` selection freeze. When both
        are omitted, raises. Prefer ``boundary_key``; ``run_id`` is accepted
        because production default ``run_id`` equals ``boundary_key``.
        """
        token = run_id if run_id is not None else boundary_key
        if token is None:
            raise StoragePathError("run_id or boundary_key is required")
        safe = validate_run_id(token) if run_id is not None else validate_boundary_key(token)
        return _ensure_within_root(self.root, self.manifests_root / "selection" / f"{safe}.json")

    def lock_path(self, boundary_key: str, slug: str) -> Path:
        key = validate_boundary_key(boundary_key)
        edition = validate_edition_slug(slug)
        return _ensure_within_root(self.root, self.locks_root / f"{key}-{edition}.lock")

    def once_publication_record_path(self, publication_key: str) -> Path:
        """Once-path publication sidecar: ``manifests/megaphone_once/<key>.json``."""
        return self._once_key_path(publication_key, suffix=".json")

    def megaphone_publication_result_path(self, publication_key: str) -> Path:
        """Canonical Megaphone create result: ``manifests/megaphone_publications/<key>.json``."""
        return self._publication_key_path(
            publication_key,
            directory="megaphone_publications",
            suffix=".json",
        )

    def claim_path(self, publication_key: str) -> Path:
        """Once-path claim file: ``manifests/megaphone_once/<key_with_colons_as_underscores>.claim``."""
        return self._once_key_path(publication_key, suffix=".claim")

    def publication_claim_path(self, publication_key: str) -> Path:
        """Canonical publication claim: ``manifests/publication_claims/<key>.json``."""
        return self._publication_key_path(
            publication_key,
            directory="publication_claims",
            suffix=".json",
        )

    def _once_key_path(self, publication_key: str, *, suffix: str) -> Path:
        return self._publication_key_path(
            publication_key, directory="megaphone_once", suffix=suffix
        )

    def _publication_key_path(
        self, publication_key: str, *, directory: str, suffix: str
    ) -> Path:
        text = str(publication_key or "").strip()
        if not text:
            raise StoragePathError("publication_key is required")
        if ".." in text or _TRAVERSAL_RE.search(text):
            raise StoragePathError("publication_key must not contain path traversal")
        if "/" in text or "\\" in text:
            raise StoragePathError("publication_key must not contain path separators")
        safe = text.replace(":", "_")
        if not safe or safe in {".", ".."} or "/" in safe or "\\" in safe or ".." in safe:
            raise StoragePathError("publication_key produces an unsafe filename")
        dir_name = _reject_unsafe_segment(directory, label="directory")
        if not suffix.startswith("."):
            suffix = f".{suffix}"
        return _ensure_within_root(
            self.root, self.manifests_root / dir_name / f"{safe}{suffix}"
        )

    def published_episode_manifest(self, run_id: str, slug: str) -> Path:
        return self.run(run_id).published_manifest(slug)

    def edition_status_path(self, run_id: str, slug: str) -> Path:
        return self.run(run_id).edition_status(slug)

    def episode_mp3(self, run_id: str, slug: str) -> Path:
        return self.run(run_id).final_mp3(slug)

    def ensure_base_directories(self) -> None:
        """Explicit creation of common top-level dirs (does not create run trees)."""
        for path in (
            self.episodes_root,
            self.manifests_root,
            self.locks_root,
            self.runs_root,
            self.previews_root,
            self.reusable_audio_root,
            self.tts_cache_root,
            self.articles_root,
            self.logs_root,
            self.manifests_root / "selection",
            self.manifests_root / "runs",
            self.manifests_root / "status",
            self.manifests_root / "megaphone_once",
            self.manifests_root / "megaphone_publications",
            self.manifests_root / "publication_claims",
            self.manifests_root / "reports",
        ):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class RunPaths:
    """Per-run artifact paths (once-path + shared episode/status layout)."""

    storage: StoragePaths
    run_id: str

    @property
    def run_root(self) -> Path:
        return _ensure_within_root(self.storage.root, self.storage.runs_root / self.run_id)

    @property
    def selection_path(self) -> Path:
        """Once-path frozen selection: ``runs/<run_id>/selection.json``."""
        return _ensure_within_root(self.storage.root, self.run_root / "selection.json")

    @property
    def english_stories_path(self) -> Path:
        return _ensure_within_root(self.storage.root, self.run_root / "english_stories.json")

    @property
    def episode_manifest_path(self) -> Path:
        """Once-path assembled episode manifest (not the published feed manifest)."""
        return _ensure_within_root(self.storage.root, self.run_root / "episode_manifest.json")

    @property
    def megaphone_episode_path(self) -> Path:
        return _ensure_within_root(self.storage.root, self.run_root / "megaphone_episode.json")

    @property
    def state_path(self) -> Path:
        return _ensure_within_root(self.storage.root, self.run_root / "state.json")

    @property
    def report_path(self) -> Path:
        return _ensure_within_root(self.storage.root, self.run_root / "report.json")

    @property
    def events_path(self) -> Path:
        return _ensure_within_root(self.storage.root, self.run_root / "events.jsonl")

    def final_mp3(self, slug: str) -> Path:
        edition = validate_edition_slug(slug)
        return _ensure_within_root(
            self.storage.root,
            self.storage.episodes_root / self.run_id / f"{edition}.mp3",
        )

    def published_manifest(self, slug: str) -> Path:
        """Feed/API episode manifest: ``manifests/runs/<run_id>-<slug>.json``."""
        edition = validate_edition_slug(slug)
        return _ensure_within_root(
            self.storage.root,
            self.storage.manifests_root / "runs" / f"{self.run_id}-{edition}.json",
        )

    def edition_status(self, slug: str) -> Path:
        """Per-edition operational status: ``manifests/status/<run_id>-<slug>.json``."""
        edition = validate_edition_slug(slug)
        return _ensure_within_root(
            self.storage.root,
            self.storage.manifests_root / "status" / f"{self.run_id}-{edition}.json",
        )

    def edition(self, slug: str) -> EditionRunPaths:
        return EditionRunPaths(run=self, edition_slug=validate_edition_slug(slug))

    def ensure_run_directories(self) -> None:
        """Explicit mkdir for once-path run root and episode parent dirs."""
        self.run_root.mkdir(parents=True, exist_ok=True)
        (self.storage.episodes_root / self.run_id).mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class EditionRunPaths:
    """Edition-scoped views over the existing flat run layout (no new directories)."""

    run: RunPaths
    edition_slug: str

    @property
    def final_mp3_path(self) -> Path:
        return self.run.final_mp3(self.edition_slug)

    @property
    def published_manifest_path(self) -> Path:
        return self.run.published_manifest(self.edition_slug)

    @property
    def publication_state_path(self) -> Path:
        """Alias for per-edition status JSON (operational publication state)."""
        return self.run.edition_status(self.edition_slug)

    @property
    def selection_path(self) -> Path:
        """Once-path selection is shared across editions for a run."""
        return self.run.selection_path

    @property
    def transformed_path(self) -> Path:
        """English transformed stories artifact (shared; used by all editions)."""
        return self.run.english_stories_path


@dataclass(frozen=True)
class PreviewPaths:
    """Preview tree: ``previews/<run_id>/...`` (isolated from production manifests)."""

    storage: StoragePaths
    run_id: str

    @property
    def preview_root(self) -> Path:
        return _ensure_within_root(
            self.storage.root, self.storage.previews_root / self.run_id
        )

    @property
    def manifest_path(self) -> Path:
        return self.preview_root / "manifest.json"

    @property
    def summary_path(self) -> Path:
        return self.preview_root / "preview-summary.json"

    @property
    def logs_dir(self) -> Path:
        return self.preview_root / "logs"

    def script_path(self, language: str) -> Path:
        lang = _reject_unsafe_segment(language, label="language")
        return _ensure_within_root(self.storage.root, self.preview_root / "scripts" / f"{lang}.txt")

    def audio_path(self, language: str) -> Path:
        lang = _reject_unsafe_segment(language, label="language")
        return _ensure_within_root(
            self.storage.root, self.preview_root / "audio" / f"BalVoi60_{lang}.mp3"
        )

    def ensure_preview_directories(self) -> None:
        for sub in ("scripts", "audio", "metadata", "cache", "logs"):
            (self.preview_root / sub).mkdir(parents=True, exist_ok=True)


def get_storage_paths() -> StoragePaths:
    """Process-wide helper using current ``STORAGE_PATH`` / default."""
    return StoragePaths.from_env()
