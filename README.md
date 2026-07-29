# BalVoi:60

Hourly multi-language news podcast pipeline. It fetches articles from NewsGenie for a fixed UTC ownership window, builds up to eight language editions (TTS + prerecorded assets), and publishes audio for Megaphone and/or local RSS.

**Trigger minute (open):** Production code starts Phase A at UTC **`:45`** (`PROCESSING_TRIGGER_MINUTE` in `balvoi/dates.py`). Product docs never closed `:45` vs `:51`; treat the minute as **implemented but unratified**. See [docs/HANDOFF.md](docs/HANDOFF.md).

## Requirements

- Python **>= 3.11**
- **ffmpeg** / **ffprobe** on `PATH` (or set `FFMPEG_PATH`)
- API keys as needed: `BALVOI_API_KEY` (NewsGenie articles + `POST /bedrock/prompt`), `ELEVENLABS_API_KEY`, and Megaphone credentials when enabled

## Install

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
# or: pip install -e ".[dev]"
cp .env.example .env   # then edit secrets
```

## Run

| Entry | Command |
|-------|---------|
| Pipeline | `python -m pipeline` or `balvoi-pipeline` |
| Server (Flask RSS/media) | `python -m server` or `balvoi-server` |
| Scheduler only | `python -m server.scheduler` (or `server/scheduler.py`) |

Useful flags: `--editions en`, `--all-languages`, `--dry-run`, `--preview`, `--boundary <ISO-UTC>`, `--run-id <id>`.

**Edition default mismatch:** CLI / `settings` default is all eight (`en,es,pt,fr,de,ar,ru,tr`). The in-process scheduler defaults `PIPELINE_EDITIONS` to **`en` only** if unset. Set `PIPELINE_EDITIONS` explicitly for production.

## Repo layout

| Path | Role |
|------|------|
| `balvoi/` | Shared dates, paths, edition helpers |
| `config/` | Editions, segments, assets, episode template |
| `pipeline/` | Hourly factory (fetch → publish) |
| `server/` | Flask RSS/pages + optional scheduler |
| `scripts/` | Operator / Megaphone helpers |
| `storage/` | Runtime state, episodes, caches (mostly gitignored) |
| `tests/` | Pytest suite |
| `docs/` | `AUDIT.md`, `HANDOFF.md` (active); `_archived/` (history) |
| `Arabic/`…`Turkish/` | Prerecorded MP3 trees (gitignored) |
| `.github/` | CI |

## Tests and lint

```bash
python -m pytest
python -m ruff check .
```

## Docs

- **[docs/HANDOFF.md](docs/HANDOFF.md)** — operator handoff (execution model, env, recovery, gaps)
- **[docs/AUDIT.md](docs/AUDIT.md)** — read-only code audit (what the code does)

Copy `.env.example` for variable names; prefer the env table in HANDOFF (sourced from `pipeline/lib/settings.py`).
