"""SQLite database for paper tracking."""

import sqlite3
from pathlib import Path

from .arxiv import Paper

# Base schema — the initial tables. Always run with CREATE IF NOT EXISTS
# so it's safe on an already-initialized DB.
BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    arxiv_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,
    abstract TEXT,
    published TEXT NOT NULL,
    updated TEXT,
    categories TEXT,
    pdf_url TEXT,
    abs_url TEXT,
    discovered_date TEXT NOT NULL DEFAULT (datetime('now')),
    read INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""

# Ordered list of migrations. Each entry is a list of SQL statements.
# Index = version number (0-based). A migration runs if the current
# schema version is less than its index + 1.
MIGRATIONS: list[list[str]] = [
    # Migration 1: tags system
    [
        "CREATE TABLE IF NOT EXISTS tags (name TEXT PRIMARY KEY)",
        "CREATE TABLE IF NOT EXISTS paper_tags ("
        "    arxiv_id TEXT NOT NULL REFERENCES papers(arxiv_id),"
        "    tag TEXT NOT NULL REFERENCES tags(name),"
        "    PRIMARY KEY (arxiv_id, tag)"
        ")",
    ],
    # Migration 2: favorites and notes
    [
        "ALTER TABLE papers ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE papers ADD COLUMN notes TEXT DEFAULT ''",
    ],
    # Migration 3: tex source storage
    [
        "ALTER TABLE papers ADD COLUMN tex_source TEXT",
    ],
    # Migration 4: tex download timestamp
    [
        "ALTER TABLE papers ADD COLUMN tex_downloaded_at TEXT",
    ],
]


