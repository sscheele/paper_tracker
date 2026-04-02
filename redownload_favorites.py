#!/usr/bin/env python3
"""Re-download TeX source for all favorited papers using the current extraction logic."""

import sys
from pathlib import Path

# Allow running from the repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent))

from paper_downloader.arxiv import ArxivClient
from paper_downloader.config import DEFAULT_CONFIG_PATH, DEFAULT_DB_PATH, load_config
from paper_downloader.db import PaperDB


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Re-download TeX source for all favorite papers.")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--force", action="store_true",
                        help="Re-download even if tex_source already exists")
    args = parser.parse_args()

    config = load_config(args.config)
    if not config.get("mailto"):
        print("Error: set 'mailto' in config first.", file=sys.stderr)
        sys.exit(1)

    db_path = args.db or DEFAULT_DB_PATH
    db = PaperDB(db_path)
    client = ArxivClient(mailto=config["mailto"])

    papers, total = db.query_papers(favorite_only=True, limit=10_000)
    print(f"Found {total} favorite(s).")

    ok = skipped = failed = 0
    for paper in papers:
        arxiv_id = paper["arxiv_id"]

        if not args.force and paper.get("tex_source"):
            print(f"  SKIP  {arxiv_id} (already downloaded)")
            skipped += 1
            continue

        print(f"  GET   {arxiv_id} ...", end=" ", flush=True)
        result = client.fetch_tex_source(arxiv_id)

        if result.ok:
            db.set_tex_source(arxiv_id, result.source)

            safe_id = arxiv_id.replace("/", "_")
            paper_dir = db_path.parent / "tex" / safe_id
            paper_dir.mkdir(parents=True, exist_ok=True)

            for filename, data in result.files.items():
                dest = (paper_dir / filename).resolve()
                if not dest.is_relative_to(paper_dir.resolve()):
                    print(f"\n    WARNING: skipping suspicious path: {filename}", file=sys.stderr)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)

            print(f"ok ({len(result.source):,} chars, {len(result.files)} files → tex/{safe_id}/)")
            ok += 1
        else:
            print(f"FAIL: {result.error}")
            failed += 1

    db.close()
    print(f"\nDone: {ok} downloaded, {skipped} skipped, {failed} failed.")


if __name__ == "__main__":
    main()
