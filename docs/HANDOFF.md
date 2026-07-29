# BalVoi:60 — operator handoff

**Date:** 2026-07-29  
**Sources:** `docs/AUDIT.md`, `docs/_harvest.md`, `pipeline/lib/settings.py`  
**Trigger minute ruling:** **`:45` is unratified — document as open.** Code runs Phase A at UTC `:45` (`balvoi/dates.py` `PROCESSING_TRIGGER_MINUTE = 45`). The timing decision checklist marks `:45` done while its Open product choices still ask `:45` vs `:51`. Later backlog/AWS docs still say `:51`. Do not treat either minute as a closed product vote; measure and decide explicitly.

---

## 1. EXECUTION MODEL

### Two-phase hourly run

```text
Phase A — at ownership-window close (:45 UTC in current code)
  Assign next publication boundary (next :00)
  Fetch articles for the ownership window
  Generate editions (transform → TTS → merge → validate)
  prepare_public_episode_media (local path and/or optional S3)
  Prepare title/summary metadata
  — no live Megaphone create in this phase —

  [process SLEEPS until publication boundary]

Phase B — at/after publication boundary (:00 UTC)
  wait_until_publication_boundary(boundary)   # no-op if already late
  publish_episode(..., draft=False)           # main path live create
  publish_run / history / claim complete
  Persist Megaphone episode ID + publicationDelaySeconds
```

**Sleep:** The same process sleeps between Phase A and Phase B. Do **not** run this on a platform that kills idle tasks or cannot survive ~15 minutes of sleep (short Fargate billing windows, aggressive idle timeouts, etc.) unless you split build and publish into separate scheduled jobs.

**Late Phase A:** If Phase A takes longer than the remaining time to `:00`, the wait is a **no-op** and publication is late. **`publicationDelaySeconds`** (create success UTC − boundary UTC) is the metric that detects lateness.

**Megaphone timing:** Create happens **at/after `:00` by design**. Setting `pubdate` on the episode does **not** hold release until `:00` — do not assume Megaphone scheduled publication from `pubdate` alone (timing decision).

### Window arithmetic

For publication boundary `HH:00` UTC:

| Concept | Formula | Example (boundary 19:00) |
|---------|---------|--------------------------|
| Ownership window | `[boundary − 2h15m, boundary − 1h15m)` = `[HH-2:45, HH-1:45)` | `[17:45, 18:45)` |
| Processing trigger (code) | UTC **`:45`** (unratified product choice) | Start ~18:45 for 19:00 |
| Publication | Boundary `:00` | 19:00 |

Adjacent boundaries are gap-free: an article at exactly `18:45` belongs only to the `20:00` window.

---

## 2. ENVIRONMENT VARIABLES

Derived from `pipeline/lib/settings.py` (`load_settings` / `AppSettings`). Process env wins over `.env` (`override=False`).

