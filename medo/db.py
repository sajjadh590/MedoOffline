"""
medo/db.py
SQLite storage layer with WAL mode and FTS5 full-text search.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from medo.models import AnswerSource, ExplanationStatus, ParsedImage, ParsedOption, ParsedQuestion

DEFAULT_DB_PATH = Path("data/medo.db")


def get_connection(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open SQLite connection with WAL mode and foreign keys enabled."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Generator[sqlite3.Cursor, None, None]:
    """Provide a transactional cursor that commits on exit or rolls back on exception."""
    cursor = conn.cursor()
    try:
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db(conn: sqlite3.Connection) -> None:
    """Create all required tables, indexes, and FTS5 search structures."""
    with transaction(conn) as cur:
        # Categories / Topics hierarchy
        cur.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY,
                parent_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                source_url TEXT NOT NULL,
                position INTEGER DEFAULT 0,
                question_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Questions
        cur.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id INTEGER,
                source_url TEXT NOT NULL,
                source_page INTEGER NOT NULL,
                ordinal INTEGER NOT NULL,
                content_hash TEXT UNIQUE NOT NULL,
                stem_html TEXT NOT NULL,
                stem_text TEXT NOT NULL,
                options_json TEXT NOT NULL,
                correct_index INTEGER,
                answer_source TEXT NOT NULL,
                vote_percent_json TEXT,
                explanation_html TEXT,
                explanation_text TEXT,
                explanation_status TEXT NOT NULL,
                has_images INTEGER DEFAULT 0,
                bookmarked INTEGER DEFAULT 0,
                user_selected_option INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Index for topic lookups and pagination
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_questions_topic_page
            ON questions(topic_id, source_page, ordinal);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_questions_content_hash
            ON questions(content_hash);
        """)

        # Assets (Mindmaps, illustrations, images)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS assets (
                sha1 TEXT PRIMARY KEY,
                original_url TEXT UNIQUE NOT NULL,
                local_path TEXT NOT NULL,
                mime TEXT,
                bytes_size INTEGER,
                role TEXT,
                downloaded_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Question <-> Assets M:N relation
        cur.execute("""
            CREATE TABLE IF NOT EXISTS question_assets (
                question_id INTEGER REFERENCES questions(id) ON DELETE CASCADE,
                asset_sha1 TEXT REFERENCES assets(sha1) ON DELETE CASCADE,
                PRIMARY KEY (question_id, asset_sha1)
            );
        """)

        # Resumable scraper progress state
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scrape_state (
                topic_id INTEGER PRIMARY KEY,
                last_page INTEGER DEFAULT 0,
                total_pages INTEGER DEFAULT 0,
                status TEXT DEFAULT 'idle',
                error_message TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # FTS5 full text search table
        cur.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS questions_fts USING fts5(
                content='questions',
                content_rowid='id',
                stem_text,
                explanation_text,
                options_text
            );
        """)

        # Triggers to keep FTS5 synchronized with questions table
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS questions_ai AFTER INSERT ON questions BEGIN
                INSERT INTO questions_fts(rowid, stem_text, explanation_text, options_text)
                VALUES (new.id, new.stem_text, coalesce(new.explanation_text, ''), new.options_json);
            END;
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS questions_ad AFTER DELETE ON questions BEGIN
                INSERT INTO questions_fts(questions_fts, rowid, stem_text, explanation_text, options_text)
                VALUES('delete', old.id, old.stem_text, coalesce(old.explanation_text, ''), old.options_json);
            END;
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS questions_au AFTER UPDATE ON questions BEGIN
                INSERT INTO questions_fts(questions_fts, rowid, stem_text, explanation_text, options_text)
                VALUES('delete', old.id, old.stem_text, coalesce(old.explanation_text, ''), old.options_json);
                INSERT INTO questions_fts(rowid, stem_text, explanation_text, options_text)
                VALUES (new.id, new.stem_text, coalesce(new.explanation_text, ''), new.options_json);
            END;
        """)


