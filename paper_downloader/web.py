"""Flask web UI for paper tracker."""

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request, Response, stream_with_context

from .arxiv import ArxivClient
from .config import load_config, save_config

log = logging.getLogger(__name__)


def create_app(db_path: Path, config_path: Path | None = None):
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.config["DB_PATH"] = db_path
    app.config["CONFIG_PATH"] = config_path

    def get_db():
        if "db" not in g:
            from .db import PaperDB
            g.db = PaperDB(app.config["DB_PATH"])
        return g.db

    @app.teardown_appcontext
    def close_db(exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def get_config():
        return load_config(app.config["CONFIG_PATH"], create_if_missing=True)

    @app.errorhandler(Exception)
    def handle_error(e):
        import traceback
        traceback.print_exc()
        message = str(e)
        if isinstance(e, FileNotFoundError):
            code = 404
        elif isinstance(e, ValueError):
            code = 400
        else:
            code = 500
        return jsonify({"error": message}), code

    # --- Pages ---

    @app.route("/favicon.ico")
    def favicon():
        return "", 204

    @app.route("/")
    def index():
        return render_template("index.html")

    # --- Paper API ---
    # Fixed-path routes MUST come before <path:arxiv_id> wildcard routes.

    @app.route("/api/papers")
    def list_papers():
        db = get_db()
        papers, total = db.query_papers(
            unread_only=request.args.get("unread_only") == "true",
            favorite_only=request.args.get("favorite_only") == "true",
            author=request.args.get("author") or None,
            category=request.args.get("category") or None,
            tag=request.args.get("tag") or None,
            sort_by=request.args.get("sort", "published"),
            sort_order=request.args.get("order", "desc"),
            limit=int(request.args.get("limit", 50)),
            offset=int(request.args.get("offset", 0)),
        )
        return jsonify({"papers": papers, "total": total})

    @app.route("/api/papers/bulk-read", methods=["POST"])
    def bulk_read():
        db = get_db()
        ids = request.json.get("arxiv_ids", [])
        db.mark_read_bulk(ids)
        return jsonify({"ok": True, "count": len(ids)})

    @app.route("/api/papers/bulk-unread", methods=["POST"])
    def bulk_unread():
        db = get_db()
        ids = request.json.get("arxiv_ids", [])
        db.mark_unread_bulk(ids)
        return jsonify({"ok": True, "count": len(ids)})

    @app.route("/api/papers/catchup", methods=["POST"])
    def catchup():
        db = get_db()
        db.mark_all_read()
        return jsonify({"ok": True})

    # Wildcard routes after fixed paths
    @app.route("/api/papers/<path:arxiv_id>")
    def get_paper(arxiv_id):
        db = get_db()
        row = db.get_paper(arxiv_id)
        if not row:
            return jsonify({"error": "not found"}), 404
        paper = dict(row)
        paper["tags"] = db.get_paper_tags(arxiv_id)
        return jsonify(paper)

    @app.route("/api/papers/<path:arxiv_id>/read", methods=["POST"])
    def mark_read(arxiv_id):
        db = get_db()
        db.mark_read(arxiv_id)
        return jsonify({"ok": True})

    @app.route("/api/papers/<path:arxiv_id>/unread", methods=["POST"])
    def mark_unread(arxiv_id):
        db = get_db()
        db.mark_unread(arxiv_id)
        return jsonify({"ok": True})

    @app.route("/api/papers/<path:arxiv_id>/favorite", methods=["POST"])
    def toggle_favorite(arxiv_id):
        db = get_db()
        new_state = db.toggle_favorite(arxiv_id)
        if new_state:
            _queue_tex_download(arxiv_id, app.config["DB_PATH"], app.config["CONFIG_PATH"])
        return jsonify({"favorite": new_state})

    @app.route("/api/papers/<path:arxiv_id>/tex", methods=["GET"])
    def get_tex_source(arxiv_id):
        db = get_db()
        source = db.get_tex_source(arxiv_id)
        if source is None:
            return jsonify({"error": "no source available"}), 404
        return jsonify({"arxiv_id": arxiv_id, "tex_source": source})

    @app.route("/api/papers/<path:arxiv_id>/notes", methods=["PUT"])
    def update_notes(arxiv_id):
        db = get_db()
        notes = request.json.get("notes", "")
        db.set_notes(arxiv_id, notes)
        return jsonify({"ok": True})

    @app.route("/api/papers/<path:arxiv_id>/tags", methods=["POST"])
    def add_tag(arxiv_id):
        db = get_db()
        tag = request.json.get("tag", "").strip()
        if not tag:
            return jsonify({"error": "empty tag"}), 400
        db.tag_paper(arxiv_id, tag)
        return jsonify({"ok": True, "tags": db.get_paper_tags(arxiv_id)})

    @app.route("/api/papers/<path:arxiv_id>/tags/<tag>", methods=["DELETE"])
    def remove_tag(arxiv_id, tag):
        db = get_db()
        db.untag_paper(arxiv_id, tag)
        return jsonify({"ok": True, "tags": db.get_paper_tags(arxiv_id)})

    # --- Tags / Filters ---

    @app.route("/api/tags")
    def list_tags():
        db = get_db()
        return jsonify({"tags": db.get_all_tags()})

    @app.route("/api/stats")
    def stats():
        db = get_db()
        return jsonify(db.get_stats())

    @app.route("/api/filters")
    def filters():
        db = get_db()
        return jsonify({
            "authors": db.get_distinct_authors(),
            "categories": db.get_distinct_categories(),
            "tags": db.get_all_tags(),
        })

    # --- Config / Author management ---

    @app.route("/api/config")
    def get_config_api():
        config = get_config()
        return jsonify({
            "mailto": config.get("mailto", ""),
            "authors": config.get("authors", []),
            "lookback_days": config.get("lookback_days", 7),
        })

    @app.route("/api/config", methods=["PUT"])
    def update_config():
        config = get_config()
        data = request.json
        if "mailto" in data:
            config["mailto"] = data["mailto"].strip()
        if "lookback_days" in data:
            config["lookback_days"] = max(1, int(data["lookback_days"]))
        save_config(config, app.config["CONFIG_PATH"])
        return jsonify({"ok": True})

    @app.route("/api/authors")
    def list_authors():
        config = get_config()
        return jsonify({"authors": config.get("authors", [])})

    @app.route("/api/authors", methods=["POST"])
    def add_author():
        name = request.json.get("name", "").strip()
        if not name:
            return jsonify({"error": "empty name"}), 400
        config = get_config()
        if name not in config["authors"]:
            config["authors"].append(name)
            save_config(config, app.config["CONFIG_PATH"])
        return jsonify({"authors": config["authors"]})

    @app.route("/api/authors/<name>", methods=["DELETE"])
    def remove_author(name):
        config = get_config()
        config["authors"] = [a for a in config["authors"] if a != name]
        save_config(config, app.config["CONFIG_PATH"])
        return jsonify({"authors": config["authors"]})

    # --- Fetch (SSE) ---

    @app.route("/api/fetch")
    def fetch_papers():
        config = get_config()
        if not config.get("mailto"):
            return jsonify({"error": "Set your email in Settings before fetching (required by arxiv API)."}), 400
        if not config.get("authors"):
            return jsonify({"error": "Add at least one author in Settings before fetching."}), 400
        lookback_days = int(request.args.get("days", config["lookback_days"]))
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        authors = config["authors"]
        mailto = config["mailto"]
        max_results = config["max_results_per_author"] * len(authors)
        db_path = app.config["DB_PATH"]

        def generate():
            from .db import PaperDB
            client = ArxivClient(mailto=mailto)
            db = PaperDB(db_path)
            try:
                yield f"data: {json.dumps({'type': 'progress', 'message': f'Fetching papers for {len(authors)} authors...'})}\n\n"
                try:
                    papers = client.search_authors(authors, max_results=max_results)
                    total_new = 0
                    for paper in papers:
                        if paper.published >= cutoff:
                            if db.upsert_paper(paper):
                                total_new += 1
                    yield f"data: {json.dumps({'type': 'result', 'found': len(papers), 'new': total_new})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'total_new': total_new})}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'total_new': 0})}\n\n"
            finally:
                db.close()

        return Response(stream_with_context(generate()), mimetype="text/event-stream")

    # --- One-off search ---

    @app.route("/api/search")
    def search_papers():
        author = request.args.get("author", "").strip()
        if not author:
            return jsonify({"error": "author required"}), 400
        days = int(request.args.get("days", 30))
        max_results = int(request.args.get("max_results", 50))
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        config = get_config()
        if not config.get("mailto"):
            return jsonify({"error": "Set your email in Settings first (required by arxiv API)."}), 400
        client = ArxivClient(mailto=config["mailto"])
        papers = client.search_author(author, max_results=max_results)
        results = [
            {
                "arxiv_id": p.arxiv_id,
                "title": p.title,
                "authors": ", ".join(p.authors),
                "abstract": p.abstract,
                "published": p.published.isoformat(),
                "categories": ", ".join(p.categories),
                "pdf_url": p.pdf_url,
                "abs_url": p.abs_url,
            }
            for p in papers
            if p.published >= cutoff
        ]
        return jsonify({"results": results})

    @app.route("/api/search/save", methods=["POST"])
    def save_search_results():
        from .arxiv import Paper as PaperData
        db = get_db()
        items = request.json.get("papers", [])
        saved = 0
        for item in items:
            paper = PaperData(
                arxiv_id=item["arxiv_id"],
                title=item["title"],
                authors=[a.strip() for a in item["authors"].split(",")],
                abstract=item.get("abstract", ""),
                published=datetime.fromisoformat(item["published"]),
                updated=datetime.fromisoformat(item.get("updated", item["published"])),
                categories=[c.strip() for c in item.get("categories", "").split(",") if c.strip()],
                pdf_url=item.get("pdf_url", ""),
                abs_url=item.get("abs_url", ""),
            )
            if db.upsert_paper(paper):
                saved += 1
        return jsonify({"ok": True, "saved": saved})

    return app


def _queue_tex_download(arxiv_id: str, db_path: Path, config_path: Path | None):
    """Download TeX source in a background thread."""
    def _download():
        from .db import PaperDB
        db = PaperDB(db_path)
        try:
            config = load_config(config_path, create_if_missing=True)
            if not config.get("mailto"):
                log.warning("Skipping TeX download for %s: no mailto configured", arxiv_id)
                return
            client = ArxivClient(mailto=config["mailto"])
            result = client.fetch_tex_source(arxiv_id)
            if result.ok:
                db.set_tex_source(arxiv_id, result.source)
                tex_dir = db_path.parent / "tex"
                tex_dir.mkdir(parents=True, exist_ok=True)
                safe_id = arxiv_id.replace("/", "_")
                (tex_dir / f"{safe_id}.tex").write_text(result.source, encoding="utf-8")
                log.info("Downloaded TeX source for %s (%d chars, saved to tex/%s.tex)", arxiv_id, len(result.source), safe_id)
            else:
                log.warning("TeX download failed for %s: %s", arxiv_id, result.error)
        except Exception:
            log.exception("Failed to download TeX source for %s", arxiv_id)
        finally:
            db.close()

    thread = threading.Thread(target=_download, daemon=True)
    thread.start()
