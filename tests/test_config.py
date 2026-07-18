from __future__ import annotations

from balvoi import config


def test_editions_loads_all_slugs() -> None:
    slugs = {e.get("slug") for e in config.editions()}
    assert slugs == {"en", "es", "pt", "fr", "de", "ar", "ru", "tr"}


def test_edition_by_slug_known() -> None:
    edition = config.edition_by_slug("en")
    assert edition is not None
    assert edition["id"] == "balvoi60-en"
    assert edition["editionName"] == "Five Eyes Edition"


def test_edition_by_slug_unknown() -> None:
    assert config.edition_by_slug("xx") is None
    assert config.edition_by_slug("") is None


def test_master_brand_defaults() -> None:
    brand = config.master_brand()
    assert brand["name"] == "BalVoi:60"
    assert brand["tagline"]
    assert brand["subtitle"]


def test_is_english() -> None:
    assert config.is_english("English")
    assert config.is_english("en-US")
    assert not config.is_english("Spanish")
    assert not config.is_english("")


def test_config_loader_reexports() -> None:
    from pipeline.config_loader import edition_by_slug, editions

    assert len(editions()) == 8
    assert edition_by_slug("fr")["slug"] == "fr"


def test_server_data_reexports() -> None:
    from server import data

    assert len(data.editions()) == 8
    assert data.edition_by_slug("de")["slug"] == "de"
    assert data.master_brand()["name"] == "BalVoi:60"