| Name | Required? | Default | Purpose |
|------|-----------|---------|---------|
| `BALVOI_ENV` | **REQUIRED (fail-fast when Megaphone on)** | `development` if unset | Runtime profile. Hard-blocks demo articles when set to `staging`/`production`. **When `MEGAPHONE_ENABLED=true`, unset `BALVOI_ENV` is rejected at startup** — unset defaults to development and disables the demo-article block. Set explicitly even for local Megaphone tests (`development` is allowed if present in the environment). |
| `BALVOI_SERVICE_NAME` | optional | `balvoi60` | Service label |
| `STORAGE_PATH` | optional | `storage` | Runtime storage root |
| `PIPELINE_EDITIONS` | **REQUIRED for scheduler use** | settings/CLI: `en,es,pt,fr,de,ar,ru,tr`; **scheduler subprocess default if unset: `en` only** | Explicitly set for production. Mismatch: in-process scheduler defaults to `"en"`; CLI/`settings.py` default is all eight. Leaving unset under the scheduler silently under-publishes (English only). |
| `SCHEDULER_ENABLED` | optional | `false` | In-process Flask scheduler |
| `CRON_ENABLED` | optional (legacy) | — | Alias for scheduler; conflict with `SCHEDULER_ENABLED` → error |
| `MEGAPHONE_ENABLED` | optional | `false` | Master Megaphone switch |
| `MEGAPHONE_CREATE_AS_DRAFT` | optional | `true` | Default draft flag when create does not pass `draft=` |
| `MEGAPHONE_API_BASE` | optional | `https://cms.megaphone.fm/api` | Megaphone CMS API |
| `MEGAPHONE_API_TOKEN` | when Megaphone on | `""` | Auth (or per-slug tokens) |
| `MEGAPHONE_API_TOKEN_{SLUG}` | optional | — | Per-edition token override (`EN`…`TR`) |
| `MEGAPHONE_NETWORK_ID` | when Megaphone on | `""` | Network UUID |
| `MEGAPHONE_NETWORK_ID_{SLUG}` | optional | — | Per-edition network |
| `MEGAPHONE_PODCAST_ID_{SLUG}` | when Megaphone on for that slug | — | Podcast UUID per edition |
| `PUBLIC_BASE_URL` | required if Megaphone on | `""` | Public HTTPS origin for `/episodes/...` |
| `BALVOI_ALLOW_PRIVATE_PUBLIC_BASE_URL` | optional | `false` | Break-glass private hosts (not placeholders; not staging/prod) |
| `BALVOI_SITE_URL` | optional | `https://staging.balvoi.com` | Story URL base / demo URLs |
| `BALVOI_API_URL` | optional | `https://api.staging.newsgenie.ai` | NewsGenie API base |
| `BALVOI_API_KEY` | required non-dry unless demo | `""` | `X-Api-Token` for articles + Bedrock proxy |
| `BALVOI_ARTICLE_LIMIT` | optional | `200` | Fetch page size (no pagination) |
| `BALVOI_ARTICLE_WINDOW_MINUTES` | must be 60 | `60` | Validated; ownership math is fixed in code |
| `BALVOI_STORY_COOLDOWN_MINUTES` | optional | `360` | History exclusion (`0` disables) |
| `BALVOI_ALLOW_DEMO_ARTICLES` | optional | `false` | Demo corpus if API empty; **forbidden** when `BALVOI_ENV` is staging/production |
| `OPENAI_API_KEY` | optional (preview still checks) | `""` | Legacy name; main LLM uses NewsGenie `/bedrock/prompt` via `BALVOI_API_KEY` |
| `OPENAI_MODEL` | optional | `gpt-4o-mini` | Model name field (proxy path) |
| `ELEVENLABS_API_KEY` | required non-dry | `""` | TTS |
| `HTTP_CONNECT_TIMEOUT_SECONDS` | optional | `10` | HTTP connect timeout |
| `HTTP_READ_TIMEOUT_SECONDS` | optional | `45` | HTTP read timeout |
| `HTTP_RETRY_LIMIT` | optional | `3` | HTTP retries |
| `FFMPEG_PATH` | optional | `""` | ffmpeg binary; else PATH |
| `LOG_LEVEL` | optional | `INFO` | Log level |
| `DRY_RUN` | optional | `false` | Skip live side effects / TTS spend |
| `PREVIEW_MODE` | optional | `false` | Isolated preview; forces Megaphone/scheduler/demo off |
| `MIN_PUBLISH_DURATION_SECONDS` | optional | `600` | Publish floor (merge validate + edition_lock history check) |
| `LANGUAGE_WORKER_CONCURRENCY` | optional | `4` | Parallel languages |
| `TRANSLATION_CONCURRENCY` | optional | `4` | Translation workers |
| `TTS_REQUEST_CONCURRENCY` | optional | `3` | TTS workers |
| `MERGE_CONCURRENCY` | optional | `2` | Merge workers |
| `PORT` | optional | `3001` | Flask port |

**Not in `settings.py` but used by code:** `BALVOI_EPISODE_S3_BUCKET`, `AWS_REGION` / `AWS_DEFAULT_REGION` (`episode_media.py`); `BALVOI_SINCE_OVERRIDE` (API helpers). Set when using S3 upload or test overrides.

---

## 3. AUDIO ORIGIN REQUIREMENTS

`PUBLIC_BASE_URL` must be:

- **Stable** — same host over time for a given deployment  
- **Public** — Megaphone fetches the URL from the internet  
- **Permanent** for the lifetime of the episode object (path `{PUBLIC_BASE_URL}/episodes/{run_id}/{slug}.mp3`)  
- Serving **`audio/mpeg`** with correct **Content-Length** and **HTTP Range** support  

A **Cloudflare quick tunnel** (`*.trycloudflare.com`) does **not** qualify: the hostname dies when the tunnel process stops, and Megaphone **re-fetches** media on reconcile and media-replace. Ephemeral tunnels produce broken or flaky Megaphone episodes.

**Optional upload:** set `BALVOI_EPISODE_S3_BUCKET` to upload the MP3 via boto3 (`ContentType: audio/mpeg`). Megaphone still uses the **HTTPS** `PUBLIC_BASE_URL` URL, not `s3://`. Cloudflare **R2** is S3-API-compatible if you point credentials/endpoint at R2 and put a stable public CDN hostname in `PUBLIC_BASE_URL`.

---

## 4. FAILURE MODES AND EXIT CODES

From `docs/AUDIT.md` §5:

| Condition | Behavior | Exit |
|-----------|----------|------|
| Empty ownership window / empty selection | `failed_selection`; locks released; selection **not** frozen | **2** |
| Fetch exception in freeze | `failed_fetch` statuses | **2** |
| Empty English after transform | `empty_english` | **2** |
| Languages run but none published | | **1** |
| Uncaught exception in `main` | traceback | **1** |
| Dry-run TTS | Skip ElevenLabs; early success without merge/publish | **0** (success path) |

Also: live path does **not** publish if duration &lt; minimum. Soft fails include ffprobe→size heuristic, LLM/headline fallbacks, hourly report warn-and-continue, scheduler tick errors (loop continues).