def upsert_question(
    conn: sqlite3.Connection,
    q: ParsedQuestion,
    topic_id: Optional[int] = None,
) -> int:
    """
    Insert or update a parsed question by its content_hash.
    Returns the question database ID.
    """
    if not q.content_hash:
        q.compute_content_hash()

    now = datetime.now(timezone.utc).isoformat()
    opts_json = q.options_json()
    vote_json = q.vote_percent_json()

    with transaction(conn) as cur:
        # Check if already exists by content_hash
        cur.execute(
            "SELECT id FROM questions WHERE content_hash = ?",
            (q.content_hash,),
        )
        row = cur.fetchone()

        if row:
            qid = row["id"]
            cur.execute("""
                UPDATE questions SET
                    topic_id = coalesce(?, topic_id),
                    source_url = ?,
                    source_page = ?,
                    ordinal = ?,
                    stem_html = ?,
                    stem_text = ?,
                    options_json = ?,
                    correct_index = coalesce(?, correct_index),
                    answer_source = ?,
                    vote_percent_json = ?,
                    explanation_html = coalesce(?, explanation_html),
                    explanation_text = coalesce(?, explanation_text),
                    explanation_status = ?,
                    has_images = ?,
                    updated_at = ?
                WHERE id = ?
            """, (
                topic_id,
                q.source_url,
                q.source_page,
                q.ordinal,
                q.stem_html,
                q.stem_text,
                opts_json,
                q.correct_index,
                q.answer_source.value,
                vote_json,
                q.explanation_html,
                q.explanation_text,
                q.explanation_status.value,
                1 if q.has_images else 0,
                now,
                qid,
            ))
        else:
            cur.execute("""
                INSERT INTO questions (
                    topic_id, source_url, source_page, ordinal,
                    content_hash, stem_html, stem_text, options_json,
                    correct_index, answer_source, vote_percent_json,
                    explanation_html, explanation_text, explanation_status,
                    has_images, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                topic_id,
                q.source_url,
                q.source_page,
                q.ordinal,
                q.content_hash,
                q.stem_html,
                q.stem_text,
                opts_json,
                q.correct_index,
                q.answer_source.value,
                vote_json,
                q.explanation_html,
                q.explanation_text,
                q.explanation_status.value,
                1 if q.has_images else 0,
                now,
                now,
            ))
            qid = cur.lastrowid

        # Link any image assets
        for img in q.images:
            if img.sha1:
                cur.execute("""
                    INSERT OR IGNORE INTO assets (sha1, original_url, local_path, role)
                    VALUES (?, ?, ?, ?)
                """, (img.sha1, img.original_url, img.local_path or "", img.role))
                cur.execute("""
                    INSERT OR IGNORE INTO question_assets (question_id, asset_sha1)
                    VALUES (?, ?)
                """, (qid, img.sha1))

    return qid


def get_question_by_id(conn: sqlite3.Connection, qid: int) -> Optional[dict]:
    """Retrieve a single question by id."""
    cur = conn.cursor()
    cur.execute("SELECT * FROM questions WHERE id = ?", (qid,))
    row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["options"] = json.loads(d["options_json"])
    d["vote_percent"] = json.loads(d["vote_percent_json"]) if d["vote_percent_json"] else {}
    return d


def get_questions_by_topic(
    conn: sqlite3.Connection,
    topic_id: int,
    limit: int = 1000,
    offset: int = 0,
) -> list[dict]:
    """Retrieve questions for a given topic ordered by page and ordinal."""
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM questions
        WHERE topic_id = ?
        ORDER BY source_page ASC, ordinal ASC
        LIMIT ? OFFSET ?
    """, (topic_id, limit, offset))
    results = []
    for row in cur.fetchall():
        d = dict(row)
        d["options"] = json.loads(d["options_json"])
        d["vote_percent"] = json.loads(d["vote_percent_json"]) if d["vote_percent_json"] else {}
        results.append(d)
    return results


