"""Episode duration budget math — target ~30 min including ads and fillers."""

from __future__ import annotations

from pathlib import Path

from balvoi.paths import ROOT
from pipeline.config_loader import assets

# Hard cap: episode must stay below 30 minutes
MAX_EPISODE_SECONDS = 30 * 60
# Target slightly under cap to absorb TTS variance
TARGET_EPISODE_SECONDS = 28 * 60
WORDS_PER_MINUTE = 150
SECONDS_PER_STORY_TARGET = 150  # ~2.5 min broadcast read per story
STORY_BLOCKS = 3


def _mp3_duration_estimate(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        from pipeline.stages.merge_audio import duration_seconds
        d = duration_seconds(path)
        if d > 0:
            return d
    except Exception:
        pass
    return max(1, path.stat().st_size // 16000)


def average_asset_duration(edition_id: str, asset_key: str) -> int:
    pool = assets().get(edition_id, {}).get(asset_key, [])
    if not pool:
        defaults = {"ad_1": 35, "ad_2": 50, "right_back": 5}
        return defaults.get(asset_key, 5)
    durations = [_mp3_duration_estimate(ROOT / p) for p in pool]
    return int(sum(durations) / len(durations))


def fixed_overhead_seconds(edition_id: str, headline_count: int) -> int:
    intro = 55
    headlines = 20 + headline_count * 8
    transition_in = 5
    outro = 12
    # Two ad breaks: right_back + ad + welcome_back each
    rb = average_asset_duration(edition_id, "right_back")
    ad1 = average_asset_duration(edition_id, "ad_1")
    ad2 = average_asset_duration(edition_id, "ad_2")
    wb = 5
    ad_breaks = 2 * (rb + wb) + ad1 + ad2
    return intro + headlines + transition_in + outro + ad_breaks


def story_budget_seconds(edition_id: str, headline_count: int = 8) -> int:
    fixed = fixed_overhead_seconds(edition_id, headline_count)
    return max(600, TARGET_EPISODE_SECONDS - fixed)


def target_story_count(edition_id: str) -> int:
    budget = story_budget_seconds(edition_id)
    count = max(STORY_BLOCKS * 2, budget // SECONDS_PER_STORY_TARGET)
    return min(count, 18)


def seconds_per_story(edition_id: str, story_count: int) -> int:
    if story_count <= 0:
        return SECONDS_PER_STORY_TARGET
    budget = story_budget_seconds(edition_id, min(story_count, 10))
    per = budget // story_count
    return max(90, min(210, per))


def words_for_seconds(seconds: int) -> int:
    return int(seconds * WORDS_PER_MINUTE / 60)


def estimate_spoken_seconds(text: str) -> int:
    words = len(text.split())
    return max(1, int(words * 60 / WORDS_PER_MINUTE))


def budget_summary(edition_id: str, story_count: int) -> dict:
    fixed = fixed_overhead_seconds(edition_id, min(story_count, 10))
    per_story = seconds_per_story(edition_id, story_count)
    stories_total = per_story * story_count
    estimated = fixed + stories_total + (max(0, story_count - 1) * 3)
    return {
        "maxEpisodeSeconds": MAX_EPISODE_SECONDS,
        "targetEpisodeSeconds": TARGET_EPISODE_SECONDS,
        "fixedOverheadSeconds": fixed,
        "storyCount": story_count,
        "secondsPerStory": per_story,
        "wordsPerStory": words_for_seconds(per_story),
        "estimatedTotalSeconds": estimated,
        "estimatedTotalMinutes": round(estimated / 60, 1),
    }