Retry controls: edition lock + history `durationSeconds >= 600`; publication claims (`completed`/`failed` block re-acquire); frozen selection reuse; Megaphone local result → remote reconcile by `externalId` → else create.

---

## 5. RECOVERY RUNBOOKS

Only procedures marked **VERIFIED** in `_harvest.md` Priority 2.  

**Do not** follow older docs that say the main path does not persist a Megaphone episode ID. That is **false post-M2**. Main path writes `manifests/megaphone_publications/` and reconciles by `externalId` before create. Blind re-POST after a lost response risks a **duplicate episode**. Prefer local publication-result + remote reconcile; use once-path resume when applicable.

### Duplicate scheduler / overlapping runs

1. Disable extras: `SCHEDULER_ENABLED=false` on all but one host.  
2. Inspect `storage/locks/*`.  
3. Check Megaphone for `externalId=balvoi60:{slug}:{boundary}`.  
4. If duplicate remotes exist, use Megaphone UI/support (no in-repo cleanup API).  
5. Restore a single writer.

### Public media URL not reachable

1. Confirm object exists at the expected key.  
2. HEAD/GET via `probe_media_file_url` / `scripts/check_megaphone.py`.  
3. Do not enable Megaphone create until HTTP 200.  
4. Once-path already blocks on verify failure; main path also probes before create.

### Empty selection / no stories

1. Check NewsGenie credentials and window logs.  
2. Confirm demo mode is off.  
3. Accept exit code **2**; wait for the next boundary.  
4. Do not widen the ownership window ad hoc in production.

### ffmpeg / short audio

1. Confirm ffmpeg/ffprobe on PATH.  
2. Inspect segment list / missing prerecorded assets.  
3. Failed language only; others may succeed — republish a single slug carefully with locks.

### Secret rotation

1. Update `.env` (or process env).  
2. Restart pipeline/server processes (env is read at start).  
3. Verify with a dry-run or staging fetch/TTS.

### Disk full

1. Stop the scheduler.  
2. Archive old `storage/episodes` to cold storage.  
3. Clear `storage/cache/tts` if safe.

### Operator don’ts

- Do not delete a frozen selection to “force refresh” after languages partially published.  
- Do not set `BALVOI_ALLOW_DEMO_ARTICLES=true` to fill an hour.  
- Do not point `PUBLIC_BASE_URL` at localhost for Megaphone.  
- Do not run two writers against the same Megaphone podcast without global locks.

### Pre-flight commands

```text
python -m ruff check .
python -m pytest
python -m pipeline --editions en --dry-run
python scripts/check_megaphone.py --run-id <id>   # read-only as documented
```

---

## 6. EXTERNAL SERVICE SETUP

### Megaphone

- Provision **network** + **per-edition podcast** UUIDs → `MEGAPHONE_NETWORK_ID` / `MEGAPHONE_PODCAST_ID_{SLUG}` (+ tokens).  
- Keep staging and production IDs/tokens separated (example UUIDs’ environment is **UNVERIFIED**).  
- Default env creates drafts (`MEGAPHONE_CREATE_AS_DRAFT=true`); main path at boundary passes **`draft=False`**.  
- Do not assume `pubdate` alone schedules go-live.  
- Search CMS by `externalId=balvoi60:{slug}:{boundary}` on incidents.

### ElevenLabs

- Live `ELEVENLABS_API_KEY`.  
- Voice IDs are product choices in `config/editions.json` `voiceShifts` (primary IDs; many secondaries `null`). Keep the ElevenLabs library aligned with that file.

### NewsGenie

- Base URL (default staging API) + `BALVOI_API_KEY` as `X-Api-Token`.  
- Articles: `GET …/podcast_articles` (path hardcoded).  
- LLM: `POST …/bedrock/prompt` (module `openai_client.py` — not the OpenAI SDK).  
- Empty windows → exit **2**. Pagination beyond `BALVOI_ARTICLE_LIMIT` is **UNVERIFIED**.

### S3 / public origin

- Optional: `BALVOI_EPISODE_S3_BUCKET` + AWS (or R2-compatible) credentials.  
- Production `PUBLIC_BASE_URL` / CDN product still an infra decision (AUDIT §10).

---

## 7. KNOWN GAPS

### Test coverage (newer modules)

Covered today among newer modules: **`publication_claim`**, **`publication_identity`**, **`settings`**.  

**No automated coverage** (tests archived or never added) for: `megaphone_once`, `megaphone_reconciliation`, `already_published_claim`, `episode_media`, `storage_paths`, `tts_chunking`, `hourly_report`, `logging_utils`, `edition_voice_validation`, `megaphone_discover`, `megaphone_episode_payload`, `megaphone_publication_result`.  

A **double-publish on retry would not be caught by tests**.

### Config lie vs English path

`config/episode-template.json` claims `aiPipeline.provider: amazon_bedrock` and task `newscaster_rewrite`. English story **bodies** pass through **verbatim** (`prepare_english_script`); primers/headlines use NewsGenie Bedrock proxy.