def get_all_questions(conn: sqlite3.Connection) -> list[dict]:
    """Retrieve all questions ordered by topic, page, ordinal."""
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM questions
        ORDER BY coalesce(topic_id, 0) ASC, source_page ASC, ordinal ASC
    """)
    results = []
    for row in cur.fetchall():
        d = dict(row)
        d["options"] = json.loads(d["options_json"])
        d["vote_percent"] = json.loads(d["vote_percent_json"]) if d["vote_percent_json"] else {}
        results.append(d)
    return results


def search_questions(conn: sqlite3.Connection, query: str, limit: int = 50) -> list[dict]:
    """Full-text search in questions using FTS5."""
    cur = conn.cursor()
    cur.execute("""
        SELECT q.*, fts.rank
        FROM questions_fts fts
        JOIN questions q ON q.id = fts.rowid
        WHERE questions_fts MATCH ?
        ORDER BY fts.rank
        LIMIT ?
    """, (query, limit))
    results = []
    for row in cur.fetchall():
        d = dict(row)
        d["options"] = json.loads(d["options_json"])
        d["vote_percent"] = json.loads(d["vote_percent_json"]) if d["vote_percent_json"] else {}
        results.append(d)
    return results


def upsert_asset(
    conn: sqlite3.Connection,
    sha1: str,
    original_url: str,
    local_path: str,
    mime: Optional[str] = None,
    bytes_size: Optional[int] = None,
    role: Optional[str] = None,
) -> None:
    """Save metadata for a downloaded media asset."""
    with transaction(conn) as cur:
        cur.execute("""
            INSERT INTO assets (sha1, original_url, local_path, mime, bytes_size, role)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(sha1) DO UPDATE SET
                local_path = excluded.local_path,
                mime = coalesce(excluded.mime, assets.mime),
                bytes_size = coalesce(excluded.bytes_size, assets.bytes_size),
                role = coalesce(excluded.role, assets.role)
        """, (sha1, original_url, local_path, mime, bytes_size, role))


def get_asset_by_sha1(conn: sqlite3.Connection, sha1: str) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM assets WHERE sha1 = ?", (sha1,))
    row = cur.fetchone()
    return dict(row) if row else None


def get_asset_by_url(conn: sqlite3.Connection, original_url: str) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM assets WHERE original_url = ?", (original_url,))
    row = cur.fetchone()
    return dict(row) if row else None


def get_scrape_state(conn: sqlite3.Connection, topic_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM scrape_state WHERE topic_id = ?", (topic_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def update_scrape_state(
    conn: sqlite3.Connection,
    topic_id: int,
    last_page: int,
    total_pages: Optional[int] = None,
    status: str = "idle",
    error_message: Optional[str] = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with transaction(conn) as cur:
        cur.execute("""
            INSERT INTO scrape_state (topic_id, last_page, total_pages, status, error_message, updated_at)
            VALUES (?, ?, coalesce(?, 0), ?, ?, ?)
            ON CONFLICT(topic_id) DO UPDATE SET
                last_page = excluded.last_page,
                total_pages = CASE WHEN excluded.total_pages > 0 THEN excluded.total_pages ELSE scrape_state.total_pages END,
                status = excluded.status,
                error_message = excluded.error_message,
                updated_at = excluded.updated_at
        """, (topic_id, last_page, total_pages, status, error_message, now))


def set_user_answer(conn: sqlite3.Connection, question_id: int, selected_option: int) -> None:
    """Record user's answered option for interactive offline practice."""
    with transaction(conn) as cur:
        cur.execute(
            "UPDATE questions SET user_selected_option = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (selected_option, question_id),
        )


def toggle_bookmark(conn: sqlite3.Connection, question_id: int) -> bool:
    """Toggle bookmarked status for a question."""
    with transaction(conn) as cur:
        cur.execute("SELECT bookmarked FROM questions WHERE id = ?", (question_id,))
        row = cur.fetchone()
        new_val = 1 if not (row and row["bookmarked"]) else 0
        cur.execute(
            "UPDATE questions SET bookmarked = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_val, question_id),
        )
        return bool(new_val)


