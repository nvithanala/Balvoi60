"""Canonical publication identity (T-M1-003).

Formats (frozen by T-M1-001 characterization):

- ``run_id`` / ``boundary_key`` (production default):
  ``boundary.strftime("%Y-%m-%dT%H-%M-%SZ")``
  which equals ``format_iso_utc(boundary).replace(":", "-")`` for UTC boundaries.
- ``publication_key`` / Megaphone ``externalId``:
  ``balvoi60:{slug}:{YYYY-MM-DDTHH:MM:SSZ}``
- Legacy publication key (read-only compatibility):
  ``balvoi60:{YYYY-MM-DDTHH:MM:SSZ}:{slug}``

Production rule: default ``run_id`` equals ``boundary_key`` unless an explicit
override (CLI ``--run-id``) or preview prefix is used. Preview identities are
out of scope for this module.

Precedence for identity values: derived only from ``boundary`` + ``edition_slug``
(+ optional explicit ``run_id`` override). Never from local timezone or process
start time.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime

from balvoi.dates import format_iso_utc

ALLOWED_EDITION_SLUGS = frozenset({"en", "es", "pt", "fr", "de", "ar", "ru", "tr"})

_CANONICAL_KEY_RE = re.compile(
    r"^balvoi60:(?P<slug>[a-z]{2}):(?P<iso>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)$"
)
_LEGACY_KEY_RE = re.compile(
    r"^balvoi60:(?P<iso>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z):(?P<slug>[a-z]{2})$"
)
_RUN_ID_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<h>\d{2})-(?P<m>\d{2})-(?P<s>\d{2})Z$"
)


class PublicationIdentityError(ValueError):
    """Invalid publication identity input."""


def require_utc_boundary(value: datetime) -> datetime:
    """Return an aware UTC datetime; reject naive values."""
    if not isinstance(value, datetime):
        raise PublicationIdentityError("boundary must be a datetime")
    if value.tzinfo is None:
        raise PublicationIdentityError(
            "boundary must be timezone-aware UTC (naive datetimes are rejected)"
        )
    return value.astimezone(UTC)


def normalize_edition_slug(slug: str) -> str:
    """Strip + lowercase; reject unknown edition slugs."""
    if slug is None:
        raise PublicationIdentityError("edition slug is required")
    normalized = str(slug).strip().lower()
    if not normalized:
        raise PublicationIdentityError("edition slug is required")
    if normalized not in ALLOWED_EDITION_SLUGS:
        raise PublicationIdentityError(
            f"invalid edition slug {slug!r}; expected one of: "
            + ", ".join(sorted(ALLOWED_EDITION_SLUGS))
        )
    return normalized


def canonical_run_id(boundary: datetime) -> str:
    """One boundary → one canonical production ``run_id`` / ``boundary_key``."""
    utc = require_utc_boundary(boundary)
    return utc.strftime("%Y-%m-%dT%H-%M-%SZ")


def boundary_key(boundary: datetime) -> str:
    """Filesystem-safe boundary key (identical to canonical ``run_id`` for UTC)."""
    return format_iso_utc(require_utc_boundary(boundary)).replace(":", "-")


def make_publication_key(slug: str, boundary: datetime) -> str:
    """Canonical ``balvoi60:{slug}:{iso_utc}`` (also Megaphone ``externalId``)."""
    return f"balvoi60:{normalize_edition_slug(slug)}:{format_iso_utc(require_utc_boundary(boundary))}"


def legacy_publication_key(slug: str, boundary: datetime) -> str:
    """Legacy key order for **reading** prior records only — do not emit for new runs."""
    return (
        f"balvoi60:{format_iso_utc(require_utc_boundary(boundary))}:"
        f"{normalize_edition_slug(slug)}"
    )


def is_canonical_publication_key(value: str | None) -> bool:
    return bool(value and _CANONICAL_KEY_RE.match(str(value).strip()))


def is_legacy_publication_key(value: str | None) -> bool:
    return bool(value and _LEGACY_KEY_RE.match(str(value).strip()))


def parse_run_id(run_id: str) -> datetime:
    """Parse a canonical production ``run_id`` / ``boundary_key`` to UTC boundary."""
    text = (run_id or "").strip()
    match = _RUN_ID_RE.match(text)
    if not match:
        raise PublicationIdentityError(f"invalid run_id {run_id!r}")
    iso = f"{match.group('date')}T{match.group('h')}:{match.group('m')}:{match.group('s')}Z"
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(UTC)


def parse_publication_key(value: str) -> tuple[str, datetime, bool]:
    """Parse a publication key.

    Returns ``(slug, boundary, is_legacy)``. Emits ``DeprecationWarning`` when legacy.
    """
    text = (value or "").strip()
    canonical = _CANONICAL_KEY_RE.match(text)
    if canonical:
        slug = canonical.group("slug")
        boundary = datetime.fromisoformat(
            canonical.group("iso").replace("Z", "+00:00")
        ).astimezone(UTC)
        return slug, boundary, False
    legacy = _LEGACY_KEY_RE.match(text)
    if legacy:
        warnings.warn(
            "legacy publication key format read; prefer balvoi60:{slug}:{iso}",
            DeprecationWarning,
            stacklevel=2,
        )
        slug = legacy.group("slug")
        boundary = datetime.fromisoformat(
            legacy.group("iso").replace("Z", "+00:00")
        ).astimezone(UTC)
        return slug, boundary, True
    raise PublicationIdentityError(f"invalid publication key {value!r}")


def normalize_stored_publication_key(
    value: str | None,
    *,
    slug: str,
    boundary: datetime,
) -> tuple[str, bool]:
    """Return ``(canonical_key, migrated_from_legacy)``.

    Does not rewrite files; callers decide whether to persist the canonical form.
    Emits a deprecation warning when a legacy key is recognized.
    """
    canonical = make_publication_key(slug, boundary)
    if not value:
        return canonical, False
    text = str(value).strip()
    if text == canonical:
        return canonical, False
    expected_legacy = legacy_publication_key(slug, boundary)
    if text == expected_legacy:
        warnings.warn(
            "legacy publication key format read; prefer balvoi60:{slug}:{iso}",
            DeprecationWarning,
            stacklevel=2,
        )
        return canonical, True
    # Unknown stored value: still prefer canonical for this boundary/slug.
    return canonical, False


@dataclass(frozen=True)
class PublicationIdentity:
    """Immutable publication identity for one boundary + edition."""

    boundary: datetime
    run_id: str
    boundary_key: str
    edition_slug: str
    publication_key: str
    external_id: str

    @classmethod
    def from_boundary(
        cls,
        boundary: datetime,
        edition_slug: str,
        *,
        run_id: str | None = None,
    ) -> PublicationIdentity:
        """Build identity from boundary + slug.

        Optional ``run_id`` override is retained for CLI/resume compatibility
        (I1 PARTIAL); ``boundary_key`` and publication keys still derive from
        ``boundary`` + slug only.
        """
        utc_boundary = require_utc_boundary(boundary)
        slug = normalize_edition_slug(edition_slug)
        canonical = canonical_run_id(utc_boundary)
        # boundary_key always tracks the boundary (lock/selection paths).
        bkey = boundary_key(utc_boundary)
        assert bkey == canonical
        override = (run_id or "").strip()
        rid = override or canonical
        key = make_publication_key(slug, utc_boundary)
        return cls(
            boundary=utc_boundary,
            run_id=rid,
            boundary_key=bkey,
            edition_slug=slug,
            publication_key=key,
            external_id=key,
        )

    @classmethod
    def from_existing(
        cls,
        *,
        boundary: datetime,
        edition_slug: str,
        run_id: str,
    ) -> PublicationIdentity:
        """Reuse a persisted ``run_id`` (retry/resume) without generating a new one."""
        text = (run_id or "").strip()
        if not text:
            raise PublicationIdentityError("run_id is required for resume/retry")
        return cls.from_boundary(boundary, edition_slug, run_id=text)


# Compatibility aliases matching historical call sites.
def publication_key(slug: str, boundary: datetime) -> str:
    """Canonical publication key (also Megaphone ``externalId``)."""
    return make_publication_key(slug, boundary)
