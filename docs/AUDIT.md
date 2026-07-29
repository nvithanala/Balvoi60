# BalVoi podcast pipeline — read-only audit

**Audit date:** 2026-07-29  
**Workspace:** `BalVoi_30` (product package name `balvoi60`)  
**Scope:** Application code, config, tests, CI, and operator docs as present on disk. Generated artifacts under `storage/` were inspected only as examples of shapes the code writes; they are not treated as source of truth for behavior.  
**Method:** Static reading of source + local `pytest` / `ruff` / `git ls-files`. No production systems were called for this document except where prior conversation already probed the NewsGenie API (not re-run for this file).  
**Rule:** Every factual claim cites `path:line` (or a path for directory-level facts). Unresolvable items are marked **UNVERIFIED**.

---

## 1. REPO MAP

### Top-level directories (one line each)

| Path | Role |
|------|------|
| `.git/` | Git metadata |
| `.github/` | CI workflow (`workflows/ci.yml`) |
| `.pytest_cache/` | Local pytest cache (generated) |
| `.ruff_cache/` | Local ruff cache (generated) |
| `Arabic/`, `English/`, `French/`, `German/`, `Portuguese/`, `Russian/`, `Spanish/`, `Turkish/` | Prerecorded ad / bumper MP3 trees referenced by `config/assets.json` (gitignored) |
| `balvoi/` | Shared library: dates, edition config helpers, paths, countries |
| `balvoi30.egg-info/` | Stale setuptools metadata for former package name `balvoi30` (gitignored; on disk) |
| `balvoi60.egg-info/` | Current editable-install metadata for package `balvoi60` (gitignored; on disk) |
| `config/` | Runtime JSON: editions, segments, assets, episode template |
| `docs/` | Specs, production-hardening notes, this audit |
| `pipeline/` | Hourly episode factory (fetch → publish) |
| `scripts/` | Operator / one-shot / prerender / Megaphone helpers |
| `server/` | Flask RSS + pages + optional in-process scheduler |
| `storage/` | Runtime state, episodes, caches, manifests (mostly gitignored) |
| `tests/` | Pytest suite |
| `web/` | Gitignored; README calls it legacy frontend — on this machine only `node_modules` was observed |

Root files of note: `pyproject.toml`, `requirements.txt`, `README.md`, `.env.example`, `.gitignore`, `BalVoi_30 Content.xlsx` (tracked content workbook), planning binaries (`*.pptx` / `*.docx` gitignored), scratch dumps (gitignored).

### Entry points

| Entry | Evidence |
|-------|----------|
| `python -m pipeline` | `pipeline/__main__.py:1-5` → `pipeline.run:main` |
| Console script `balvoi-pipeline` | `pyproject.toml:28` → `pipeline.run:main` |
| `python -m server` | `server/__main__.py:1-4` → `server.app:main` |
| Console script `balvoi-server` | `pyproject.toml:29` → `server.app:main` |
| Scheduler alone | `server/scheduler.py:107-116` (`if __name__ == "__main__"`) |
| Flask app factory | `server/app.py:52-135` (`create_app`), started from `main` at `137-145` |

There is **no** checked-in Node/React application entry. README states the Node/React stack was removed (`README.md:9`). Referenced migration doc `docs/python-migration.md` is **missing on disk** (glob found 0 files) — **UNVERIFIED** historical detail beyond README’s claim.

### Frontend

Live “frontend” is **server-rendered Flask** templates under `server/templates/` and `server/static/styles.css` (`server/app.py:61-98`, `pyproject.toml:36`).  
`web/` is listed in `.gitignore:61` as “Legacy frontend (Python-only stack).”

### Duplicates / older copies / parallel implementations

