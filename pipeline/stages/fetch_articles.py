"""Fetch articles from NewsGenie podcast_articles API."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from balvoi.paths import storage_root
from pipeline.lib.balvoi_api import fetch_podcast_articles

SITE_URL = os.environ.get("BALVOI_SITE_URL", "https://staging.balvoi.com")
ARTICLES_CACHE = storage_root() / "articles" / "latest.json"


def _demo_allowed() -> bool:
    return os.environ.get("BALVOI_ALLOW_DEMO_ARTICLES", "").strip().lower() == "true"


def _demo_articles() -> list[dict]:
    """Demo corpus for local testing when API is unavailable."""
    now = datetime.now(UTC).isoformat()
    samples = [
        (
            "Global markets react to central bank policy shift",
            "Financial markets worldwide saw significant movement today as investors parsed the latest signals from major central banks on interest rates and inflation outlook. Trading volumes surged across equities, bonds, and currency markets as portfolio managers repositioned ahead of expected policy announcements. Analysts noted that the reaction reflected both relief over clearer communication and caution about how long elevated rates may persist. Regional indices posted mixed results, with technology and energy sectors leading gains while real estate and utilities faced pressure. Economists said the next several data releases on employment and consumer spending will likely determine whether central banks pause or continue tightening.",
            True,
        ),
        (
            "Tech leaders announce new AI transparency initiative",
            "A coalition of technology companies unveiled a framework aimed at improving transparency and accountability in artificial intelligence systems used for public information. The group said the standards would require clearer labeling of AI-generated content, independent auditing of high-risk models, and public reporting on error rates and bias testing. Supporters argued the move could help rebuild trust amid growing concern over synthetic media and automated news summaries. Critics cautioned that voluntary frameworks may not be enforceable without government regulation. Several major platforms committed to pilot programs over the next six months.",
            False,
        ),
        (
            "Climate summit delegates reach preliminary agreement",
            "Representatives from dozens of nations agreed on a preliminary framework to accelerate emissions reporting and renewable energy investment targets. The deal includes new verification mechanisms and funding commitments for developing nations transitioning away from coal. Environmental groups welcomed progress but said the timeline remains too slow to meet global temperature goals. Energy companies highlighted opportunities in grid modernization and storage. Officials expect a final vote after additional negotiations on enforcement provisions.",
            False,
        ),
        (
            "Regional health officials monitor emerging virus strain",
            "Health authorities are tracking a new viral variant and urging preparedness while emphasizing that current risk levels remain manageable for the general public. Hospitals have been asked to update testing protocols and ensure adequate supplies of antiviral treatments. Epidemiologists said early data suggest existing vaccines still provide meaningful protection against severe illness. Travel advisories remain unchanged, though officials recommend staying current on vaccinations. Public health messaging focuses on symptoms to watch and when to seek care.",
            True,
        ),
        (
            "Major infrastructure bill advances in legislative session",
            "Lawmakers moved closer to approving a comprehensive infrastructure package focused on transportation, broadband, and energy grid modernization. The bill allocates funding for bridge repairs, rural internet expansion, and electric vehicle charging networks. Supporters said the investment would create jobs and improve competitiveness. Some legislators raised concerns about oversight and long-term maintenance costs. A final vote is expected after budget scoring is completed.",
            False,
        ),
        (
            "Sports championship draws record global viewership",
            "The latest championship event attracted one of the largest global audiences in recent years, driven by strong international interest and digital streaming. Broadcast partners reported significant growth among younger viewers accessing coverage on mobile devices. Athletes credited expanded qualifying formats with raising competition quality. Sponsors announced renewed partnerships citing brand exposure metrics. Organizers said they plan to expand future events to additional markets.",
            False,
        ),
        (
            "Diplomatic talks resume amid heightened regional tensions",
            "Senior diplomats reconvened for a new round of negotiations aimed at reducing tensions and restoring communication channels between key regional powers. Mediators described the initial sessions as frank but constructive. Security analysts warned that unresolved disputes over trade routes and military exercises could derail progress. Humanitarian groups urged negotiators to prioritize civilian safety. Talks are scheduled to continue through the week.",
            True,
        ),
        (
            "Consumer confidence index shows mixed economic signals",
            "The latest consumer confidence survey revealed a split between optimism about employment and caution about rising costs in essential categories. Retailers reported strong spending on services while discretionary purchases softened. Economists said the data suggest a gradual cooling rather than a sharp downturn. Housing market indicators remained stable in most metropolitan areas. Policymakers are watching upcoming inflation reports closely.",
            False,
        ),
        (
            "Space agency previews next-generation satellite mission",
            "Officials outlined plans for a satellite program designed to improve climate monitoring, disaster response, and global communications resilience. The mission will deploy advanced sensors capable of tracking atmospheric changes with greater precision. International partners contributed components and launch capacity. Researchers said the data could improve hurricane forecasting and drought detection. Launch is targeted for next year pending final testing.",
            False,
        ),
        (
            "Cybersecurity agencies warn of coordinated phishing campaign",
            "National cybersecurity agencies issued a joint alert about a coordinated phishing campaign targeting government and media organizations. Investigators said attackers used spoofed login pages and compromised email accounts to gather credentials. Agencies recommended multi-factor authentication and enhanced monitoring. Several organizations confirmed they disrupted attempted intrusions. Experts said the campaign appears designed to steal information rather than disrupt services.",
            True,
        ),
        (
            "Housing affordability remains focus in major cities",
            "City leaders and housing advocates convened to discuss rising rents and limited supply in major urban centers. Proposals include zoning reforms, incentives for affordable units, and streamlined permitting for new construction. Developers cited material costs and interest rates as ongoing challenges. Tenant groups pushed for stronger protections against sudden rent increases. Officials promised a coordinated response plan within sixty days.",
            False,
        ),
        (
            "Renewable energy output hits seasonal record",
            "Grid operators reported that renewable energy sources accounted for a record share of electricity generation during the latest measurement period. Solar and wind installations benefited from favorable weather conditions and expanded capacity. Energy analysts said storage systems played a critical role in maintaining stability. Fossil fuel generation declined proportionally though remained available for backup. Industry groups called for continued investment in transmission infrastructure.",
            False,
        ),
    ]
    return [
        {
            "id": f"fallback-{i}",
            "slug": f"fallback-story-{i}",
            "title": title,
            "url": f"{SITE_URL}/story/fallback-{i}",
            "fullText": body,
            "summary": body[:200],
            "publishDate": now,
            "publishTimestamp": datetime.now(UTC).timestamp(),
            "breaking": breaking,
            "category": "World",
            "tags": ["breaking"] if breaking else [],
            "country": "",
            "source": "DEMO",
        }
        for i, (title, body, breaking) in enumerate(samples, 1)
    ]


def fetch_articles(
    window_start: datetime | None = None,
    window_end_exclusive: datetime | None = None,
    *,
    cache_path: os.PathLike[str] | str | None = ARTICLES_CACHE,
) -> list[dict]:
    """Return normalized articles from NewsGenie podcast_articles API."""
    articles = fetch_podcast_articles(window_start, window_end_exclusive)
    if not articles and _demo_allowed():
        print("  [info] API empty — using demo articles (BALVOI_ALLOW_DEMO_ARTICLES=true)")
        articles = _demo_articles()

    seen: set[str] = set()
    unique: list[dict] = []
    for a in sorted(articles, key=lambda x: x.get("publishTimestamp", 0), reverse=True):
        if a["id"] in seen:
            continue
        seen.add(a["id"])
        unique.append(a)

    if cache_path is not None:
        resolved_cache = Path(cache_path)
        resolved_cache.parent.mkdir(parents=True, exist_ok=True)
        resolved_cache.write_text(
            json.dumps(unique, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    print(f"  [fetch] {len(unique)} articles available")
    return unique
