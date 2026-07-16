"""BalVoi:30 Flask service — podcast feeds, server-rendered pages, audio."""

from __future__ import annotations

import os

from dotenv import load_dotenv

from balvoi.dates import format_display_datetime
from balvoi.paths import ROOT

load_dotenv(ROOT / ".env", override=False)

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    render_template,
    request,
    send_from_directory,
)

from . import data
from .feed import build_feed


def _format_duration(seconds) -> str:
    sec = int(seconds or 0)
    minutes, secs = divmod(sec, 60)
    return f"{minutes}:{secs:02d}"


def _theme(edition: dict | None) -> dict:
    colors = (edition or {}).get("colors") or {
        "primary": "#0a3d91",
        "secondary": "#c0c7d1",
        "accent": "#152238",
    }
    return colors


def _base_url() -> str:
    configured = os.environ.get("PUBLIC_BASE_URL")
    if configured:
        return configured.rstrip("/")
    return request.url_root.rstrip("/")


def create_app() -> Flask:
    app = Flask(__name__)
    app.jinja_env.filters["duration"] = _format_duration
    app.jinja_env.filters["datetime"] = format_display_datetime

    @app.context_processor
    def inject_globals():
        return {"brand": data.master_brand(), "editions": data.editions()}

    @app.route("/")
    def index():
        overview = []
        latest = data.latest_map()
        for edition in data.editions():
            overview.append({"edition": edition, "latest": latest.get(edition["slug"])})
        return render_template("index.html", overview=overview, theme=_theme(None))

    @app.route("/<slug>")
    def edition_page(slug: str):
        edition = data.edition_by_slug(slug)
        if not edition:
            abort(404)
        latest = data.latest_for(slug)
        recent = [e for e in data.history_for(slug) if not latest or e.get("id") != latest.get("id")]
        return render_template(
            "edition.html",
            edition=edition,
            latest=latest,
            recent=recent[:12],
            active_slug=slug,
            theme=_theme(edition),
        )

    @app.route("/episode/<episode_id>")
    def episode_page(episode_id: str):
        episode = data.episode_by_id(episode_id)
        if not episode:
            abort(404)
        edition = data.edition_by_slug(episode.get("slug", "")) or {}
        return render_template(
            "episode.html",
            edition=edition,
            episode=episode,
            active_slug=episode.get("slug"),
            theme=_theme(edition),
        )

    @app.route("/feed/<slug>.xml")
    def feed(slug: str):
        edition = data.edition_by_slug(slug)
        if not edition:
            abort(404)
        episodes = data.history_for(slug)
        xml = build_feed(edition, episodes, _base_url(), data.audio_size)
        return Response(xml, mimetype="application/rss+xml")

    @app.route("/episodes/<path:subpath>")
    def episode_audio(subpath: str):
        return send_from_directory(data.episodes_dir(), subpath, conditional=True)

    @app.route("/api/health")
    def health():
        return jsonify({"ok": True, **data.status()})

    @app.errorhandler(404)
    def not_found(_err):
        return render_template("404.html", theme=_theme(None)), 404

    return app


def main() -> None:
    app = create_app()
    if os.environ.get("SCHEDULER_ENABLED", "").lower() == "true":
        from .scheduler import start_scheduler

        start_scheduler()
    port = int(os.environ.get("PORT", "3001"))
    print(f"BalVoi:30 server http://localhost:{port}")
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
