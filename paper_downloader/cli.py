"""CLI entry point."""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .arxiv import ArxivClient
from .config import DEFAULT_CONFIG_PATH, DEFAULT_DB_PATH, load_config
from .db import PaperDB
from .output import format_html, format_plaintext, format_rss


def main():
    parser = argparse.ArgumentParser(
        prog="paper-downloader",
        description="Track new arxiv papers from favorite researchers.",
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help=f"Config file path (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--db", type=Path, default=None,
        help=f"Database path (default: {DEFAULT_DB_PATH})",
    )

    sub = parser.add_subparsers(dest="command")

    # fetch
    fetch_p = sub.add_parser("fetch", help="Fetch new papers from arxiv")
    fetch_p.add_argument("--days", type=int, default=None, help="Override lookback_days")

    # show
    show_p = sub.add_parser("show", help="Show papers")
    show_p.add_argument("--all", action="store_true", help="Show read papers too")
    show_p.add_argument(
        "--format", choices=["text", "html", "rss"], default="text",
        help="Output format (default: text)",
    )
    show_p.add_argument("--abstract", action="store_true", help="Show abstracts (text mode)")
    show_p.add_argument("-o", "--output", type=Path, help="Write to file instead of stdout")

    # read
    read_p = sub.add_parser("read", help="Mark paper(s) as read")
    read_p.add_argument("arxiv_id", nargs="+", help="arxiv ID(s) to mark as read")

    # unread
    unread_p = sub.add_parser("unread", help="Mark paper(s) as unread")
    unread_p.add_argument("arxiv_id", nargs="+", help="arxiv ID(s) to mark as unread")

    # catchup
    sub.add_parser("catchup", help="Mark all papers as read")

    # stats
    sub.add_parser("stats", help="Show database stats")

    # serve
    serve_p = sub.add_parser("serve", help="Start the web UI")
    serve_p.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    serve_p.add_argument("--port", type=int, default=8088, help="Port")
    serve_p.add_argument("--debug", action="store_true", help="Enable debug mode")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Serve command handles its own config/db lifecycle
    if args.command == "serve":
        _cmd_serve(args)
        return

    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    db_path = args.db or DEFAULT_DB_PATH
    db = PaperDB(db_path)

    try:
        if args.command == "fetch":
            _cmd_fetch(config, db, args)
        elif args.command == "show":
            _cmd_show(db, args)
        elif args.command == "read":
            _cmd_read(db, args)
        elif args.command == "unread":
            _cmd_unread(db, args)
        elif args.command == "catchup":
            _cmd_catchup(db)
        elif args.command == "stats":
            _cmd_stats(db)
    finally:
        db.close()


def _cmd_fetch(config: dict, db: PaperDB, args):
    client = ArxivClient(mailto=config["mailto"])
    lookback_days = args.days or config["lookback_days"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    authors = config["authors"]
    max_results = config["max_results_per_author"] * len(authors)

    print(f"Fetching papers for {len(authors)} authors...", file=sys.stderr)
    try:
        papers = client.search_authors(authors, max_results=max_results)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return

    total_new = 0
    for paper in papers:
        if paper.published >= cutoff:
            if db.upsert_paper(paper):
                total_new += 1

    print(f"Found {len(papers)} papers, {total_new} new", file=sys.stderr)


def _cmd_show(db: PaperDB, args):
    papers = db.get_all(unread_only=not args.all)

    if args.format == "html":
        output = format_html(papers)
    elif args.format == "rss":
        output = format_rss(papers)
    else:
        output = format_plaintext(papers, show_abstract=args.abstract)

    if args.output:
        args.output.write_text(output, encoding="utf-8")
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(output)


def _cmd_read(db: PaperDB, args):
    for aid in args.arxiv_id:
        paper = db.get_paper(aid)
        if paper:
            db.mark_read(aid)
            print(f"Marked as read: {aid}")
        else:
            print(f"Not found: {aid}", file=sys.stderr)


def _cmd_unread(db: PaperDB, args):
    for aid in args.arxiv_id:
        paper = db.get_paper(aid)
        if paper:
            db.mark_unread(aid)
            print(f"Marked as unread: {aid}")
        else:
            print(f"Not found: {aid}", file=sys.stderr)


def _cmd_catchup(db: PaperDB):
    stats = db.get_stats()
    db.mark_all_read()
    print(f"Marked {stats['unread']} papers as read.")


def _cmd_stats(db: PaperDB):
    stats = db.get_stats()
    print(f"Total papers: {stats['total']}")
    print(f"Unread:       {stats['unread']}")
    print(f"Read:         {stats['read']}")


def _cmd_serve(args):
    try:
        from .web import create_app
    except ImportError:
        print(
            "Error: Flask is required for the web UI.\n"
            "Install it with: pip install 'paper-downloader[web]' or: pip install flask",
            file=sys.stderr,
        )
        sys.exit(1)

    db_path = args.db or DEFAULT_DB_PATH
    config_path = args.config or None
    app = create_app(db_path=db_path, config_path=config_path)
    print(f"Paper tracker running at http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)