def topics_needing_refill(conn: sqlite3.Connection) -> list[int]:
    """Distinct topic ids that hold questions without a full explanation.

    Drives `medo scrape --refill-explanations`: later, with a better account
    tier, those topics are revisited to fill the gaps (never evading limits).
    """
    cur = conn.cursor()
    cur.execute(
        """SELECT DISTINCT topic_id FROM questions
           WHERE topic_id IS NOT NULL
             AND (explanation_status != 'full'
                  OR explanation_text IS NULL
                  OR length(explanation_text) < 30)
           ORDER BY topic_id"""
    )
    return [row[0] for row in cur.fetchall()]


def upsert_category(
    conn: sqlite3.Connection,
    *,
    kind: str,
    title: str,
    source_url: str,
    parent_id: Optional[int] = None,
    position: int = 0,
    question_count: int = 0,
    node_id: Optional[int] = None,
) -> int:
    """Insert or update one hierarchy node (category/lesson/section/topic).

    Identity: the source_url (site ids are NOT reused as primary keys —
    auto rowids avoid colliding with site topic ids like 79).
    Returns the row id.
    """
    now = datetime.now(timezone.utc).isoformat()
    with transaction(conn) as cur:
        cur.execute("SELECT id FROM categories WHERE source_url = ?", (source_url,))
        row = cur.fetchone()
        if row:
            rid = row[0]
            cur.execute(
                """UPDATE categories SET
                       parent_id = coalesce(?, parent_id), title = ?,
                       kind = ?, position = ?,
                       question_count = max(question_count, ?),
                       updated_at = ?
                   WHERE id = ?""",
                (parent_id, title, kind, position, question_count, now, rid),
            )
            return rid

        cur.execute(
            """INSERT INTO categories
                   (id, parent_id, kind, title, source_url, position,
                    question_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (None, parent_id, kind, title, source_url, position,
             question_count, now, now),
        )
        return int(cur.lastrowid or 0)


def get_categories(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM categories ORDER BY coalesce(parent_id, 0), position, id"
    )
    return [dict(r) for r in cur.fetchall()]


def get_category_tree(conn: sqlite3.Connection) -> list[dict]:
    """Nested tree (children ordered by position) for the web UI."""
    nodes = get_categories(conn)
    by_id = {n["id"]: {**n, "children": []} for n in nodes}
    roots: list[dict] = []
    for n in by_id.values():
        parent = n.get("parent_id")
        if parent is not None and parent in by_id:
            by_id[parent]["children"].append(n)
        else:
            roots.append(n)
    return roots


def list_topic_ids(conn: sqlite3.Connection, include_done: bool = True) -> list[int]:
    """Site topic ids (from source_url), in hierarchy order;
    optionally only topics whose scrape_state is not completed."""
    import re as _re
    cur = conn.cursor()
    cur.execute(
        "SELECT source_url FROM categories WHERE kind = 'topic' ORDER BY position, id"
    )
    ids: list[int] = []
    for (url,) in cur.fetchall():
        m = _re.search(r"topic-id-(\d+)", url or "")
        if m:
            ids.append(int(m.group(1)))
    if include_done:
        return ids
    done = {
        r[0]
        for r in conn.execute(
            "SELECT topic_id FROM scrape_state WHERE status = 'completed'"
        ).fetchall()
    }
    return [t for t in ids if t not in done]


def get_database_stats(conn: sqlite3.Connection) -> dict:
    """Return database aggregate summary statistics."""
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM questions")
    total_questions = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM questions WHERE correct_index IS NOT NULL")
    with_answer = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM questions WHERE explanation_text IS NOT NULL AND length(explanation_text) > 20")
    with_explanation = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM questions WHERE has_images = 1")
    with_images = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM assets")
    total_assets = cur.fetchone()[0]

    cur.execute("SELECT COUNT(DISTINCT topic_id) FROM questions WHERE topic_id IS NOT NULL")
    total_topics = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM questions WHERE bookmarked = 1")
    bookmarked = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM questions WHERE user_selected_option IS NOT NULL")
    attempted = cur.fetchone()[0]

    return {
        "total_questions": total_questions,
        "with_answer": with_answer,
        "with_explanation": with_explanation,
        "with_images": with_images,
        "total_assets": total_assets,
        "total_topics": total_topics,
        "bookmarked": bookmarked,
        "attempted": attempted,
    }