| Pair | Live? | Evidence |
|------|-------|----------|
| `balvoi60` package (`pyproject.toml:6` `name = "balvoi60"`) vs `balvoi30.egg-info/` | **Live:** `balvoi60`. **Stale:** `balvoi30.egg-info` embeds BalVoi:30 README (`:25`/`:55`, 30-minute windows) — `balvoi30.egg-info/PKG-INFO:2,18-22`. | Package name and current `balvoi/dates.py:13` (`PROCESSING_TRIGGER_MINUTE = 45`) disagree with egg-info copy. |
| `balvoi60.egg-info/` vs source tree | Generated install metadata mirroring current README; not an alternate runtime. | `balvoi60.egg-info/PKG-INFO:2` `Name: balvoi60`. |
| `pipeline/lib/openai_client.py` name vs OpenAI SDK | **Live path** is NewsGenie `POST /bedrock/prompt` via `requests` (`openai_client.py:1-4,14,26`). No `import openai` anywhere in `.py` files. | Declared dependency `openai>=1.30.0` (`pyproject.toml:15`) is unused by imports. |
| `scripts/*megaphone*` / `pipeline/lib/megaphone_once.py` vs `pipeline/run.py` | **Main hourly path:** `run_pipeline` in `pipeline/run.py`. **Parallel English once-path:** `megaphone_once.py` + scripts. | Both call Megaphone helpers; once-path is not the scheduler’s subprocess (`server/scheduler.py:55-64` runs `-m pipeline`). |
| `article_lookback_window` (`balvoi/dates.py:158-169`) vs main selection | Function exists; main path comment says empty hour does **not** widen (`pipeline/run.py:161`). | Lookback used in tests (`tests/test_dates.py:252-254`); not called from `run_pipeline`. |
| `fit_stories_to_budget` vs transform | Helper is a no-op (`duration_budget.py:94-103`); transform never calls it. | Grep: only `duration_budget.py` + `tests/test_transform_and_budget.py`. |
| Workspace folder `BalVoi_30` / optional second tree `C:\BalVoi_30` | Folder name is legacy branding. A second directory `C:\BalVoi_30` **exists on this machine** (`Test-Path` True) with an older `fetch_podcast_articles()` signature observed earlier in session. | **UNVERIFIED** whether infra/operators ever run that tree; **this workspace’s** `python -c` import of `pipeline` resolved to the OneDrive path when cwd was this repo. |
| `robot_script/` | **Absent** | `Test-Path robot_script` → False |

---

## 2. PIPELINE FLOW

Orchestrator: `run_pipeline` (`pipeline/run.py:639-876`) → shared `_freeze_selection` (`89-223`) → `transform_stories_english` → per-language `_process_language` (`240-618`).

### Stages (main path)

| Stage | Function | Reads | Writes (disk) |
|-------|----------|-------|----------------|
| Fetch | `fetch_articles` `pipeline/stages/fetch_articles.py:109-148` → `fetch_podcast_articles` `pipeline/lib/balvoi_api.py:182+` | NewsGenie `GET {BALVOI_API_URL}/podcast_articles` | Default `storage/articles/latest.json` (`fetch_articles.py:138-146`) |
| Select + freeze | `select_stories` `select_stories.py:70-159`; freeze in `run.py:183-200` | Article pool; cooldown IDs from `storage/manifests/runs/*.json` (`story_history.py`) | Non-empty: `storage/manifests/selection/{boundary_key}.json` (`run.py:199-200`) |
| Transform EN | `transform_stories_english` `transform_stories.py:13-45` | Selected articles; Bedrock headlines via `openai_client` | **None on main path** (in-memory). Once-path may write `runs/<run_id>/english_stories.json`. |
| Localize | `localize_stories` `transform_stories.py:48-63` | English stories | In-memory only |
| Headlines | `headlines_segment` `transform_stories.py:66-70` | Primers | In-memory string |
| Assemble | `assemble_episode` `assemble_episode.py:69-190` | Edition, `segments.json`, `assets.json`, template | In-memory manifest dict. Once-path may persist `episode_manifest.json`. |
| Synthesize | `render_segments` `synthesize.py:75-247` | Manifest; reusable + dynamic caches; prerecorded under language folders | Cache fills under `storage/audio_assets/reusable/…` and `storage/cache/tts/…` |
| Merge | `merge_segments` `merge_audio.py:19-62` | Segment MP3 paths | `storage/episodes/{run_id}/{slug}.mp3` |
| Duration gate | `validate_publishable_audio` `merge_audio.py:95-110` | Merged MP3 | None (raises if below minimum) |
| Media prepare | `prepare_public_episode_media` `episode_media.py:45-122` | Merged MP3 | Canonical episode path; optional S3 upload |
| Megaphone | `publish_episode` `megaphone_client.py` (called from `run.py` ~452+) | Public URL + metadata | Megaphone API + local publication result store |
| Local publish | `publish_run` `publish.py:40-134` | Episode metadata | `latest.json`, `history.json`, `runs/{run_id}-{slug}.json`, aggregate `status.json` |
| Status / report | `record_status`; `write_hourly_report` | Run state | `manifests/status/{run_id}-{slug}.json`; `manifests/reports/{run_id}.json` |

