"""Select one ordered story set for every language in an hourly publication."""

from __future__ import annotations

from datetime import UTC, datetime

from balvoi.countries import article_countries, matches_edition
from balvoi.dates import article_publish_timestamp


def _decision_row(
    article: dict,
    *,
    matched_country: str | None,
    status: str,
    reason: str | None,
) -> dict:
    return {
        "id": str(article.get("id")),
        "title": article.get("title"),
        "category": article.get("category"),
        "countries": article_countries(article),
        "matchedCountry": matched_country,
        "publishTimestamp": article_publish_timestamp(article),
        "breaking": bool(article.get("breaking")),
        "status": status,
        "reason": reason,
    }


def _scope_by_country(
    articles: list[dict],
    source_countries: list[str] | None,
) -> tuple[list[dict], dict[str, str | None], dict[str, str | None]]:
    """Return (scoped pool, matched_country_by_id, country_reason_by_id)."""
    matched_by_id: dict[str, str | None] = {}
    reason_by_id: dict[str, str | None] = {}

    if not source_countries:
        for article in articles:
            matched_by_id[str(article["id"])] = None
        return articles, matched_by_id, reason_by_id

    any_tagged = any(article_countries(a) for a in articles)
    if not any_tagged:
        print("  [select] no country tags in pool — skipping country filter")
        for article in articles:
            matched_by_id[str(article["id"])] = None
        return articles, matched_by_id, reason_by_id

    scoped: list[dict] = []
    for article in articles:
        article_id = str(article["id"])
        matched, key = matches_edition(article, source_countries)
        if matched:
            matched_by_id[article_id] = key
            scoped.append(article)
        else:
            matched_by_id[article_id] = None
            reason_by_id[article_id] = (
                "no_country_tag" if not article_countries(article) else "out_of_country"
            )

    dropped = len(articles) - len(scoped)
    if dropped:
        print(f"  [select] {dropped} stories outside edition countries")
    return scoped, matched_by_id, reason_by_id


def select_stories(
    articles: list[dict],
    edition_id: str,
    since_minutes: int = 60,
    exclude_ids: set[str] | None = None,
    source_countries: list[str] | None = None,
    record: list | None = None,
    *,
    window_start: datetime | None = None,
    window_end_exclusive: datetime | None = None,
) -> list[dict]:
    if not articles:
        return []

    exclude_ids = exclude_ids or set()
    country_scoped, matched_by_id, country_reason_by_id = _scope_by_country(
        articles, source_countries
    )

    exclusion_reason: dict[str, str | None] = dict(country_reason_by_id)
    selected_ids: set[str] = set()

    del edition_id
    now = datetime.now(UTC)
    start_ts = (
        window_start.astimezone(UTC).timestamp()
        if window_start
        else now.timestamp() - since_minutes * 60
    )
    end_ts = (
        window_end_exclusive.astimezone(UTC).timestamp()
        if window_end_exclusive
        else now.timestamp()
    )

    fresh = [a for a in country_scoped if str(a.get("id")) not in exclude_ids]
    if exclude_ids:
        dropped = len(country_scoped) - len(fresh)
        if dropped:
            print(f"  [select] skipping {dropped} stories already aired recently")
            for article in country_scoped:
                article_id = str(article["id"])
                if article_id in exclude_ids and article_id not in exclusion_reason:
                    exclusion_reason[article_id] = "cooldown"

    in_window = [a for a in fresh if start_ts <= article_publish_timestamp(a) < end_ts]
    pool = in_window
    in_window_ids = {str(a["id"]) for a in in_window}
    for article in fresh:
        article_id = str(article["id"])
        if article_id not in in_window_ids and article_id not in exclusion_reason:
            exclusion_reason[article_id] = "out_of_window"

    breaking = [a for a in pool if a.get("breaking")]
    non_breaking = [a for a in pool if not a.get("breaking")]

    breaking.sort(key=article_publish_timestamp, reverse=True)
    non_breaking.sort(key=article_publish_timestamp, reverse=True)

    if breaking:
        ranked = breaking + [a for a in non_breaking if a["id"] not in {b["id"] for b in breaking}]
        print(f"  [select] {len(breaking)} breaking + {len(non_breaking)} other in pool")
    else:
        ranked = non_breaking
        print(f"  [select] no breaking news — using {len(ranked)} stories in ownership window")

    selected = []
    for article in ranked:
        article_id = str(article["id"])
        if article_id in selected_ids:
            continue
        selected.append(article)
        selected_ids.add(article_id)
    print(f"  [select] {len(selected)} unique stories selected")

    if record is not None:
        for article in articles:
            article_id = str(article["id"])
            reason = exclusion_reason.get(article_id)
            status = "selected" if article_id in selected_ids else "excluded"
            record.append(
                _decision_row(
                    article,
                    matched_country=matched_by_id.get(article_id),
                    status=status,
                    reason=reason,
                )
            )

    return selected