### Shipping defects (`_harvest.md` Priority 5)

- Article fetch: single page / `limit` only.  
- Demo articles: soft env flag (hard-blocked only when `BALVOI_ENV` is staging/production — see gate chain below).  
- **Silent under-publishing:** if the in-process scheduler runs with `PIPELINE_EDITIONS` unset, it defaults to **`en` only**, while a direct `python -m pipeline` / `settings.py` default is all eight. Operators who assume “scheduler = full matrix” will only publish English. **Set `PIPELINE_EDITIONS` explicitly for scheduler use.**  
- `megaphone_discover` still single-page `per_page=100` (main reconcile paginates).  
- Template rewrite claim unused.  
- No Dockerfile / IaC in repo.

### Demo-mode gate chain

**Settled:** a demo article **can** reach `publish_episode` on the hourly main path when `BALVOI_ALLOW_DEMO_ARTICLES=true` and `BALVOI_ENV` is unset (defaults to development; staging/production demo reject never runs). The only `run.py` override of the flag is inside the `--preview` branch:

```python
# pipeline/run.py — preview only
os.environ["BALVOI_ALLOW_DEMO_ARTICLES"] = "false"
```

Hourly main path does **not** force the flag off. Mitigations:

1. **`BALVOI_ENV=staging|production`:** `load_settings` rejects `BALVOI_ALLOW_DEMO_ARTICLES=true`.  
2. **Megaphone fail-fast:** when `MEGAPHONE_ENABLED=true`, `BALVOI_ENV` must be set explicitly (unset → ConfigurationError explaining the demo-block hole).  
3. **`--preview`:** forces demo/Megaphone/scheduler off.  
4. **Once-path:** live Megaphone create still requires `--confirm-live-publish`.

### Step 0 findings (2026-07-29)

1. **Demo mode:** Flag `BALVOI_ALLOW_DEMO_ARTICLES`, default **`false`**. See gate chain above. **Nothing** in `publish_episode` blocks demo-sourced stories once they are selected.  
2. **Scheduler editions:** Scheduler default `"en"` vs CLI/settings default `"en,es,pt,fr,de,ar,ru,tr"` — silent under-publishing risk (above).  
3. **German assets:** `config/assets.json` German entries reference only `*_GE.mp3`. On disk, **orphans** exist and are **not** referenced: `German/Ad 2/Ad 2_1_PT.mp3`, `Ad 2_2_PT.mp3`, `Ad 2_3_PT.mp3`.

### Tier 1.2 / lock interaction

`EditionLock` may reject a full retry with `DuplicateEditionError` (`already_published` / history duration ≥ `MIN_PUBLISH_DURATION_SECONDS`, default 600) **before** `publish_run`, so Tier 1.2 already-published **claim recovery does not engage** on that path.

---

## 8. OPEN DECISIONS FOR INFRA

### From AUDIT §10

1. Which Cloudflare (or other) product fronts `{PUBLIC_BASE_URL}/episodes/...`, and is Range/`audio/mpeg` preserved?  
2. Is `BALVOI_EPISODE_S3_BUCKET` the production origin, or Flask/local behind CDN?  
3. Confirm production `PUBLIC_BASE_URL` (not leftover `trycloudflare.com`).  
4. Commit scope for currently untracked Megaphone modules/tests/scripts.  
5. Ratify operator docs to **`:45`** or plan a return to **`:51`** (this handoff: **unratified / open**).  
6. Empty-hour exit **2** (no lookback) as production policy?  
7. Are example Megaphone UUIDs safe in the company repo?  
8. Status of any parallel tree at `C:\BalVoi_30`.  
9. Preview still requiring `OPENAI_API_KEY` while main LLM is Bedrock-via-NewsGenie — intentional?  
10. Missing `docs/python-migration.md` — drop (README already dropped the link).

### Owner decisions (AWS_FROM_HERE D1–D9)

| # | Decision |
|---|----------|
| D1 | Real production `PUBLIC_BASE_URL` hostname |
| D2 | Staging vs prod Megaphone network/podcast IDs |
| D3 | Megaphone sole distribution vs keep Flask RSS |
| D4 | Day-one languages: English only vs all eight |
| D5 | Private subnets + NAT vs public-IP Fargate |
| D6 | EFS for TTS cache vs regenerate from S3 |
| D7 | One AWS account (prefixes) vs separate staging/prod |
| D8 | Single task sleep until `:00` vs split build / `:00` publish |
| D9 | Live Megaphone create policy (`MEGAPHONE_CREATE_AS_DRAFT`) |

### Phase A duration

**Unknown — needs measurement.** If Phase A routinely exceeds ~15 minutes, the in-process sleep model publishes late (`publicationDelaySeconds`) or needs a split schedule.

### Cloudflare R2 for `BALVOI_EPISODE_S3_BUCKET` — NOT implemented, NOT tested

`pipeline/lib/episode_media.py` currently builds:

```python
client = boto3.client("s3", region_name=region) if region else boto3.client("s3")
```

Exact change needed for R2 (not in tree yet):

