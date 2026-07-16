# BalVoi:30

A little radio station that never sleeps. Every half hour it grabs the latest world news, rewrites it like a real anchor would read it, runs it through eight language editions, stitches in the ads, and hands you a finished ~30-minute episode. No human in the loop.

Two production runs an hour, at **:25** and **:55**, each covering whatever broke in the previous 30 minutes.

It's a podcast, not an app — so the whole thing is **Python**: a pipeline that produces episodes, and a small Flask service that publishes them as standard RSS feeds (subscribe in any podcast player) plus a few plain server-rendered pages.

> It used to have a Node/Express backend and a React frontend. Those were removed in favor of the Python-only setup — see [docs/python-migration.md](docs/python-migration.md) for what changed and why.

## The eight editions

Same brand, different voice per market. No flags, just colors. Each edition also has its own set of source countries it covers.

| Slug | Edition | Language | Home city | Feed |
|------|---------|----------|-----------|------|
| `en` | Five Eyes | English | New York City | `/feed/en.xml` |
| `es` | Latinoamérica | Spanish | Mexico City | `/feed/es.xml` |
| `pt` | Brasil | Portuguese | São Paulo | `/feed/pt.xml` |
| `fr` | Monde Francophone | French | Paris | `/feed/fr.xml` |
| `de` | Mitteleuropa | German | Berlin | `/feed/de.xml` |
| `ar` | الشرق الأوسط | Arabic | Dubai | `/feed/ar.xml` |
| `ru` | Россия и Евразия | Russian | Riga | `/feed/ru.xml` |
| `tr` | Türkiye ve Kafkasya | Turkish | Istanbul | `/feed/tr.xml` |

Each edition rotates through three anchor voices over the run, so a 30-minute episode doesn't feel like one robot talking at you for half an hour.

## Getting it running

You'll need Python and **ffmpeg** on your PATH (ffmpeg is what actually glues the audio segments together — without it you only get a dry run).

```powershell
# 1. Install the package (runtime + dev tools)
pip install -e ".[dev]"

# 2. Configure
copy .env.example .env
# then edit .env and add OPENAI_API_KEY + ELEVENLABS_API_KEY

# 3. Turn the spreadsheet spec into config JSON
python scripts/load-spec.py

# 4. Produce one English episode (add --dry-run to skip TTS / API keys)
python -m pipeline --editions en

# 5. Serve the feeds + pages
python -m server
```

Then open:

- Site: http://localhost:3001
- English edition: http://localhost:3001/en
- English RSS feed: http://localhost:3001/feed/en.xml

### Producing episodes on a schedule

The pipeline runs at **:25** and **:55** when you start the server with the scheduler enabled:

```powershell
$env:SCHEDULER_ENABLED = "true"; python -m server
```

Or run the scheduler on its own: `python -m server.scheduler`. Either way it skips a cycle if a run is already in progress (`storage/.pipeline.lock`).

You can also just run the pipeline by hand: `python -m pipeline --editions en` (one edition) or leave `--editions` off to do all eight. Console scripts `balvoi-pipeline` and `balvoi-server` are available after `pip install -e .`.

## Development

After `pip install -e ".[dev]"`:

```powershell
# Run tests
python -m pytest

# Lint (imports, pyupgrade, etc.)
python -m ruff check .

# Auto-fix safe lint issues
python -m ruff check . --fix

# Format (optional)
python -m ruff format .
```

Integration tests (network/API/ffmpeg) can be marked with `@pytest.mark.integration` when added.

CI runs on push/PR to `main` or `master`: Ruff lint plus pytest on Python 3.11 and 3.12 (see `.github/workflows/ci.yml`).

## Where things live

```
config/        editions (+ source countries), segments, assets — generated from the xlsx + PPTX
{Language}/    pre-rendered MP3s (Ad 1, Ad 2, "we'll be right back") per market
pipeline/      the Python production pipeline (the interesting part)
server/        Flask service: RSS feeds, pages, and audio serving
storage/       output: episodes, manifests, the run lock
scripts/       one-off helpers like load-spec
docs/          project notes (e.g. the Python-only migration)
```

## How an episode gets made

Every cycle runs the same six steps (see `pipeline/run.py` if you want the gory details):

1. **Fetch** — pull the latest articles from the NewsGenie API.
2. **Select** — breaking news first, then anything from the last 30 minutes. If the window's empty, fall back to the latest available.
3. **Transform** — OpenAI rewrites each story in newscaster voice (English base), then localizes/translates per edition.
4. **Assemble** — build the 13-segment episode: intro, headlines, stories, ad breaks, outro.
5. **Synthesize + merge** — ElevenLabs does the TTS, ffmpeg merges it with the pre-rendered ads.
6. **Publish** — write the MP3 and manifest into `storage/`.

The `server/` then reads those manifests to build each edition's RSS feed and pages — it never touches the pipeline directly except to trigger it on schedule.

## Staying under 30 minutes

The whole thing is budgeted to land **under 30 minutes**. Roughly:

| Component | ~Duration |
|-----------|-----------|
| Intro + dynamic city/time | ~55s |
| Headlines | ~45–90s |
| Fillers (transitions, welcome-backs, outro) | ~35s |
| Ad break 1 (right back + Ad 1 + welcome back) | ~45s |
| Ad break 2 (right back + Ad 2 + welcome back) | ~60s |
| **Fixed overhead** | **~4 min** |
| **Stories** (9–10 × ~2.5 min) | **~24 min** |
| **Total** | **~28 min** |

If a finished episode somehow blows past the 30-minute cap, the pipeline warns you so you can trim the story count or tighten the scripts.

## Configuration

Everything lives in `.env`:

```
BALVOI_SITE_URL=https://staging.balvoi.com
BALVOI_API_URL=https://api.staging.newsgenie.ai
BALVOI_API_KEY=your_key_here
OPENAI_API_KEY=...
ELEVENLABS_API_KEY=...

# server
PORT=3001
STORAGE_PATH=storage                       # shared by pipeline, server, and scheduler
PUBLIC_BASE_URL=https://your-host        # used for absolute feed/enclosure URLs
SCHEDULER_ENABLED=true                    # run the pipeline at :25 and :55
PIPELINE_EDITIONS=en                      # editions the scheduler produces
```

A few things worth knowing:

- Articles come from the **NewsGenie API engine** (`api.staging.newsgenie.ai`, MongoDB-backed). The public BalVoi site URL is only used for story links and as a scrape fallback.
- Default article endpoint is `GET /articles` with an `X-Api-Token: <domain JWT>` header. If yours lives somewhere else, set `BALVOI_API_ARTICLES_PATH`.
- The 30-minute lookback window is tunable via `BALVOI_ARTICLE_WINDOW_MINUTES`.
- Stories that aired in a recent cycle are skipped so episodes don't repeat. The cooldown is tunable via `BALVOI_STORY_COOLDOWN_MINUTES` (default `360` = 6 hours; set to `0` to allow repeats). If every available story is still on cooldown, the pipeline allows repeats for that one cycle rather than producing an empty episode.
- `STORAGE_PATH` sets where episodes, manifests, cache, and the pipeline lock file live. Pipeline, server, and scheduler all read the same value.
- No API keys handy? Run the pipeline with `--dry-run` (or `DRY_RUN=true`) and the synth step is skipped so you can still exercise everything else.
- `PUBLIC_BASE_URL` should be set in production so podcast players get absolute MP3 URLs; locally it falls back to the request host.