### Episode manifest shape

Returned by `assemble_episode` (`assemble_episode.py:182-190`):

- `editionId`, `slug`, `language`, `voice`, `segments`, `picks`, `storyIds`
- Segment TTS entries: `type`, `segmentType`, `text`, `sheet`, `variant`, optional `reusable` (`94-103`)
- Segment audio entries: `type`, `segmentType`, `path`, `sheet`, `variant` (`105-114`)

On-disk example (once-path artifact): `storage/runs/2026-07-24T20-00-00Z/episode_manifest.json`.

### History / latest shape

Written in `publish_run` (`publish.py:82-125`). Episode object includes `id` (`{run_id}-{slug}`), `runId`, `publicationBoundary`, edition fields, `timestamp`, relative `audioUrl` (`/episodes/...`), `durationSeconds`, `anchor`, `storyIds`, `headlines` (original titles), `picks`, `budget`.  
`history.json`: newest-first list, capped at 200 (`publish.py`).  
`latest.json`: map slug → episode.

### How audio reaches a public URL / Cloudflare

1. Merge writes `storage/episodes/{run_id}/{slug}.mp3`.
2. `production_public_audio_url` builds `{PUBLIC_BASE_URL}/episodes/{run_id}/{slug}.mp3` (`megaphone_client.py:249-251`).
3. Flask serves that path from disk: `server/app.py:122-124` `send_from_directory(data.episodes_dir(), subpath, conditional=True)`.
4. Optional S3: if `BALVOI_EPISODE_S3_BUCKET` is set, `episode_media.py:27-42` uploads with `ContentType: audio/mpeg` via `boto3`; Megaphone still uses the **PUBLIC_BASE_URL** HTTPS URL, not the `s3://` URI (`episode_media.py:74-85`).

**Cloudflare product identification:**  
Application code contains **no** R2 client, Workers script, wrangler config, or Tunnel daemon. Local `.env` / run state may contain a `*.trycloudflare.com` host (observed in `storage/runs/2026-07-24T20-00-00Z/state.json:12` as `publicMp3Url`). That hostname pattern indicates a **Cloudflare Tunnel quick URL** was used as `PUBLIC_BASE_URL` at some point, but **which Cloudflare product fronts production for the company deploy is UNVERIFIED** from repository code alone. Needed: infra team confirmation (Tunnel vs R2 custom domain vs other CDN in front of Flask/S3).

---

## 3. SCHEDULING AND DURATION

### Cadence (current values)

| Mechanism | Current value | Citation |
|-----------|---------------|----------|
| Processing trigger minute | **45** UTC | `balvoi/dates.py:13` `PROCESSING_TRIGGER_MINUTE = 45` |
| Scheduler loop | Fires when `now.minute == TRIGGER_MINUTE` and `second < 30` | `server/scheduler.py:22,82-85` |
| Publication boundary | Next hour `:00` if minute ≥ 45; else current hour `:00` | `balvoi/dates.py:71-83` |
| Ownership window | Exactly 60 minutes ending at `boundary − 15m` → `[HH-2:45, HH-1:45)` for boundary `HH:00` | `balvoi/dates.py:138-155` |
| Cron expressions | **None** in-repo | No crontab / EventBridge JSON found |
| Scheduler env | `SCHEDULER_ENABLED` (and legacy `CRON_ENABLED` via `scheduler_enabled`) | `server/app.py:139`; `.env.example:37` |

### Article window / lookback

| Knob | Current behavior | Citation |
|------|------------------|----------|
| `BALVOI_ARTICLE_WINDOW_MINUTES` | Default **60**; settings **require** exactly 60 | `settings.py:372-381` |
| Effect on formula | Validated but **not** plugged into `article_ownership_window` | Window hard-coded 60m + 15m offset (`dates.py:153-155`) |
| `.env.example` comment | “Only pick articles published in the last N minutes (fallback: latest available)” | `.env.example:15-16` — wording does not match gap-free ownership formula |
| `article_lookback_window` | Defined; docstring says used when hourly window empty | `dates.py:158-165` — **not** used by `run_pipeline` (empty → skip, `run.py:161-164`) |
| API `since` | `window_start` ISO, or `previous_podcast_boundary()` fallback | `balvoi_api.py:194`; `dates.py:96-106` (`:00`-based, not `:45`) |