```python
client = boto3.client(
    "s3",
    endpoint_url="https://<accountid>.r2.cloudflarestorage.com",
    region_name="auto",
    # credentials: standard AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (R2 API tokens)
)
```

Prefer driving `endpoint_url` from an env var (e.g. `BALVOI_EPISODE_S3_ENDPOINT_URL`). Megaphone still needs a permanent public `PUBLIC_BASE_URL` (R2 custom domain / CDN), not the S3 API endpoint. **Not implemented. Not tested against R2.**

### `MIN_PUBLISH_DURATION_SECONDS` drift risk

`edition_lock` and `merge_audio` now read the env. Hardcoded **`600` function defaults** remain in `pipeline/stages/publish.py`, `pipeline/lib/megaphone_once.py`, and `pipeline/lib/megaphone_episode_payload.py` (callers usually override; still a drift risk if a new call site omits the arg). `duration_budget.MIN_PUBLISH_DURATION_SECONDS = 600` is a separate module constant.

---

## 9. AWS — PROPOSED, NOT IMPLEMENTED

There is **no Dockerfile, compose, or Terraform** in this repo (`AUDIT.md` §8).

```text
GitHub Actions → ECR
EventBridge (older docs say :51; code trigger is :45 — open) → ECS Fargate: python -m pipeline
  Secrets Manager | S3 artifacts | DynamoDB locks+publication | CloudWatch→SNS
S3 episodes → CloudFront → PUBLIC_BASE_URL (Megaphone mediaFileUrl)
Optional: ECS+ALB for Flask RSS, or RSS-to-S3 later
Do NOT use in-process Flask scheduler as the production timer
Prefer split build vs :00 publish if sleep is unsafe for billing/reliability
```

---

# APPENDIX — Decision records (verbatim from `_harvest.md`)

Inline **[DIVERGENCE]** notes preserved. Quoted text is not silently corrected.

## A1. Full text — `docs/production-hardening/MEGAPHONE_PUBLISH_TIMING_DECISION.md`

# Megaphone publish timing — inspection & decision

**Date:** 2026-07-28  
**Status:** Implemented — build-before-boundary / Megaphone-only-at-`:00` (draft-before-boundary still out of scope)  
**Related:** [AWS_FROM_HERE_TO_PRODUCTION.md](./AWS_FROM_HERE_TO_PRODUCTION.md), `pipeline/lib/megaphone_client.py`, `pipeline/lib/episode_media.py`, `balvoi/dates.py`

---

## Question

Does Megaphone “Create Episode” publish immediately? How should BalVoi:60 time Megaphone calls relative to the top-of-hour boundary (e.g. 19:00 UTC)?

---

## Verified behavior in this repository

### Create Episode vs “publish”

| `MEGAPHONE_CREATE_AS_DRAFT` | Create POST effect |
|-----------------------------|--------------------|
| **`true` (default)** | Creates a **draft** — not in the public RSS feed |
| **`false`** | Creates a **non-draft** episode with `pubdate` — live/publishable content |

Evidence:

- Payload always includes `draft` — `build_episode_create_payload` (`pipeline/lib/megaphone_client.py` ~187–197).
- Default draft — `create_as_draft()` (~50–59); `.env.example` sets `MEGAPHONE_CREATE_AS_DRAFT=true`.
- Main and once paths perform **one** Megaphone write: `POST …/episodes` inside `publish_episode` (~850–999). No follow-up publish call on those paths.
- Local note: create creates the episode object; `draft=true` = draft (`megaphone_episode_payload.py` ~331–334).

### Create-draft then publish separately

| Capability | Status |
|------------|--------|
| Create with `draft=true` | Yes — via `publish_episode` |
| Flip draft → published | Helper exists: `set_episode_draft(..., draft=False)` PUT (~1081–1137) |
| Used on main `run.py` / once path? | **No** — only `scripts/recreate_megaphone_episode.py` |
| Scheduled auto-publish at `pubdate` | **Not implemented / not verified** |

`pubdate` is set to the UTC boundary ISO string on create. The code does **not** treat that as Megaphone-scheduled go-live. **Do not assume scheduled publication.**

### What the main path does today

```text
TTS → merge → validate
  → wait_until_publication_boundary(boundary)   # sleep until :00
  → publish_episode(...)                        # POST create (draft per env)
  → publish_run(...)                            # local RSS / history
```

(`pipeline/run.py` — wait then Megaphone)

Megaphone create already happens **at/after** the top of the hour. Audio is generated before the wait. There is still **no S3 upload** step in app code.

**[DIVERGENCE vs AUDIT/code 2026-07-29]:** Optional S3 upload exists (`BALVOI_EPISODE_S3_BUCKET` / `episode_media.py`). Main path passes `draft=False` at boundary (not only “draft per env”). Trigger minute in code is `:45`, not the “Today … :51” sentence below.

---

## Target hourly timeline (product intent)

Example for **19:00 UTC** publication (article window `[17:45, 18:45)`):

