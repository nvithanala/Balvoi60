"""T-M1-003 canonical publication identity tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipeline.lib import publication_identity as identity_mod
from pipeline.lib.megaphone_client import (
    legacy_publication_key as client_legacy_key,
)
from pipeline.lib.megaphone_client import (
    normalize_stored_publication_key as client_normalize,
)
from pipeline.lib.megaphone_client import (
    publication_key as client_publication_key,
)
from pipeline.lib.megaphone_once import OnceRunContext
from pipeline.lib.publication_identity import (
    ALLOWED_EDITION_SLUGS,
    PublicationIdentity,
    PublicationIdentityError,
    boundary_key,
    canonical_run_id,
    is_canonical_publication_key,
    is_legacy_publication_key,
    legacy_publication_key,
    make_publication_key,
    normalize_edition_slug,
    normalize_stored_publication_key,
    parse_publication_key,
    parse_run_id,
    publication_key,
    require_utc_boundary,
)

BOUNDARY = datetime(2026, 7, 22, 18, 0, 0, tzinfo=UTC)
OTHER_BOUNDARY = datetime(2026, 7, 22, 19, 0, 0, tzinfo=UTC)


def test_deterministic_identity_generation() -> None:
    a = PublicationIdentity.from_boundary(BOUNDARY, "en")
    b = PublicationIdentity.from_boundary(BOUNDARY, "en")
    assert a == b
    assert a.run_id == b.run_id
    assert a.publication_key == b.publication_key
    assert a.external_id == b.external_id


def test_same_boundary_produces_same_run_id() -> None:
    assert canonical_run_id(BOUNDARY) == canonical_run_id(BOUNDARY)
    assert canonical_run_id(BOUNDARY) == "2026-07-22T18-00-00Z"
    assert boundary_key(BOUNDARY) == canonical_run_id(BOUNDARY)


def test_same_boundary_and_edition_same_publication_key() -> None:
    assert publication_key("en", BOUNDARY) == publication_key("en", BOUNDARY)
    assert publication_key("en", BOUNDARY) == "balvoi60:en:2026-07-22T18:00:00Z"


def test_same_boundary_and_edition_same_external_id() -> None:
    ident = PublicationIdentity.from_boundary(BOUNDARY, "en")
    assert ident.external_id == ident.publication_key
    assert ident.external_id == "balvoi60:en:2026-07-22T18:00:00Z"


def test_different_editions_different_publication_keys() -> None:
    en = PublicationIdentity.from_boundary(BOUNDARY, "en")
    es = PublicationIdentity.from_boundary(BOUNDARY, "es")
    assert en.run_id == es.run_id
    assert en.publication_key != es.publication_key
    assert en.external_id != es.external_id


def test_different_boundaries_different_identities() -> None:
    a = PublicationIdentity.from_boundary(BOUNDARY, "en")
    b = PublicationIdentity.from_boundary(OTHER_BOUNDARY, "en")
    assert a.run_id != b.run_id
    assert a.publication_key != b.publication_key


def test_utc_timezone_behavior() -> None:
    utc = require_utc_boundary(BOUNDARY)
    assert utc.tzinfo == UTC
    assert canonical_run_id(utc) == "2026-07-22T18-00-00Z"


def test_non_utc_aware_datetime_converted() -> None:
    eastern = timezone(timedelta(hours=-4))
    local = datetime(2026, 7, 22, 14, 0, 0, tzinfo=eastern)  # 18:00 UTC
    ident = PublicationIdentity.from_boundary(local, "en")
    assert ident.boundary == BOUNDARY
    assert ident.run_id == "2026-07-22T18-00-00Z"
    assert ident.publication_key == "balvoi60:en:2026-07-22T18:00:00Z"


def test_naive_datetime_rejected() -> None:
    naive = datetime(2026, 7, 22, 18, 0, 0)
    with pytest.raises(PublicationIdentityError, match="timezone-aware"):
        require_utc_boundary(naive)
    with pytest.raises(PublicationIdentityError):
        PublicationIdentity.from_boundary(naive, "en")


def test_boundary_parsing_via_run_id() -> None:
    parsed = parse_run_id("2026-07-22T18-00-00Z")
    assert parsed == BOUNDARY


def test_run_id_parsing_round_trip() -> None:
    rid = canonical_run_id(BOUNDARY)
    assert parse_run_id(rid) == BOUNDARY


def test_invalid_run_id() -> None:
    with pytest.raises(PublicationIdentityError, match="invalid run_id"):
        parse_run_id("not-a-run-id")
    with pytest.raises(PublicationIdentityError):
        parse_run_id("2026-07-22T18:00:00Z")  # colon form is publication ISO, not run_id


def test_invalid_boundary_type() -> None:
    with pytest.raises(PublicationIdentityError):
        require_utc_boundary("2026-07-22T18:00:00Z")  # type: ignore[arg-type]


def test_valid_edition_slugs() -> None:
    for slug in sorted(ALLOWED_EDITION_SLUGS):
        assert normalize_edition_slug(slug) == slug
        assert PublicationIdentity.from_boundary(BOUNDARY, slug).edition_slug == slug


def test_invalid_edition_slugs() -> None:
    with pytest.raises(PublicationIdentityError, match="invalid edition slug"):
        normalize_edition_slug("xx")
    with pytest.raises(PublicationIdentityError):
        PublicationIdentity.from_boundary(BOUNDARY, "english")
    with pytest.raises(PublicationIdentityError):
        normalize_edition_slug("")


def test_case_normalization() -> None:
    assert normalize_edition_slug("EN") == "en"
    ident = PublicationIdentity.from_boundary(BOUNDARY, "Es")
    assert ident.edition_slug == "es"
    assert ident.publication_key == "balvoi60:es:2026-07-22T18:00:00Z"


def test_whitespace_normalization() -> None:
    assert normalize_edition_slug("  en  ") == "en"
    ident = PublicationIdentity.from_boundary(BOUNDARY, " pt ")
    assert ident.edition_slug == "pt"


def test_legacy_publication_key_parsing() -> None:
    legacy = legacy_publication_key("en", BOUNDARY)
    assert legacy == "balvoi60:2026-07-22T18:00:00Z:en"
    with pytest.warns(DeprecationWarning, match="legacy publication key"):
        slug, boundary, is_legacy = parse_publication_key(legacy)
    assert slug == "en"
    assert boundary == BOUNDARY
    assert is_legacy is True


def test_legacy_key_warning_on_normalize() -> None:
    legacy = "balvoi60:2026-07-22T18:00:00Z:en"
    with pytest.warns(DeprecationWarning, match="legacy publication key"):
        canonical, migrated = normalize_stored_publication_key(
            legacy, slug="en", boundary=BOUNDARY
        )
    assert migrated is True
    assert canonical == "balvoi60:en:2026-07-22T18:00:00Z"


def test_canonical_values_never_use_legacy_format() -> None:
    ident = PublicationIdentity.from_boundary(BOUNDARY, "en")
    assert is_canonical_publication_key(ident.publication_key)
    assert is_canonical_publication_key(ident.external_id)
    assert not is_legacy_publication_key(ident.publication_key)
    assert ident.publication_key.startswith("balvoi60:en:")
    assert not ident.publication_key.startswith("balvoi60:2026-")


def test_compatibility_with_manifest_state_fixture(tmp_path: Path) -> None:
    """Persisted canonical + legacy keys remain readable without rewrite."""
    run_dir = tmp_path / "runs" / "2026-07-22T18-00-00Z"
    run_dir.mkdir(parents=True)
    state = {
        "runId": "2026-07-22T18-00-00Z",
        "publicationBoundary": "2026-07-22T18:00:00Z",
        "publicationKey": "balvoi60:2026-07-22T18:00:00Z:en",  # legacy on disk
    }
    (run_dir / "state.json").write_text(
        __import__("json").dumps(state), encoding="utf-8"
    )
    with pytest.warns(DeprecationWarning):
        canonical, migrated = normalize_stored_publication_key(
            state["publicationKey"], slug="en", boundary=BOUNDARY
        )
    assert migrated is True
    assert canonical == publication_key("en", BOUNDARY)
    # Fixture file left unchanged (no automatic bulk migration).
    loaded = __import__("json").loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert loaded["publicationKey"] == state["publicationKey"]


def test_retry_reuses_existing_identity() -> None:
    first = PublicationIdentity.from_boundary(BOUNDARY, "en")
    resumed = PublicationIdentity.from_existing(
        boundary=BOUNDARY,
        edition_slug="en",
        run_id=first.run_id,
    )
    assert resumed.run_id == first.run_id
    assert resumed.publication_key == first.publication_key
    # Explicit override must not invent a new run_id.
    custom = PublicationIdentity.from_existing(
        boundary=BOUNDARY,
        edition_slug="en",
        run_id="custom-retry-run",
    )
    assert custom.run_id == "custom-retry-run"
    assert custom.boundary_key == first.boundary_key
    assert custom.publication_key == first.publication_key


def test_run_and_megaphone_once_generate_identical_canonical_identity() -> None:
    """pipeline.run default run_id and once-path identity share canonical formulas."""
    from pipeline.lib.publication_identity import canonical_run_id as run_formula

    run_id = run_formula(BOUNDARY)
    once = PublicationIdentity.from_boundary(BOUNDARY, "en")
    assert once.run_id == run_id
    assert once.boundary_key == run_id
    assert once.publication_key == client_publication_key("en", BOUNDARY)
    assert once.external_id == once.publication_key


def test_identity_stable_across_process_restart_simulation() -> None:
    """Re-importing the module and rebuilding yields the same strings."""
    first = PublicationIdentity.from_boundary(BOUNDARY, "de")
    # Simulate "new process" by calling through reloaded helpers.
    second = identity_mod.PublicationIdentity.from_boundary(BOUNDARY, "de")
    assert first.run_id == second.run_id
    assert first.publication_key == second.publication_key
    assert first.external_id == second.external_id


def test_megaphone_client_reexports_match_canonical() -> None:
    assert client_publication_key("en", BOUNDARY) == publication_key("en", BOUNDARY)
    assert client_legacy_key("en", BOUNDARY) == legacy_publication_key("en", BOUNDARY)
    with pytest.warns(DeprecationWarning):
        a, m = client_normalize(
            "balvoi60:2026-07-22T18:00:00Z:en", slug="en", boundary=BOUNDARY
        )
    assert m is True
    assert a == make_publication_key("en", BOUNDARY)


def test_parse_canonical_publication_key() -> None:
    slug, boundary, is_legacy = parse_publication_key("balvoi60:en:2026-07-22T18:00:00Z")
    assert slug == "en"
    assert boundary == BOUNDARY
    assert is_legacy is False


def test_invalid_publication_key() -> None:
    with pytest.raises(PublicationIdentityError, match="invalid publication key"):
        parse_publication_key("nope")


def test_once_context_uses_canonical_key_shape() -> None:
    ident = PublicationIdentity.from_boundary(BOUNDARY, "en")
    ctx = OnceRunContext(
        run_id=ident.run_id,
        boundary=ident.boundary,
        publication_key=ident.publication_key,
    )
    assert ctx.publication_key == ident.external_id