### Episode length

| Knob | Current value | Citation |
|------|---------------|----------|
| `MIN_PUBLISH_DURATION_SECONDS` | Default **600** | `.env.example:52`; `duration_budget.py:10`; `merge_audio.py:101` |
| Template `minimumPublishDurationSeconds` | **600** | `config/episode-template.json:2` |
| Template segment `durationSec` ranges | Documentary budgets (e.g. story blocks `"480-540"`) | `episode-template.json:35-48` — **not** enforced as max/min in merge beyond the 600s floor |
| MAX / TARGET publish seconds | **Absent** as publish caps | `duration_budget.py:1` “no target runtime”; `story_budget_seconds` returns `0` (`54-56`) |
| `edition_was_published` threshold | Hard-coded **`>= 600`** | `edition_lock.py:28` — does not read env |

### Places that disagree

1. **Trigger minute docs vs code:** `README.md:58-60` and `.env.example:37` say process at UTC **`:51`**; code uses **`:45`** (`dates.py:13`, `scheduler.py:22`). Several files under `docs/production-hardening/` and `docs/system-specification/` still say `51` (e.g. prior EXECUTIVE_SUMMARY / CONFIGURATION_MATRIX text).
2. **`BALVOI_ARTICLE_WINDOW_MINUTES` commentary:** `.env.example:15` implies a sliding “last N minutes” picker; code uses fixed ownership windows from the publication boundary.
3. **Min duration env vs lock check:** publish validation uses env (`merge_audio.py:101`); history “already published” check hardcodes 600 (`edition_lock.py:28`).
4. **Template AI tasks vs runtime:** `episode-template.json:50-53` lists `amazon_bedrock` / `newscaster_rewrite`; English body is verbatim (`openai_client.py:130-132`).

---

## 4. CONFIG vs CODE

| Claim location | Claim | Code reality |
|----------------|-------|--------------|
| `README.md:2-3` | “rewrites it like a real anchor would read it” | English `broadcastScript` is verbatim body (`openai_client.py:130-132`; `transform_stories.py:28`) |
| `README.md:38` | Configure `OPENAI_API_KEY` | LLM path uses `BALVOI_API_KEY` → `/bedrock/prompt` (`.env.example:19-21`; `openai_client.py:1-4`). `OPENAI_API_KEY` still checked for `--preview` (`run.py:929-934`) |
| `README.md:58-60` / `.env.example:37` | Scheduler `:51` | Code `:45` (`dates.py:13`) |
| `README.md:9` → `docs/python-migration.md` | Migration doc exists | File **missing** |
| `config/episode-template.json:37` | Headlines source `bedrock_primers_tts` | Primers may be batch-rewritten via Bedrock; join is local `batch_headline_intro` (`openai_client.py:310-318`) |
| `config/episode-template.json:39,47` | Story source `bedrock_rewrite_tts` | English stories not rewritten through Bedrock |
| `config/episode-template.json:50-53` | `aiPipeline.provider: amazon_bedrock`, task `newscaster_rewrite` | No `newscaster_rewrite` function; provider call is HTTP to NewsGenie Bedrock proxy |
| `.env.example:7-8` / README articles path notes | Operators may set `BALVOI_API_ARTICLES_PATH` | **Implemented** in `balvoi_api.py:24-41` |
| `dates.py:158-164` docstring | Lookback used when hourly window empty | Main path skips episode instead (`run.py:161-164`) |
| Stale docs saying `PROCESSING_TRIGGER_MINUTE = 51` | e.g. older system-spec matrices | Current constant is 45 (`dates.py:13`; `tests/test_dates.py:86`) |
| `balvoi30.egg-info/PKG-INFO` | `:25`/`:55`, 30-minute product | Not live code |

---

## 5. FAILURE BEHAVIOR

### Exception handling patterns

- **No bare `except:`** found under `pipeline/`, `server/`, `balvoi/`.
- Soft-fail examples:
  - `duration_budget.py:26-28` — ffprobe failure → size heuristic
  - `openai_client.py` — non-strict chat / failed headline batch → warn + fallback / `None` (title primers kept, `transform_stories.py:37-39`)
  - `run.py` hourly report failure → warn, continue (near end of `run_pipeline`)
  - `server/scheduler.py:86-89` — exception in tick logged; loop continues
  - `edition_lock.py` lock release OS/JSON errors → `pass`