```text
18:45
├─ Assign upcoming 19:00 UTC boundary
├─ Fetch [17:45, 18:45)
├─ Generate all eight editions
├─ Upload completed MP3s
└─ Prepare metadata and public URLs

19:00
├─ Publish each completed edition to Megaphone
├─ Save Megaphone episode ID
└─ Record actual publication time and delay
```

Today: window formula matches; scheduler still triggers at **:51** (not :45); S3 upload and delay metrics not done.

**[DIVERGENCE]:** Code trigger is `:45`; optional S3 exists. “Delay metrics” completeness not re-audited here.

---

## Decision (recommended policy)

### Primary (implemented)

**Keep Megaphone out of the build phase.**

1. Scheduler / processing trigger is UTC **:45** (`PROCESSING_TRIGGER_MINUTE`).
2. Before the boundary: fetch, generate editions, `prepare_public_episode_media` (optional S3 via `BALVOI_EPISODE_S3_BUCKET`, else local + URL), prepare title/summary metadata.
3. **At or after the publication boundary:** `wait_until_publication_boundary` (no-op if late), then `publish_episode(..., draft=False)`.
4. Persist Megaphone episode ID and `publicationDelaySeconds` = create_success_utc − boundary_utc.
5. **Do not** rely on `pubdate` alone to hold release until `:00`.
6. Duplicate creates prevented via publication result + claim + remote reconcile (unchanged mechanisms).

### Optional later (out of scope until explicitly requested)

Pre-create `draft=true` before the boundary, then at `:00` call `set_episode_draft(..., draft=False)`.

- API-capable via existing helper.
- **Not** wired into main/once paths today.
- Treat as **not production-ready** until integrated and tested.

### Explicit non-goals

- Megaphone “scheduled” publication via API (unverified in this repo).
- Calling Create Episode with `draft=false` during the pre-boundary build window.

---

## Implementation checklist (when approved)

- [x] Trigger / phase A at window close (**:45**)
- [x] Phase A: generate + prepare public media; no Megaphone create for live policy
- [x] Phase B at **:00**: wait if early → probe already done → `publish_episode(draft=False)` → save ID + delay
- [ ] Production CDN/`PUBLIC_BASE_URL` + optional `BALVOI_EPISODE_S3_BUCKET` (ops)
- [x] Draft-before-boundary + `set_episode_draft(false)` remains **out of scope**

---

## Open product choices

1. Move processing trigger from **:51** to **:45**, or keep :51 and only split Megaphone to :00?
2. Day-one: live create at :00 only, or also wire draft-then-undraft?
3. Record delay relative to boundary — which clock (task start vs Megaphone HTTP success)?

**[DIVERGENCE / INTERNAL CONFLICT]:** Checklist marks `:45` done while Open #1 still asks whether to move to `:45`. See Priority 0.

---

## A2. Full text — `docs/production-hardening/TIER1_1_UNIFY_PUBLICATION_OWNERSHIP.md`

# Tier 1.1 — Unify Publication Ownership Across Main and Once Paths

**Date:** 2026-07-27  
**Status:** Complete  
**Scope:** Publication ownership only (claims). No verifier, sidecar, resume-recovery, scheduler, AWS, or later Tier 1 work.

---

## 1. Implementation summary

The English once path (`pipeline.lib.megaphone_once.run_english_once`) now acquires, completes, and fails ownership through the same canonical module used by the hourly main path:

- `pipeline.lib.publication_claim.create_claim`
- `pipeline.lib.publication_claim.complete_claim`
- `pipeline.lib.publication_claim.fail_claim`

Both paths write `manifests/publication_claims/<key>.json` keyed by `PublicationIdentity.publication_key`.

Legacy once ownership (`megaphone_once.claim_publication` → `manifests/megaphone_once/*.claim`) no longer authorizes publication. The helper remains as a fail-closed stub. Existing `.claim` files on disk are ignored (not deleted, not migrated).

`publish_episode` was not changed.

**[DIVERGENCE]:** Later M2 work **did** change `publish_episode` (persist/reconcile/verify). Statement true *for Tier 1.1 scope date*, false as a description of “current publish_episode forever.”

---

## 2. Files changed

| Path | Change |
|------|--------|
| `pipeline/lib/megaphone_once.py` | Wire canonical claim lifecycle; retire live use of `claim_publication`; ignore legacy `.claim` in preflight |
| `tests/test_megaphone_once.py` | Seed/assert canonical claims; ignore legacy files |
| `tests/test_publication_identity_characterization.py` | Replace dual-claim separation assertions with shared-ownership freeze |
| `tests/test_publication_ownership_unify.py` | **New** — once integration + cross-path contention |
| `docs/production-hardening/TIER1_1_UNIFY_PUBLICATION_OWNERSHIP.md` | **New** — this report |

---

## 3. Old claim flow (before)

```text
run_english_once
  → (resume: skip_claim=True — ownership bypassed)
  → claim_publication(publication_key, run_id)
       writes manifests/megaphone_once/<key>.claim via O_EXCL
  → EditionLock.acquire
  → … generate / verify_public_audio_url / publish_episode / publish_run …
  → no complete/fail claim status machine
```