class PaperDB:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(BASE_SCHEMA)
        self._migrate()

    def _migrate(self):
        """Run any pending migrations."""
        row = self.conn.execute(
            "SELECT version FROM schema_version"
        ).fetchone()
        if row is None:
            # Fresh DB or pre-migration DB. Detect which by checking for
            # columns/tables that would exist if older code already ran.
            current = self._detect_legacy_version()
            self.conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (current,)
            )
            self.conn.commit()
        else:
            current = row[0]

        for i in range(current, len(MIGRATIONS)):
            for stmt in MIGRATIONS[i]:
                try:
                    self.conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # e.g. column/table already exists from legacy code
            self.conn.execute(
                "UPDATE schema_version SET version = ?", (i + 1,)
            )
            self.conn.commit()

    def _detect_legacy_version(self) -> int:
        """Detect how far a pre-migration DB has already been set up."""
        cols = {
            row[1]
            for row in self.conn.execute("PRAGMA table_info(papers)").fetchall()
        }
        tables = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        version = 0
        if "tags" in tables and "paper_tags" in tables:
            version = 1
        if "favorite" in cols and "notes" in cols:
            version = 2
        if "tex_source" in cols:
            version = 3
        return version

    # --- Core CRUD ---

    def upsert_paper(self, paper: Paper) -> bool:
        """Insert or update a paper. Returns True if it was new."""
        existing = self.conn.execute(
            "SELECT arxiv_id FROM papers WHERE arxiv_id = ?", (paper.arxiv_id,)
        ).fetchone()
        self.conn.execute(
            """INSERT INTO papers (arxiv_id, title, authors, abstract, published,
               updated, categories, pdf_url, abs_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(arxiv_id) DO UPDATE SET
                 title=excluded.title,
                 authors=excluded.authors,
                 abstract=excluded.abstract,
                 updated=excluded.updated,
                 categories=excluded.categories,
                 pdf_url=excluded.pdf_url,
                 abs_url=excluded.abs_url
            """,
            (
                paper.arxiv_id,
                paper.title,
                ", ".join(paper.authors),
                paper.abstract,
                paper.published.isoformat(),
                paper.updated.isoformat(),
                ", ".join(paper.categories),
                paper.pdf_url,
                paper.abs_url,
            ),
        )
        self.conn.commit()
        return existing is None

    # --- Read status ---

    def mark_read(self, arxiv_id: str):
        self.conn.execute("UPDATE papers SET read = 1 WHERE arxiv_id = ?", (arxiv_id,))
        self.conn.commit()

    def mark_unread(self, arxiv_id: str):
        self.conn.execute("UPDATE papers SET read = 0 WHERE arxiv_id = ?", (arxiv_id,))
        self.conn.commit()

    def mark_all_read(self):
        self.conn.execute("UPDATE papers SET read = 1")
        self.conn.commit()

    def mark_read_bulk(self, arxiv_ids: list[str]):
        self.conn.executemany(
            "UPDATE papers SET read = 1 WHERE arxiv_id = ?",
            [(aid,) for aid in arxiv_ids],
        )
        self.conn.commit()

    def mark_unread_bulk(self, arxiv_ids: list[str]):
        self.conn.executemany(
            "UPDATE papers SET read = 0 WHERE arxiv_id = ?",
            [(aid,) for aid in arxiv_ids],
        )
        self.conn.commit()

    # --- Favorites ---

    def toggle_favorite(self, arxiv_id: str) -> bool:
        """Toggle favorite status. Returns new state."""
        self.conn.execute(
            "UPDATE papers SET favorite = 1 - favorite WHERE arxiv_id = ?", (arxiv_id,)
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT favorite FROM papers WHERE arxiv_id = ?", (arxiv_id,)
        ).fetchone()
        return bool(row and row[0])

    # --- Notes ---

    def set_notes(self, arxiv_id: str, notes: str):
        self.conn.execute("UPDATE papers SET notes = ? WHERE arxiv_id = ?", (notes, arxiv_id))
        self.conn.commit()

    # --- TeX source ---

    def set_tex_source(self, arxiv_id: str, tex_source: str | None):
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat() if tex_source else None
        self.conn.execute(
            "UPDATE papers SET tex_source = ?, tex_downloaded_at = ? WHERE arxiv_id = ?",
            (tex_source, ts, arxiv_id),
        )
        self.conn.commit()

    def get_tex_source(self, arxiv_id: str) -> str | None:
        row = self.conn.execute("SELECT tex_source FROM papers WHERE arxiv_id = ?", (arxiv_id,)).fetchone()
        return row[0] if row else None

    # --- Tags ---

    def get_all_tags(self) -> list[str]:
        rows = self.conn.execute("SELECT name FROM tags ORDER BY name").fetchall()
        return [r[0] for r in rows]

    def tag_paper(self, arxiv_id: str, tag_name: str):
        self.conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
        self.conn.execute(
            "INSERT OR IGNORE INTO paper_tags (arxiv_id, tag) VALUES (?, ?)",
            (arxiv_id, tag_name),
        )
        self.conn.commit()

    def untag_paper(self, arxiv_id: str, tag_name: str):
        self.conn.execute(
            "DELETE FROM paper_tags WHERE arxiv_id = ? AND tag = ?",
            (arxiv_id, tag_name),
        )
        self.conn.commit()

    def get_paper_tags(self, arxiv_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT tag FROM paper_tags WHERE arxiv_id = ? ORDER BY tag", (arxiv_id,)
        ).fetchall()
        return [r[0] for r in rows]

    # --- Queries ---

    def get_paper(self, arxiv_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM papers WHERE arxiv_id = ?", (arxiv_id,)
        ).fetchone()

    def get_unread(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM papers WHERE read = 0 ORDER BY published DESC"
        ).fetchall()

    def get_all(self, unread_only: bool = False) -> list[sqlite3.Row]:
        if unread_only:
            return self.get_unread()
        return self.conn.execute(
            "SELECT * FROM papers ORDER BY published DESC"
        ).fetchall()

    def query_papers(
        self,
        *,
        unread_only: bool = False,
        favorite_only: bool = False,
        author: str | None = None,
        category: str | None = None,
        tag: str | None = None,
        sort_by: str = "published",
        sort_order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Unified query with filtering, sorting, pagination. Returns (papers, total)."""
        conditions = []
        params: list = []

        if unread_only:
            conditions.append("p.read = 0")
        if favorite_only:
            conditions.append("p.favorite = 1")
        if author:
            conditions.append("p.authors LIKE ?")
            params.append(f"%{author}%")
        if category:
            conditions.append("p.categories LIKE ?")
            params.append(f"%{category}%")

        join = ""
        if tag:
            join = "JOIN paper_tags pt ON p.arxiv_id = pt.arxiv_id"
            conditions.append("pt.tag = ?")
            params.append(tag)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Validate sort to prevent injection
        allowed_sorts = {"published", "title", "discovered_date", "authors"}
        if sort_by not in allowed_sorts:
            sort_by = "published"
        order = "DESC" if sort_order.lower() == "desc" else "ASC"

        count_sql = f"SELECT COUNT(DISTINCT p.arxiv_id) FROM papers p {join} {where}"
        total = self.conn.execute(count_sql, params).fetchone()[0]

        query_sql = f"""
            SELECT DISTINCT p.* FROM papers p {join} {where}
            ORDER BY p.{sort_by} {order}
            LIMIT ? OFFSET ?
        """
        rows = self.conn.execute(query_sql, params + [limit, offset]).fetchall()

        papers = []
        for row in rows:
            paper = dict(row)
            paper["tags"] = self.get_paper_tags(paper["arxiv_id"])
            papers.append(paper)

        return papers, total

    def get_stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        unread = self.conn.execute("SELECT COUNT(*) FROM papers WHERE read = 0").fetchone()[0]
        favorites = self.conn.execute("SELECT COUNT(*) FROM papers WHERE favorite = 1").fetchone()[0]
        return {"total": total, "unread": unread, "read": total - unread, "favorites": favorites}

    def get_distinct_authors(self) -> list[str]:
        """Get all unique individual author names from the DB."""
        rows = self.conn.execute("SELECT DISTINCT authors FROM papers").fetchall()
        names = set()
        for row in rows:
            for name in row[0].split(", "):
                stripped = name.strip()
                if stripped:
                    names.add(stripped)
        return sorted(names)

    def get_distinct_categories(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT categories FROM papers").fetchall()
        cats = set()
        for row in rows:
            for cat in (row[0] or "").split(", "):
                stripped = cat.strip()
                if stripped:
                    cats.add(stripped)
        return sorted(cats)

    def close(self):
        self.conn.close()