- Hard-fail examples: merge/ffmpeg errors, duration below minimum (`merge_audio.py`), Megaphone/publish typed errors, localization strict failures.

### Empty / degraded outcomes

| Condition | Behavior | Exit |
|-----------|----------|------|
| Empty ownership window / empty selection | Status `failed_selection`; locks released; selection **not** frozen | **2** (`run.py:761-781`) |
| Fetch exception in freeze | `failed_fetch` statuses | **2** (`run.py:725-752`) |
| Empty English after transform | `empty_english` | **2** (`run.py:809-830`) |
| Languages run but none published | | **1** (`run.py:852`) |
| Uncaught exception in `main` | traceback | **1** (`run.py:957-960`) |
| Dry-run TTS | Segments skipped without calling ElevenLabs; early success return without merge/publish | `synthesize.py:176-178,225-227`; `run.py` dry-run branch |

Degraded episode emission: dry-run can exit success without audio. Live path does **not** publish if duration &lt; minimum (`validate_publishable_audio`). Headline batch failure keeps title primers (still produces an episode if other stages succeed).

### Retry after partial failure

- **Edition lock:** `O_EXCL` file lock; `DuplicateEditionError` if history shows same boundary/slug with `durationSeconds >= 600` (`edition_lock.py:18-29,45+`).
- **Publication claim:** terminal `completed`/`failed` blocks re-acquire for that `publication_key` (`publication_claim.py` — same-run `acquired` may re-enter).
- **Frozen selection:** non-empty freeze reused; empty deleted so fetch can retry (`run.py:107-108,197-198`).
- **Megaphone create:** local result → remote reconcile by `externalId` → else create (`megaphone_client` publish path). Designed to avoid duplicate creates for the same identity.
- **MP3:** `ffmpeg -y` overwrite of `storage/episodes/{run_id}/{slug}.mp3` on remake (`merge_audio.py`). Same path reused; Megaphone episode identity is separate from file bytes (replace media is a distinct PUT helper).

---

## 6. MEGAPHONE / RSS / CLOUDFLARE

### Episode audio URLs

| Property | Finding | Citation |
|----------|---------|----------|
| Form | Absolute when `PUBLIC_BASE_URL` set: `{base}/episodes/{run_id}/{slug}.mp3` | `megaphone_client.py:249-251` |
| Relative fallback | `/episodes/...` when Megaphone off and no public base | `episode_media.py:87-96` |
| Auth on URL | No signed/expiring URL builder in-repo; Megaphone fetches the public HTTPS URL | `episode_media.py:99-104` requires `https://` when probing for Megaphone |
| Permanence | Path stable for a given `run_id`+slug; bytes can be overwritten on regenerating same `run_id` | merge `-y`; same object key for S3 (`episode_media.py:66`) |

### Cloudflare

**UNVERIFIED product** beyond: operators have pointed `PUBLIC_BASE_URL` at a `trycloudflare.com` host (run artifact `state.json`). Code supports Flask origin and optional **S3** upload (`episode_media.py:1-7,27-42`), not Cloudflare APIs.

### Serving headers / Range

`server/app.py:122-124` uses Flask `send_from_directory(..., conditional=True)`. Werkzeug then supplies conditional/Range behavior and Content-Length / guessed Content-Type for `.mp3`. No custom middleware sets headers explicitly.  
S3 upload sets `ContentType: audio/mpeg` (`episode_media.py:40-41`). Whether the production CDN preserves Range is **UNVERIFIED**.

### RSS (`server/feed.py`)

| Field | Behavior | Citation |
|-------|----------|----------|
| Enclosure URL | `{base}{audioUrl}` | `feed.py:43,53` |
| Enclosure length | File size via `data.audio_size` | `feed.py:40-41,53` |
| Enclosure type | `audio/mpeg` | `feed.py:53` |
| guid | `isPermaLink="false"`, value episode `id` = `{run_id}-{slug}` | `feed.py:52`; `publish.py:83` |
| guid stability | Stable for same `run_id`+slug; new boundary → new `run_id` → new guid | `canonical_run_id` / publish id |
| pubDate | RFC2822 of episode `timestamp` (wall clock at local publish), **not** publication boundary | `feed.py:51`; `publish.py:92` |
| itunes:duration | From `durationSeconds` | `feed.py:54` + `_itunes_duration` |
| itunes:explicit | `false` (item + channel) | `feed.py:56,73` |
| itunes:image | Channel only, if artwork URL configured | `feed.py:78` |
| Channel required-ish tags present | title, link, description, language, atom self-link, itunes author/summary/type/category/explicit/owner | `feed.py:63-77` |