Main path already used `publication_claim` under `manifests/publication_claims/`. The two stores did not coordinate.

---

## 4. New canonical claim flow (after)

```text
run_english_once
  → PublicationIdentity.from_boundary | from_existing
  → create_claim(identity)     # same API/path as pipeline/run.py
       if not acquired → duplicate_blocked; no media verify; no publish_episode; no publish_run
  → EditionLock.acquire
  → … generate / verify_public_audio_url / confirm / publish_episode / publish_run …
  → complete_claim(identity)   # after successful publish_run (same success point as main)
  → on unexpected exception after acquire → fail_claim(identity)
```

Retryable controlled exits (no `--confirm-live-publish`, public URL failure, Megaphone create failure that returns without raising) leave the claim in `acquired` so the same `runId` can resume via `create_claim` idempotency.

---

## 5. Exact once resume behavior after the change

| Prior claim state | Same `runId` resume | Other `runId` |
|-------------------|---------------------|---------------|
| Missing | `create_claim` creates new `acquired` | creates if free |
| `acquired` (same run) | Idempotent success (`reason=idempotent`) | blocked |
| `completed` | **Fail closed** (`already_owned`) | blocked |
| `failed` | **Fail closed** (`already_owned`) | blocked |

- `retry_english_run` no longer sets `skip_claim=True`.
- `skip_claim` remains on the signature for compatibility but is ignored for ownership.
- Terminal claim recovery / lease takeover is **not** implemented (next task).

---

## 6. Cross-path contention behavior

Proven by `tests/test_publication_ownership_unify.py`:

1. P1-style `create_claim` for `publicationKey` succeeds → once `run_english_once` with a different `runId` is blocked before `publish_episode` / `publish_run`.
2. Once-style `create_claim` succeeds → P1-style `create_claim` with a different `runId` is blocked.
3. Different languages / boundaries do not collide.

---

## 7. Legacy `.claim` file treatment

- Not read for ownership or preflight blocking.
- Not deleted automatically.
- Not converted into canonical claims.
- `claim_publication(...)` raises `DuplicateEditionError` explaining retirement.
- `_migrate_legacy_claim` is a documented no-op.
- `StoragePaths.claim_path` still describes the legacy path layout for path tests.

---

## 8. New and updated tests

| Suite | Coverage |
|-------|----------|
| `tests/test_publication_ownership_unify.py` | Acquire/complete; no legacy `.claim`; foreign claim blocks publish; same-run idempotent; exception → failed; resume fail-closed on failed claim; P1↔P2 contention; language/boundary isolation; legacy file ignored; confirm-stop leaves `acquired`; retired helper raises |
| `tests/test_megaphone_once.py` | Seeds canonical claims; asserts completed claim + no `.claim` on success; legacy key retry ignores old `.claim` |
| `tests/test_publication_identity_characterization.py` | Shared canonical claim freeze; once source uses `create_claim(identity)` before `publish_fn(` |
| `tests/test_publication_claim.py` | Unchanged unit coverage of claim module |

---

## 9. Full test results

Focused:

```text
python -m pytest tests/test_megaphone_once.py tests/test_publication_claim.py \
  tests/test_publication_ownership_unify.py \
  tests/test_publication_identity_characterization.py -v --tb=no
→ 80 passed
```

Complete:

```text
python -m pytest tests/ -v --tb=no
→ 397 passed, 2 warnings
```

Warnings are existing DeprecationWarning for legacy publication-key normalize reads in once key tests.

No real Megaphone network requests in these tests (mocked `publish_episode_impl` / client tests).

---

## 10. Known limitations

1. **No failed-claim recovery** — if an unexpected exception marks the claim `failed`, same-run resume is fail-closed until a later recovery task.
2. **Retryable once exits leave `acquired`** — intentional so confirm/public-URL retries work; differs from always-`fail_claim` on every early return.
3. **Dual episode-ID sidecars remain** — `megaphone_episode.json` / once `state.json` unchanged (out of scope).
4. **Dual media verifiers remain** — `verify_public_audio_url` still used by once (out of scope).
5. **`EditionLock` remains separate** from publication claims (unchanged).

---

## 11. Rollback plan

1. Revert `pipeline/lib/megaphone_once.py` to restore `claim_publication` + `skip_claim=True` on resume.
2. Revert related tests and this doc.
3. Legacy `.claim` files on disk (if any) would again become authoritative for once only — main would still use `publication_claims/` (dual-stack regression).

---

## 12. Confirmation — out of scope not implemented

Not changed in this task:

- `verify_public_audio_url` / `verify_media_file_url` semantics  
- `megaphone_episode.json` sidecar  
- once `state.json` schema  
- publication-result schema  
- remote reconciliation / `publish_episode` control flow  
- article fetching, scheduler defaults, hardcoded 600s rule  
- claim expiry / takeover / failed-claim retry state machine  
- AWS / S3 / DynamoDB / Docker / retention / assets / Megaphone UUIDs / secrets  

