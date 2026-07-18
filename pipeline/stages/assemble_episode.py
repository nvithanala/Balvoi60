"""Build ordered segment manifest for one edition."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from pipeline.config_loader import assets, episode_template, get_voice_for_edition, segments
from pipeline.lib.asset_picker import pick_variant

SEGMENT_KEY_MAP = {
    "transition_in": "started",
    "ad_transition_out_1": "right_back",
    "ad_transition_out_2": "right_back",
    "return_to_news_1": "welcome_back",
    "return_to_news_2": "welcome_back",
    "ad_1": "ad_1",
    "ad_2": "ad_2",
    "outro": "thank_you",
}


def _dynamic_intro_text(
    edition: dict,
    voice: dict,
    when: datetime | None = None,
) -> str:
    tz = ZoneInfo(edition["timezone"])
    local = (when or datetime.now(tz)).astimezone(tz)
    tmpl = episode_template()["introTemplate"]["dynamicSuffix"]
    return tmpl.format(
        time=local.strftime("%I:%M %p").lstrip("0"),
        city=edition["city"],
        day=local.strftime("%d").lstrip("0"),
        month=local.strftime("%B"),
        year=local.strftime("%Y"),
        anchorName=voice["name"],
        editionName=edition["editionName"],
    )


def _split_stories(stories: list[dict], blocks: int = 3) -> list[list[dict]]:
    if not stories:
        return [[] for _ in range(blocks)]
    per = max(1, len(stories) // blocks)
    chunks = []
    idx = 0
    for b in range(blocks):
        if b == blocks - 1:
            chunks.append(stories[idx:])
        else:
            chunks.append(stories[idx : idx + per])
            idx += per
    return chunks


def assemble_episode(
    edition: dict,
    stories: list[dict],
    run_id: str,
    headlines_text: str | None = None,
    *,
    when: datetime | None = None,
) -> dict:
    slug = edition["slug"]
    voice = get_voice_for_edition(edition, when=when)
    edition_assets = assets()[edition["id"]]
    segs = segments()
    story_blocks = _split_stories(stories, 3)

    manifest_segments: list[dict] = []
    picks: dict[str, int] = {}

    def add_tts(
        seg_type: str,
        text: str,
        sheet: str | None = None,
        variant: int | None = None,
        *,
        reusable: bool = False,
    ):
        entry: dict = {
            "type": "tts",
            "segmentType": seg_type,
            "text": text,
            "sheet": sheet,
            "variant": variant,
        }
        if reusable:
            entry["reusable"] = True
        manifest_segments.append(entry)

    def add_audio(seg_type: str, path: str, sheet: str, variant: int):
        manifest_segments.append(
            {
                "type": "audio",
                "segmentType": seg_type,
                "path": path,
                "sheet": sheet,
                "variant": variant,
            }
        )

    welcome = segs["welcome"][slug][0] if segs["welcome"].get(slug) else ""
    if welcome.strip():
        add_tts(
            "intro_welcome",
            welcome.strip(),
            "welcome",
            0,
            reusable=True,
        )
    dynamic = _dynamic_intro_text(edition, voice, when=when)
    if dynamic.strip():
        # Time/city/anchor — never reusable-cached.
        add_tts("intro_dynamic", dynamic.strip(), "welcome", 0)

    headlines = headlines_text or " ".join(s.get("primer", "") for s in stories[:10])
    if headlines.strip():
        add_tts("headlines", headlines.strip(), "primers", None)

    block_idx = 0
    for step in episode_template()["segmentOrder"]:
        stype = step["type"]

        if stype == "intro" or stype == "headlines":
            continue

        if stype.startswith("story_segment"):
            for story in story_blocks[block_idx]:
                add_tts("story", story["broadcastScript"], "article", None)
            block_idx += 1
            continue

        key = SEGMENT_KEY_MAP.get(stype)
        if not key:
            continue

        if key in ("ad_1", "ad_2", "right_back"):
            pool = edition_assets.get(key, [])
            if pool:
                variant, path = pick_variant(pool, run_id, f"{slug}:{stype}")
                picks[f"{stype}"] = variant
                add_audio(stype, path, key, variant)
            elif key == "right_back" and segs["right_back"].get(slug):
                # Localized TTS fallback only when no prerecorded asset is configured.
                variant, text = pick_variant(segs["right_back"][slug], run_id, f"{slug}:{stype}")
                picks[f"{stype}"] = variant
                add_tts(
                    stype,
                    text,
                    key,
                    variant,
                    reusable=True,
                )
            continue

        pool = segs.get(key, {}).get(slug, [])
        if pool:
            variant, text = pick_variant(pool, run_id, f"{slug}:{stype}")
            picks[f"{stype}"] = variant
            add_tts(
                stype,
                text,
                key,
                variant,
                reusable=True,
            )

    return {
        "editionId": edition["id"],
        "slug": slug,
        "language": edition.get("language", slug),
        "voice": voice,
        "segments": manifest_segments,
        "picks": picks,
        "storyIds": [s["id"] for s in stories],
    }