Items with missing/zero-length audio are skipped (`feed.py:41-42`).

---

## 7. SECRETS AND HYGIENE

### Credentials in the working tree

| Item | Notes |
|------|-------|
| `.env` | Present locally; **gitignored** (`.gitignore:2-5`). Contains live-looking secrets — **do not commit**. Values not reproduced here. |
| `.env` in git history | `git log --all -- .env` returned empty in this audit |
| Tracked secret-like material | No `sk-` / `AKIA` / private key blobs found in tracked `.py` via search |
| `.env.example` | Placeholder API keys; **real-looking Megaphone network + podcast UUIDs** embedded (`.env.example:72,94-115`). These are IDs, not tokens; still environment-specific. |
| `storage/runs/.../state.json` | May contain public URLs; `storage/runs/` is **not** listed in `.gitignore` (gap vs other storage dirs) |

### `.env.example` vs code

Documented keys cover the main settings surface (API, TTS, Megaphone, concurrency, `PUBLIC_BASE_URL`, etc.). Gaps / drift:

- Comments still say scheduler `:51` while code is `:45`.
- Comments mark OpenAI as unused legacy, but preview still requires `OPENAI_API_KEY` (`run.py:929-934`).
- Optional keys appear only as comments (`BALVOI_EPISODE_S3_BUCKET`, structured logs, HTTP timeouts).

### `.gitignore` coverage

Covers `.env`, egg-info, language MP3 folders, most of `storage/*`, `web/`, pptx/docx/xlsx (with exception keeping `BalVoi_30 Content.xlsx` / `BalVoi_60 Content.xlsx`).  
**Not ignored:** `storage/runs/` (runtime once-path artifacts can be added accidentally).

### Tracked large / generated files

From `git ls-files` (87 tracked paths at audit time):

- `BalVoi_30 Content.xlsx` (~37 KB) — intentionally force-included.
- `storage/seen-articles.json` — listed in `.gitignore:32` but **still tracked**.
- Language MP3 trees, egg-info, pptx/docx — **not** tracked.
- Many newer test modules and Megaphone helper modules exist on disk but are **untracked** relative to the 87-file index (working tree ahead of last commit).

---

## 8. QUALITY BASELINE

### Tests

| Metric | Result |
|--------|--------|
| Command | `python -m pytest tests/ -q --tb=no` |
| Result | **449 passed** in ~1.77s |
| Test files on disk | **42** `tests/test_*.py` |
| Test files tracked in git | **19** |
| Coverage focus | Dates/scheduler, settings, selection, transform/budget, merge, feed, Megaphone/claim/once (many of the latter untracked) |

### Lint

| Metric | Result |
|--------|--------|
| Config | `pyproject.toml:46-65` ruff `E,F,I,UP`, ignore `E501`, target `py311` |
| Command | `python -m ruff check .` |
| Result | **39 errors** (mostly import sorting `I001`; 18 auto-fixable). **Does not pass clean.** |

### CI

`.github/workflows/ci.yml`: on push/PR to `main`/`master` — ruff on Python 3.12; pytest matrix 3.11 and 3.12 after `pip install -e ".[dev]"`.

### Dependencies

| Package | Declared | Imported in app? |
|---------|----------|------------------|
| `requests` | yes | yes (`balvoi_api`, `openai_client`, Megaphone, etc.) |
| `flask` | yes | yes (`server/app.py`) |
| `python-dotenv` | yes | yes (`settings` / scripts) |
| `boto3` | yes | yes, optional S3 (`episode_media.py:33`) |
| `tzdata` | yes | used indirectly via zoneinfo on Windows |
| `openpyxl` | yes | `scripts/load-spec.py` only |
| `openai` | yes (`pyproject.toml:15`) | **No `import openai`** — declared-but-unimported |
| `pytest` / `ruff` | optional `dev` | CI + local |

### Language / runtime assumptions

- `requires-python = ">=3.11"` (`pyproject.toml:10`)
- CI: 3.11 + 3.12
- ffmpeg required for real merges (`README.md:30`; merge stage)
- No Dockerfile / compose / Terraform in repo

### Node frontend

No package.json application in the tracked tree; `web/` gitignored. UI is Flask.