**Stop:** Tier 1.1 publication ownership unification complete.

**[DIVERGENCE vs older DR/invariants docs]:** “Main path no claim file” is false after this work; claims are shared. Failed-claim reopen still absent (limitation #1 still relevant).

---

## A3. Full text — `docs/production-hardening/TIER1_2_PREVENT_FALSE_FAILED_CLAIMS.md`

# Tier 1.2 — Prevent Successful Publications from Becoming FAILED Claims

**Date:** 2026-07-27  
**Status:** Complete  
**Scope:** Recover acquired claims when `publish_run` reports structured `already_published` and a valid canonical Megaphone publication result exists. No leases, TTLs, failed-claim reopening, or EditionLock changes.

---

## 1. Root cause confirmed

After Megaphone create succeeds and the canonical result is saved under `manifests/megaphone_publications/`, a later `publish_run` rejection with `already_published` was treated as a generic exception. Broad handlers in `pipeline/run.py` and `pipeline/lib/megaphone_once.py` called `fail_claim`, marking a successful publication as terminal `FAILED`.

---

## 2. Files changed

| Path | Change |
|------|--------|
| `pipeline/errors.py` | `PublishRejectedError` gains optional structured `reason` |
| `pipeline/stages/publish.py` | Raise `reason="already_published"` |
| `pipeline/lib/already_published_claim.py` | **New** — shared detection + canonical proof load |
| `pipeline/run.py` | Main-path recovery: `complete_claim` instead of `fail_claim` |
| `pipeline/lib/megaphone_once.py` | Once-path recovery: same decision |
| `tests/test_already_published_claim.py` | **New** — M1/M2/O1 + fail-closed cases |
| `docs/production-hardening/TIER1_2_PREVENT_FALSE_FAILED_CLAIMS.md` | This report |

---

## 3. Implementation summary

Shared helper `try_recover_already_published_claim(identity, err)`:

1. Accepts only `PublishRejectedError` with `reason == "already_published"`.
2. Loads/validates canonical proof via `load_publication_result` / `validate_publication_result`.
3. Returns episode ID proof or `None` (fail closed).
4. Does **not** mutate claims — callers call `complete_claim`.

---

## 4. Structured already_published detection

```python
PublishRejectedError("Publication rejected: already_published", reason="already_published")
```

Detection uses `err.reason == "already_published"`, not message-string matching.  
`DuplicateEditionError("already_published"|"duplicate_blocked")` and bare message strings are **not** treated as success.

---

## 5. Canonical success validation

Proof path: `manifests/megaphone_publications/<publication-key>.json`

Must match `PublicationIdentity`, include a non-empty `megaphoneEpisodeId`, and pass existing publication-result validation. Sidecars, `state.json`, and exception text alone are not used.

---

## 6. Main-path behavior

In `_process_language` exception handler: if claim held and recovery returns proof and `complete_claim` succeeds → log already-published success, `stage=published`, return `True`. Otherwise preserve `fail_claim` + failure status.

---

## 7. Once-path behavior

In outer `except Exception`: same recovery; on success set `published`, return code `0`. Missing/invalid proof → existing `fail_claim` path.

---

## 8. Tests added

`tests/test_already_published_claim.py` covers:

- Structured reason detection and `publish_run` emission
- Missing / malformed / missing episode ID / wrong-identity proof
- Claim ownership mismatch
- Main M1 + M2 (retry completes; no second create)
- Once O1
- Fail closed without proof
- `duplicate_blocked` not success
- True Megaphone rejection and media verification errors unchanged

---

## 9–10. Test results

**Focused** (`tests/test_already_published_claim.py`): **17 passed**

**Related** (ownership / claim / publication-result / reconciliation / once / publish): **87 passed**, 2 DeprecationWarnings (legacy publication key)

**Full suite** (`python -m pytest tests/ -v --tb=no`): **414 passed**, 2 DeprecationWarnings (legacy publication key in `megaphone_client.py`)

No failures.

---

## 11. Remaining limitations

- **EditionLock / history gate:** A full pipeline retry after history is written may still stop at `EditionLock.acquire` (`DuplicateEditionError`) before reaching `publish_run`. This fix recovers only the proven `publish_run` `PublishRejectedError(reason=already_published)` path (e.g. when the edition runner is already past lock acquisition). Completing claims solely because the lock rejected with `already_published` was explicitly out of scope (Case E).
- Claim leases, TTLs, stale takeover, and failed-claim reopening were not implemented.
- Megaphone client string `Megaphone upload rejected: already_published` (no `reason=`) remains fail-closed.

---

## 12. Confirmation

No lease, TTL, stale-claim takeover, failed-claim reopen, payload, media-verification, scheduler, AWS, or sidecar-as-proof work was included.

**[DIVERGENCE]:** Limitation about EditionLock stopping retries before claim recovery remains a live ops nuance (AUDIT: history/`already_published` interactions). Do not confuse with “Megaphone ID never stored” — proof path above is the durable create record.

---

*End of harvest. No files moved or deleted.*
