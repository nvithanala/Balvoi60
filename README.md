# BalVoi:60

A little radio station that never sleeps. Every hour it grabs the latest world news, rewrites it like a real anchor would read it, runs it through eight language editions, stitches in the ads, and hands you a finished episode. No human in the loop.

One production run an hour, covering whatever broke in the previous **60 minutes**.

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

Each edition rotates through three anchor voices over the run, so an episode doesn't feel like one robot talking at you the whole time.

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

The canonical scheduler **processes at UTC :51** and **publishes at the next
:00**. A run started at 10:51 UTC freezes the article window and builds all
languages for the 11:00 UTC publication boundary:

```powershell
$env:SCHEDULER_ENABLED = "true"; python -m server
```

Or run the scheduler on its own: `python -m server.scheduler`. Atomic
boundary/language locks prevent scheduled and manual runs from publishing the
same edition twice.

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
2. **Select once** — freeze one ordered story set for all languages from the
   hourly ownership window. A publication at 11:00 UTC owns `[09:51, 10:51)`.
   Processing begins at 10:51 so the window can close before fetch.
3. **Transform** — OpenAI rewrites each story in newscaster voice (English base), then localizes/translates per edition.
4. **Assemble** — build the 13-segment episode: intro, headlines, stories, ad breaks, outro.
5. **Synthesize + merge** — ElevenLabs does the TTS, ffmpeg merges it with the pre-rendered ads.
6. **Publish** — at the `:00` publication boundary, write validated MP3/manifest
   metadata and upload ready languages independently (failures do not block others).

The `server/` then reads those manifests to build each edition's RSS feed and pages — it never touches the pipeline directly except to trigger it on schedule.

## Runtime publication rule

BalVoi:60 describes the hourly cadence, not episode length. There is no target
or maximum runtime. An edition is publishable only when its merged audio is at
least 600 seconds (`MIN_PUBLISH_DURATION_SECONDS`). The selector never repeats
stories or adds filler to reach that threshold.

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
SCHEDULER_ENABLED=true                    # process at UTC :51; publish at next :00
PIPELINE_EDITIONS=en,es,pt,fr,de,ar,ru,tr
BALVOI_ARTICLE_WINDOW_MINUTES=60          # fixed hourly ownership width
MIN_PUBLISH_DURATION_SECONDS=600
LANGUAGE_WORKER_CONCURRENCY=4
TRANSLATION_CONCURRENCY=4
TTS_REQUEST_CONCURRENCY=3
MERGE_CONCURRENCY=2
```

A few things worth knowing:

- Articles come from the **NewsGenie API engine** (`api.staging.newsgenie.ai`, MongoDB-backed). The public BalVoi site URL is only used for story links and as a scrape fallback.
- Default article endpoint is `GET /articles` with an `X-Api-Token: <domain JWT>` header. If yours lives somewhere else, set `BALVOI_API_ARTICLES_PATH`.
- The article ownership window is exactly 60 minutes and gap-free. The
  environment value is validated as `60`.
- Stories that aired recently are skipped. The cooldown is tunable via
  `BALVOI_STORY_COOLDOWN_MINUTES` (default `360`; `0` disables the historical
  exclusion). The pipeline does not relax cooldown to fill an episode.
- `STORAGE_PATH` sets where episodes, manifests, cache, and the pipeline lock file live. Pipeline, server, and scheduler all read the same value.
- No API keys handy? Run the pipeline with `--dry-run` (or `DRY_RUN=true`) and the synth step is skipped so you can still exercise everything else.
- `PUBLIC_BASE_URL` should be set in production so podcast players get absolute MP3 URLs; locally it falls back to the request host.