---

## 9. VERIFY OR REFUTE

Treat each as a question about **current** code.

### Scheduler once used `TRIGGER_MINUTES = {45, 45}`

**REFUTED (as written) / CHANGED SINCE dual-trigger era.**  
Current code: single `TRIGGER_MINUTE = PROCESSING_TRIGGER_MINUTE` (`server/scheduler.py:22`), value **45** (`balvoi/dates.py:13`). There is no set `{45, 45}`. Historical BalVoi:30 dual triggers `:25`/`:55` appear only in stale `balvoi30.egg-info/PKG-INFO:22`.

### `BALVOI_ARTICLE_WINDOW_MINUTES` once defaulted to 60

**CONFIRMED** still defaults to **60** and must equal 60 (`settings.py:372-381`; `.env.example:16`).

### `previous_podcast_boundary()` once keyed off `:45/:45`

**REFUTED for current code.**  
Function uses top-of-hour `:00` boundaries only and documents independence from the `:45` trigger (`balvoi/dates.py:96-106`). Ownership `:45` boundaries are `article_ownership_window` (`138-155`).

### `duration_budget.py` once had MIN/MAX/TARGET episode second constants

**CHANGED.**  
Present: `MIN_PUBLISH_DURATION_SECONDS = 600` plus estimation helpers (`SECONDS_PER_STORY_TARGET`, etc.) (`duration_budget.py:10-14`). **No** MAX/TARGET publish constants. Module states no target runtime (`duration_budget.py:1`). `story_budget_seconds` returns `0` (`54-56`).

### `select_stories.py` once had a `budget_full` exclusion

**REFUTED (absent now).**  
Exclusion reasons in current file: cooldown, out_of_window, country (`select_stories.py:105-121,145-157`). No `budget_full` string.

### Transform once called `fit_stories_to_budget()`

**REFUTED.**  
`transform_stories.py` does not call it. `fit_stories_to_budget` is a no-op returning `list(stories)` (`duration_budget.py:94-103`), used in tests only.

### `run.py` once warned on over/under episode duration

**REFUTED / CHANGED.**  
Current gate is minimum duration via `validate_publishable_audio` (`run.py` ~371; `merge_audio.py:95-110`). No over/under duration warning loop found in `run.py`.

### `newscaster_rewrite()` reportedly truncated text instead of calling a model

**REFUTED (function absent).**  
Name exists only as a config task string (`episode-template.json:53`). Runtime English prep is verbatim (`openai_client.py:130-132`). Long TTS uses chunking (`tts_chunking` / `synthesize.py:43-72`), not a `newscaster_rewrite` truncator.

### `boto3` reportedly declared but unused

**CHANGED / REFUTED as “unused.”**  
Still declared (`pyproject.toml:16`; `requirements.txt:6`). **Used** for optional S3 upload (`episode_media.py:32-41`).

### Legacy `robot_script/` tree may coexist with `pipeline/`

**REFUTED (absent).**  
No `robot_script/` directory. Live pipeline is `pipeline/` (+ `server/`, `balvoi/`).

---

## 10. OPEN QUESTIONS

Things that cannot be closed from this repository alone:

1. **What Cloudflare product** (Tunnel, R2+custom domain, proxy to Flask, etc.) will the infrastructure team run in front of `{PUBLIC_BASE_URL}/episodes/...`, and is Range/`audio/mpeg` preserved end-to-end?
2. Is **`BALVOI_EPISODE_S3_BUCKET`** intended for production, or will Flask/local disk remain the origin behind the CDN?
3. Confirm production **`PUBLIC_BASE_URL`** (not the local `trycloudflare.com` leftover).
4. Will the commit include currently **untracked** Megaphone modules/tests/scripts, or only the 87 tracked paths?
5. Should operator docs be corrected to **`:45`** everywhere before handoff, or is a return to `:51` planned?
6. Is empty-hour **exit 2** (no lookback) the desired production policy when NewsGenie staging has no articles?
7. Are Megaphone podcast/network UUIDs in `.env.example` safe to publish in the company repo?
8. What is the operational status of the parallel tree at **`C:\BalVoi_30`** (if any automation still points there)?
9. Preview still requires **`OPENAI_API_KEY`** while main LLM is Bedrock-via-NewsGenie — intentional?
10. Missing **`docs/python-migration.md`** — restore, rewrite, or drop the README link?

---

*End of audit. No fix list by request.*
