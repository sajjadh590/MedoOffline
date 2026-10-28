"""
medo/db.py
SQLite storage layer with WAL mode and FTS5 full-text search.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from medo.models import AnswerSource, ExplanationStatus, ParsedImage, ParsedOption, ParsedQuestion
from medo.paths import DEFAULT_DB_PATH  # noqa: F401  (re-exported: callers import it here)

_TOPIC_ID_RE = re.compile(r"topic-id-(\d+)")
# Micro-topics («مطالعه مبحثی») are a filtered view of their parent topic:
# /qbank/topic-id-94?tag=1781.  They are real sub-categories of the tree and
# own their own questions once scraped.
_TAG_ID_RE = re.compile(r"[?&]tag=(\d+)")


def tag_id_of(source_url: str | None) -> Optional[int]:
    """The ?tag= id in a node URL, or None for a plain topic/category."""
    m = _TAG_ID_RE.search(source_url or "")
    return int(m.group(1)) if m else None


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

        # Per-run internet usage.  One row is written when a run ends, so the
        # UI can show what today cost and estimate a new download from the
        # measured average per question.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                bytes INTEGER NOT NULL DEFAULT 0,
                questions INTEGER NOT NULL DEFAULT 0,
                status TEXT,
                targets TEXT
            );
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_started ON usage_log(started_at);"
        )

        # The download the user started last (targets + limits).  Kept in the
        # database so a login/continuation still knows what to continue after
        # the app is restarted.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS run_request (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                payload TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Per-subtopic («?tag=») cursor.  A subtopic is a slice of its parent
        # topic's question pool, so it needs its own cursor: sharing the
        # topic row made a subtopic download resume at the topic's page and a
        # finished subtopic look like a finished topic.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS micro_state (
                topic_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                last_page INTEGER DEFAULT 0,
                total_pages INTEGER DEFAULT 0,
                status TEXT DEFAULT 'idle',
                error_message TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (topic_id, tag_id)
            );
        """)

        # Resumable cursor of the subtopic-mapping pass (scripts/backfill_tags
        # and the UI button).  Per subtopic so a 429 on subtopic four resumes
        # at its own page instead of repeating subtopics one to three.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tag_sync_state (
                topic_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL DEFAULT 0,
                last_page INTEGER DEFAULT 0,
                status TEXT DEFAULT 'idle',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (topic_id, tag_id)
            );
        """)

        # FTS5 full-text index over an external content table.
        #
        # Its columns must exist in `questions` under the *same* names: the
        # original declaration listed `options_text` (the real column is
        # `options_json`), so every content read — count(*), delete, rebuild —
        # raised "no such column: T.options_text" and the index could never be
        # repaired.  Detect that legacy declaration and recreate the index.
        fts_row = cur.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='questions_fts'"
        ).fetchone()
        legacy_fts = bool(fts_row and "options_text" in (fts_row[0] or ""))
        if legacy_fts:
            cur.execute("DROP TABLE questions_fts")

        cur.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS questions_fts USING fts5(
                content='questions',
                content_rowid='id',
                stem_text,
                explanation_text,
                options_json
            );
        """)

        # Triggers are dropped and recreated every start: they are this
        # module's own definitions, and IF NOT EXISTS would keep an outdated
        # body (e.g. the legacy column list) alive forever.
        for trigger in ("questions_ai", "questions_ad", "questions_au"):
            cur.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        cur.execute("""
            CREATE TRIGGER questions_ai AFTER INSERT ON questions BEGIN
                INSERT INTO questions_fts(rowid, stem_text, explanation_text, options_json)
                VALUES (new.id, new.stem_text, coalesce(new.explanation_text, ''), new.options_json);
            END;
        """)
        cur.execute("""
            CREATE TRIGGER questions_ad AFTER DELETE ON questions BEGIN
                INSERT INTO questions_fts(questions_fts, rowid, stem_text, explanation_text, options_json)
                VALUES('delete', old.id, old.stem_text, coalesce(old.explanation_text, ''), old.options_json);
            END;
        """)
        cur.execute("""
            CREATE TRIGGER questions_au AFTER UPDATE OF stem_text, explanation_text, options_json ON questions BEGIN
                INSERT INTO questions_fts(questions_fts, rowid, stem_text, explanation_text, options_json)
                VALUES('delete', old.id, old.stem_text, coalesce(old.explanation_text, ''), old.options_json);
                INSERT INTO questions_fts(rowid, stem_text, explanation_text, options_json)
                VALUES (new.id, new.stem_text, coalesce(new.explanation_text, ''), new.options_json);
            END;
        """)

        # Full hierarchy path per question (added later; migrate old DBs)
        cols = {r[1] for r in cur.execute("PRAGMA table_info(questions)").fetchall()}
        if "category_path" not in cols:
            cur.execute("ALTER TABLE questions ADD COLUMN category_path TEXT;")
        # Subtopic attribution.  A question downloaded through a «?tag=»
        # filter belongs to that sub-category; a whole-topic download leaves
        # both NULL (the question's tags are not exposed on its page).
        if "tag_id" not in cols:
            cur.execute("ALTER TABLE questions ADD COLUMN tag_id INTEGER;")
        if "subtopic_title" not in cols:
            cur.execute("ALTER TABLE questions ADD COLUMN subtopic_title TEXT;")
        # Bytes of network traffic (page HTML + assets) that this question's
        # download actually cost.  Measured live, so the UI can report real
        # consumption instead of guessing from the database file size.
        if "download_bytes" not in cols:
            cur.execute(
                "ALTER TABLE questions ADD COLUMN download_bytes INTEGER DEFAULT 0;"
            )
        # The site's own question id (#question-meta).  It stays the same when
        # a stem is edited upstream and when the question is read again through
        # another subtopic, so it is the identity a repair pass can rely on.
        # NULL for rows stored before this column existed.
        if "site_question_id" not in cols:
            cur.execute(
                "ALTER TABLE questions ADD COLUMN site_question_id INTEGER;"
            )
        # Which session read this page: 'login' pages carry the Pro lesson and
        # the key, 'guest' pages often do not.  The repair queue is built from
        # this, so a downgraded row can be upgraded once the user logs in.
        if "fetched_as" not in cols:
            cur.execute(
                "ALTER TABLE questions ADD COLUMN fetched_as TEXT NOT NULL "
                "DEFAULT 'unknown';"
            )
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_questions_site_id "
            "ON questions(site_question_id) WHERE site_question_id IS NOT NULL;"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_questions_topic_tag "
            "ON questions(topic_id, tag_id);"
        )

        # A question can be reached through several navigation nodes: the whole
        # topic and any number of «?tag=» subtopics.  questions.tag_id can hold
        # only one of them, so every membership lives here; the old columns
        # stay as the *primary* node for readers that have not moved yet.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS question_nodes (
                question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                topic_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL DEFAULT 0,
                subtopic_title TEXT,
                PRIMARY KEY (question_id, topic_id, tag_id)
            );
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_question_nodes_node "
            "ON question_nodes(topic_id, tag_id);"
        )
        # Backfill from the single-parent rows.  Runs on every start but only
        # touches questions that have no node row yet: a question that is now
        # in several subtopics keeps all of them.
        cur.execute("""
            INSERT OR IGNORE INTO question_nodes
                (question_id, topic_id, tag_id, subtopic_title)
            SELECT q.id, q.topic_id, coalesce(q.tag_id, 0), q.subtopic_title
            FROM questions q
            WHERE q.topic_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM question_nodes n
                              WHERE n.question_id = q.id)
        """)

        # Rows written before the triggers existed are invisible to MATCH.
        # `questions_fts_docsize` holds one row per *indexed* document —
        # counting the FTS table itself would just count the content rows.
        try:
            questions_n = cur.execute("SELECT count(*) FROM questions").fetchone()[0]
            indexed_n = cur.execute(
                "SELECT count(*) FROM questions_fts_docsize"
            ).fetchone()[0]
            if legacy_fts or questions_n != indexed_n:
                cur.execute(
                    "INSERT INTO questions_fts(questions_fts) VALUES('rebuild')"
                )
        except sqlite3.OperationalError:
            pass


def upsert_question(
    conn: sqlite3.Connection,
    q: ParsedQuestion,
    topic_id: Optional[int] = None,
    category_path: Optional[str] = None,
    tag_id: Optional[int] = None,
    subtopic_title: Optional[str] = None,
    download_bytes: int = 0,
    fetched_as: str = "unknown",
) -> int:
    """
    Insert or update a parsed question and record where it was found.

    Identity is the site's own question id (#question-meta) when the page
    exposed one, else the content hash.  A stem edited upstream changes the
    hash but not the site id, so the stored row is updated instead of being
    duplicated.

    ``tag_id``/``subtopic_title`` describe the subtopic (?tag=) the page was
    read through.  Every membership goes into ``question_nodes``;
    ``questions.topic_id``/``tag_id`` keep the FIRST one ever seen (the old
    single-parent view), so a later subtopic pass can never steal the tag an
    earlier one assigned.

    ``download_bytes`` is that question's measured network cost.  A re-scrape
    with no measurement (0) keeps the previous value instead of erasing it.
    Returns the question database ID.
    """
    if not q.content_hash:
        q.compute_content_hash()

    now = datetime.now(timezone.utc).isoformat()
    opts_json = q.options_json()
    vote_json = q.vote_percent_json()
    site_id = q.site_question_id
    if not isinstance(site_id, int) or isinstance(site_id, bool) or site_id <= 0:
        site_id = None
    node_tag = int(tag_id) if tag_id not in (None, "") else 0

    with transaction(conn) as cur:
        # Site id first: it survives an upstream edit of the stem/options.
        row = None
        if site_id is not None:
            row = cur.execute(
                "SELECT id FROM questions WHERE site_question_id = ?",
                (site_id,),
            ).fetchone()
        if row is None:
            row = cur.execute(
                "SELECT id FROM questions WHERE content_hash = ?",
                (q.content_hash,),
            ).fetchone()

        if row:
            qid = row["id"]
            # content_hash is UNIQUE.  When the page changed upstream the site
            # id finds the old row whose hash no longer matches; if some other
            # row already owns the new hash, keep the stored one rather than
            # failing the whole page on an IntegrityError.
            clash = cur.execute(
                "SELECT 1 FROM questions WHERE content_hash = ? AND id <> ?",
                (q.content_hash, qid),
            ).fetchone()
            cur.execute("""
                UPDATE questions SET
                    content_hash = coalesce(?, content_hash),
                    site_question_id = coalesce(?, site_question_id),
                    topic_id = coalesce(topic_id, ?),
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
                    category_path = coalesce(?, category_path),
                    tag_id = CASE WHEN tag_id IS NULL THEN ? ELSE tag_id END,
                    subtopic_title = coalesce(subtopic_title, ?),
                    download_bytes = CASE WHEN ? > 0 THEN ? ELSE download_bytes END,
                    fetched_as = CASE WHEN ? <> 'unknown' THEN ? ELSE fetched_as END,
                    updated_at = ?
                WHERE id = ?
            """, (
                None if clash else q.content_hash,
                site_id,
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
                category_path,
                node_tag,
                subtopic_title,
                int(download_bytes or 0),
                int(download_bytes or 0),
                str(fetched_as or "unknown"),
                str(fetched_as or "unknown"),
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
                    has_images, category_path, tag_id, subtopic_title,
                    download_bytes, site_question_id, fetched_as,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                category_path,
                tag_id,
                subtopic_title,
                int(download_bytes or 0),
                site_id,
                str(fetched_as or "unknown"),
                now,
                now,
            ))
            qid = cur.lastrowid

        if topic_id is not None and int(topic_id) > 0:
            cur.execute("""
                INSERT INTO question_nodes
                    (question_id, topic_id, tag_id, subtopic_title)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(question_id, topic_id, tag_id) DO UPDATE SET
                    subtopic_title = coalesce(excluded.subtopic_title,
                                              question_nodes.subtopic_title)
            """, (qid, int(topic_id), node_tag, subtopic_title))

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


def get_question_nodes(conn: sqlite3.Connection, question_id: int) -> list[dict]:
    """Every navigation node a question was reached through."""
    try:
        rows = conn.execute(
            "SELECT topic_id, tag_id, subtopic_title FROM question_nodes "
            "WHERE question_id = ? ORDER BY topic_id, tag_id",
            (int(question_id),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"topic_id": int(r["topic_id"]), "tag_id": int(r["tag_id"] or 0),
             "subtopic_title": r["subtopic_title"]} for r in rows]


def _primary_node(row: dict) -> list[dict]:
    """The old single-parent columns as a one-item node list."""
    tid = row.get("topic_id")
    if not tid:
        return []
    return [{"topic_id": int(tid), "tag_id": int(row.get("tag_id") or 0),
             "subtopic_title": row.get("subtopic_title")}]


def _attach_nodes(conn: sqlite3.Connection, rows: list[dict]) -> list[dict]:
    """Add each question's ``nodes`` list with ONE extra query.

    The web UI, the .medo bundle and the offline export all need to know every
    topic/subtopic a question belongs to; joining per row would make
    /api/questions quadratic.
    """
    if not rows:
        return rows
    if not _table_exists(conn, "question_nodes"):
        for row in rows:
            row["nodes"] = _primary_node(row)
        return rows

    by_id: dict[int, list[dict]] = {}
    ids = [int(r["id"]) for r in rows]
    try:
        if len(ids) > 500:
            # Stay below SQLite's bound-variable limit on a full bank.
            cursor = conn.execute(
                "SELECT question_id, topic_id, tag_id, subtopic_title "
                "FROM question_nodes ORDER BY topic_id, tag_id"
            )
        else:
            marks = ",".join("?" for _ in ids)
            cursor = conn.execute(
                f"SELECT question_id, topic_id, tag_id, subtopic_title "
                f"FROM question_nodes WHERE question_id IN ({marks}) "
                f"ORDER BY topic_id, tag_id", ids
            )
        for r in cursor.fetchall():
            by_id.setdefault(int(r["question_id"]), []).append({
                "topic_id": int(r["topic_id"]),
                "tag_id": int(r["tag_id"] or 0),
                "subtopic_title": r["subtopic_title"],
            })
    except sqlite3.OperationalError:
        by_id = {}
    for row in rows:
        row["nodes"] = by_id.get(int(row["id"])) or _primary_node(row)
    return rows


def count_node_questions(
    conn: sqlite3.Connection, topic_id: int, tag_id: Optional[int] = None,
) -> int:
    """Distinct questions stored for a topic, or for one of its subtopics.

    ``tag_id=None`` counts the whole topic: questions read through any node of
    it, so a subtopic question is not missing from its parent's badge.
    """
    sql = "SELECT count(DISTINCT question_id) FROM question_nodes WHERE topic_id = ?"
    params: list = [int(topic_id)]
    if tag_id is not None:
        sql += " AND tag_id = ?"
        params.append(int(tag_id))
    try:
        found = int(conn.execute(sql, params).fetchone()[0] or 0)
        # Nodes are authoritative whenever this topic has any: a question in
        # two subtopics must not be counted twice.
        if found or (conn.execute(
                "SELECT 1 FROM question_nodes WHERE topic_id = ? LIMIT 1",
                (int(topic_id),)).fetchone()):
            return found
        # No node rows for this topic at all (a database written before the
        # table existed): read the old single-parent column instead.
        legacy = "SELECT count(*) FROM questions WHERE topic_id = ?"
        legacy_params: list = [int(topic_id)]
        if tag_id is not None:
            legacy += " AND tag_id = ?"
            legacy_params.append(int(tag_id))
        return int(conn.execute(legacy, legacy_params).fetchone()[0] or 0)
    except sqlite3.OperationalError:
        return 0


def count_untagged_questions(conn: sqlite3.Connection) -> int:
    """Questions no subtopic page has claimed yet (whole-topic downloads)."""
    try:
        return int(conn.execute(
            "SELECT count(*) FROM questions q WHERE NOT EXISTS ("
            "  SELECT 1 FROM question_nodes n"
            "  WHERE n.question_id = q.id AND n.tag_id > 0)"
        ).fetchone()[0] or 0)
    except sqlite3.OperationalError:
        return 0


# A question still needs the signed-in session when the key is missing, the
# lesson is absent/partial/locked, or the page was stored as a guest.
NEEDS_REPAIR_SQL = (
    "fetched_as <> 'login'"
    " OR correct_index IS NULL"
    " OR explanation_text IS NULL"
    " OR explanation_status IN ('locked', 'key_only', 'unknown')"
)


def count_needing_repair(conn: sqlite3.Connection) -> int:
    """How many stored questions the Pro session could still improve."""
    try:
        return int(conn.execute(
            f"SELECT count(*) FROM questions WHERE {NEEDS_REPAIR_SQL}"
        ).fetchone()[0] or 0)
    except sqlite3.OperationalError:
        return 0


def questions_needing_repair(
    conn: sqlite3.Connection, limit: Optional[int] = None,
) -> list[dict]:
    """The repair queue, oldest first, with what it takes to re-locate a page."""
    sql = (
        "SELECT id, topic_id, tag_id, site_question_id, source_page, ordinal, "
        "       stem_html, stem_text, options_json, correct_index, "
        "       explanation_html, explanation_text, explanation_status, "
        "       fetched_as "
        f"FROM questions WHERE {NEEDS_REPAIR_SQL} ORDER BY id"
    )
    args: list = []
    if limit:
        sql += " LIMIT ?"
        args.append(int(limit))
    try:
        return [dict(row) for row in conn.execute(sql, args).fetchall()]
    except sqlite3.OperationalError:
        return []


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
    d["nodes"] = get_question_nodes(conn, qid)
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
    return _attach_nodes(conn, results)


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
    return _attach_nodes(conn, results)


# The list endpoint and the web UI need the card, the search text and the
# votes — never the stored HTML, which is the bulk of a Pro lesson.
LIGHT_COLUMNS = (
    "id, topic_id, tag_id, source_page, ordinal, stem_text, "
    "explanation_text, explanation_status, options_json, "
    "vote_percent_json, correct_index, answer_source, has_images, "
    "category_path, bookmarked, user_selected_option"
)


def get_questions_light(
    conn: sqlite3.Connection,
    offset: int = 0,
    limit: Optional[int] = None,
) -> list[dict]:
    """A page of the question list without the heavy HTML bodies.

    ``/api/questions`` used to return ``SELECT *`` of the whole bank in one
    JSON: tens of megabytes rebuilt and parsed on every page load, when the UI
    shows one question at a time.  The full row is fetched on demand from
    ``/api/question/<id>``.
    """
    sql = (f"SELECT {LIGHT_COLUMNS} FROM questions "
           "ORDER BY coalesce(topic_id, 0) ASC, source_page ASC, ordinal ASC")
    args: list = []
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        args = [int(limit), int(offset or 0)]
    elif offset:
        sql += " LIMIT -1 OFFSET ?"
        args = [int(offset)]

    results: list[dict] = []
    for row in conn.execute(sql, args).fetchall():
        d = dict(row)
        d["options"] = json.loads(d["options_json"] or "[]")
        d.pop("options_json", None)
        d["vote_percent"] = (json.loads(d["vote_percent_json"])
                             if d.get("vote_percent_json") else {})
        d.pop("vote_percent_json", None)
        results.append(d)
    return _attach_nodes(conn, results)


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
    return _attach_nodes(conn, results)


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


def get_tag_sync_state(
    conn: sqlite3.Connection, topic_id: int, tag_id: int = 0,
) -> Optional[dict]:
    """Where the subtopic-mapping pass stopped for this node."""
    try:
        row = conn.execute(
            "SELECT * FROM tag_sync_state WHERE topic_id = ? AND tag_id = ?",
            (int(topic_id), int(tag_id)),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(row) if row else None


def update_tag_sync_state(
    conn: sqlite3.Connection, topic_id: int, tag_id: int = 0,
    last_page: int = 0, status: str = "running",
) -> None:
    """Record the mapping pass's cursor (tag_id 0 = whole-topic pass)."""
    with transaction(conn) as cur:
        cur.execute("""
            INSERT INTO tag_sync_state (topic_id, tag_id, last_page, status, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(topic_id, tag_id) DO UPDATE SET
                last_page = excluded.last_page,
                status = excluded.status,
                updated_at = CURRENT_TIMESTAMP
        """, (int(topic_id), int(tag_id), int(last_page), status))


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


def save_run_request(conn: sqlite3.Connection, payload: dict) -> None:
    """Remember the download the user asked for (survives a restart)."""
    now = datetime.now(timezone.utc).isoformat()
    with transaction(conn) as cur:
        cur.execute(
            """INSERT INTO run_request (id, payload, updated_at)
               VALUES (1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   payload = excluded.payload,
                   updated_at = excluded.updated_at""",
            (json.dumps(payload, ensure_ascii=False), now),
        )


def load_run_request(conn: sqlite3.Connection) -> Optional[dict]:
    if not _table_exists(conn, "run_request"):
        return None
    row = conn.execute("SELECT payload FROM run_request WHERE id = 1").fetchone()
    if not row:
        return None
    try:
        data = json.loads(row[0])
        return data if isinstance(data, dict) else None
    except (TypeError, ValueError):
        return None


def clear_run_request(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "run_request"):
        return
    with transaction(conn) as cur:
        cur.execute("DELETE FROM run_request WHERE id = 1")


def get_micro_state(
    conn: sqlite3.Connection, topic_id: int, tag_id: int,
) -> Optional[dict]:
    """Cursor of one subtopic («?tag=»), or None if never scraped."""
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM micro_state WHERE topic_id = ? AND tag_id = ?",
        (topic_id, tag_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def update_micro_state(
    conn: sqlite3.Connection,
    topic_id: int,
    tag_id: int,
    last_page: int,
    total_pages: Optional[int] = None,
    status: str = "idle",
    error_message: Optional[str] = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with transaction(conn) as cur:
        cur.execute("""
            INSERT INTO micro_state
                (topic_id, tag_id, last_page, total_pages, status,
                 error_message, updated_at)
            VALUES (?, ?, ?, coalesce(?, 0), ?, ?, ?)
            ON CONFLICT(topic_id, tag_id) DO UPDATE SET
                last_page = excluded.last_page,
                total_pages = CASE WHEN excluded.total_pages > 0
                                   THEN excluded.total_pages
                                   ELSE micro_state.total_pages END,
                status = excluded.status,
                error_message = excluded.error_message,
                updated_at = excluded.updated_at
        """, (topic_id, tag_id, last_page, total_pages, status,
              error_message, now))


def get_target_state(
    conn: sqlite3.Connection, topic_id: int, tag_id: Optional[int] = None,
) -> Optional[dict]:
    """Cursor for a scrape target: a whole topic or one of its subtopics."""
    if tag_id is None:
        return get_scrape_state(conn, topic_id)
    return get_micro_state(conn, topic_id, int(tag_id))


# Statuses that still have unsaved pages: the run may be safely continued.
RESUMABLE_STATUSES = (
    "auth_required", "limit", "paused", "interrupted", "running", "stopping",
)
# Only these are continued automatically once a login is confirmed.  A guest
# quota (`limit`) is lifted by logging in; a paid-plan limit is not, but the
# site cannot be asked which one it was, and resuming re-hits it at once —
# which is exactly what the user expects after logging in.
LOGIN_RESUME_STATUSES = ("auth_required", "limit")


def pending_resume_targets(
    conn: sqlite3.Connection, statuses: tuple[str, ...] = RESUMABLE_STATUSES,
) -> list[dict]:
    """Unfinished scrape targets, in the site tree's order.

    The continuation that used to live only in the server process (so a
    restart lost it) is reconstructed from the stored cursors.  Each item is
    ``{topic_id, tag_id, subtopic_title, last_page, status, label}``.
    """
    if not statuses:
        return []
    marks = ",".join("?" for _ in statuses)
    rows: list[dict] = []
    for r in conn.execute(
        f"SELECT * FROM scrape_state WHERE status IN ({marks})", statuses
    ).fetchall():
        rows.append({
            "topic_id": int(r["topic_id"]), "tag_id": None,
            "subtopic_title": None, "last_page": int(r["last_page"] or 0),
            "status": r["status"], "error_message": r["error_message"] or "",
        })
    if _table_exists(conn, "micro_state"):
        for r in conn.execute(
            f"SELECT * FROM micro_state WHERE status IN ({marks})", statuses
        ).fetchall():
            rows.append({
                "topic_id": int(r["topic_id"]), "tag_id": int(r["tag_id"]),
                "subtopic_title": None, "last_page": int(r["last_page"] or 0),
                "status": r["status"], "error_message": r["error_message"] or "",
            })
    # Label each target with its tree title and keep the site's order.  Only
    # the pending nodes are looked up: this runs on every UI poll.
    wanted = [f"/qbank/topic-id-{it['topic_id']}" for it in rows]
    wanted += [f"/qbank/topic-id-{it['topic_id']}?tag={it['tag_id']}"
               for it in rows if it["tag_id"] is not None]
    order: dict[tuple[int, Optional[int]], tuple[int, str]] = {}
    if wanted:
        marks = ",".join("?" for _ in wanted)
        for r in conn.execute(
            f"SELECT source_url, title, position FROM categories "
            f"WHERE source_url IN ({marks})", wanted
        ).fetchall():
            m = _TOPIC_ID_RE.search(r["source_url"] or "")
            if not m:
                continue
            key = (int(m.group(1)), tag_id_of(r["source_url"]))
            order.setdefault(key, (int(r["position"] or 0), r["title"]))
    for item in rows:
        key = (item["topic_id"], item["tag_id"])
        item["label"] = order.get(key, (0, f"مبحث {item['topic_id']}"))[1]
        if item["tag_id"] is not None:
            item["subtopic_title"] = item["label"]
    tag_order = {k: v[0] for k, v in order.items()}
    rows.sort(key=lambda it: (tag_order.get((it["topic_id"], None), 9999),
                              it["tag_id"] is not None,
                              tag_order.get((it["topic_id"], it["tag_id"]), 9999)))
    return rows


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def set_user_answer(
    conn: sqlite3.Connection,
    question_id: int,
    selected_option: Optional[int],
) -> None:
    """Record user's answered option for interactive offline practice.

    ``None`` clears the answer: «پاسخ مجدد» in the UI must undo the stored
    value too, otherwise the archive keeps counting the answer as attempted.
    """
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


def set_bookmark(conn: sqlite3.Connection, question_id: int, bookmarked: bool) -> bool:
    """Set the bookmark flag explicitly (idempotent).

    The UI already knows the target state, so a lost/duplicated request must
    not flip the flag back and forth the way ``toggle_bookmark`` would.
    """
    value = 1 if bookmarked else 0
    with transaction(conn) as cur:
        cur.execute(
            "UPDATE questions SET bookmarked = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (value, question_id),
        )
    return bool(value)


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


def category_title(conn: sqlite3.Connection, topic_id: int,
                   tag_id: Optional[int] = None) -> Optional[str]:
    """The tree's own title for a topic or subtopic node.

    The UI already knows it, but the server must not trust a request body for
    a name that lives in the database: a mis-encoded or stale client title
    would be stored next to every question of that subtopic.
    """
    url = f"/qbank/topic-id-{int(topic_id)}"
    if tag_id is not None:
        url += f"?tag={int(tag_id)}"
    row = conn.execute(
        "SELECT title FROM categories WHERE source_url = ?", (url,)
    ).fetchone()
    return row[0] if row else None


def root_rank(title: str) -> int:
    """Print/browse order the user asked for: internship → pre-internship →
    authored, then everything else in the site's own order."""
    t = title or ""
    if "دستیاری" in t:
        return 0
    if "پره" in t and "انترنی" in t:
        return 1
    if "تالیفی" in t:
        return 2
    return 3


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
    roots.sort(key=lambda n: (root_rank(n.get("title")),
                              n.get("position") or 0, n.get("id") or 0))
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


def build_topic_paths(conn: sqlite3.Connection) -> dict[str, str]:
    """Map every node's source_url → full path of Persian titles
    (e.g. '/qbank/topic-id-79' → 'طب داخلی » قلب » آریتمی‌ها')."""
    nodes = get_categories(conn)
    by_id = {n["id"]: n for n in nodes}

    def path_of(n: dict) -> str:
        parts = [n["title"]]
        p = n.get("parent_id")
        seen = {n["id"]}
        while p is not None and p in by_id and p not in seen:
            seen.add(p)
            parts.append(by_id[p]["title"])
            p = by_id[p].get("parent_id")
        return " » ".join(reversed(parts))

    return {n["source_url"]: path_of(n) for n in nodes}


def refresh_question_paths(conn: sqlite3.Connection, topic_id: Optional[int] = None) -> int:
    """Backfill questions.category_path from the discovered hierarchy.

    Returns the number of rows updated. Safe to run repeatedly.
    """
    paths = build_topic_paths(conn)
    if not paths:
        return 0
    updated = 0
    # One transaction for the whole pass: a discovered tree easily holds
    # hundreds of nodes, and a commit per node meant hundreds of fsyncs.
    with transaction(conn) as cur:
        for node_url, path in paths.items():
            # Micro-topic (?tag=) leaves share their parent's question pool;
            # using the last one would brand every question with one tag.
            if "?tag=" in (node_url or ""):
                continue
            m = _TOPIC_ID_RE.search(node_url or "")
            if not m:
                continue
            tid = int(m.group(1))
            if topic_id is not None and tid != topic_id:
                continue
            cur.execute(
                "UPDATE questions SET category_path = ? "
                "WHERE topic_id = ? AND (category_path IS NULL OR category_path != ?)",
                (path, tid, path),
            )
            updated += max(cur.rowcount, 0)
    return updated


# Placeholder tree used when the site hierarchy is unreachable: every
# stored topic still gets a node so the UI can group its questions.
FALLBACK_ROOT_SOURCE = "medo:uncategorized"
FALLBACK_ROOT_TITLE = "دسته‌بندی‌نشده"


def _cleanup_fallback_root(conn: sqlite3.Connection) -> None:
    """Delete the placeholder root once no node hangs off it anymore."""
    with transaction(conn) as cur:
        cur.execute(
            """DELETE FROM categories
               WHERE source_url = ?
                 AND id NOT IN (SELECT parent_id FROM categories
                                WHERE parent_id IS NOT NULL)""",
            (FALLBACK_ROOT_SOURCE,),
        )


def prune_placeholder_nodes(conn: sqlite3.Connection) -> int:
    """Remove empty placeholder branches left behind by older crawls.

    Two kinds only, and only while they have no children:

    * ``…/by-year`` nodes — the site's «به تفکیک دوره و سال» route answers 502
      so these branches can never fill;
    * ``medo:path:…`` chains written from an on-page breadcrumb once the real
      topic is re-parented under the discovered tree.

    The «دسته‌بندی‌نشده» root is pruned only when nothing hangs off it.
    Returns the number of deleted rows. Safe to call repeatedly.
    """
    deleted = 0
    # Repeat: deleting a leaf can make its parent childless (year → lesson),
    # so one pass would leave every other level behind.
    for _ in range(16):
        removed_this_pass = 0
        with transaction(conn) as cur:
            cur.execute(
                """SELECT id FROM categories
                   WHERE source_url LIKE '%/by-year%'
                      OR source_url LIKE 'medo:path:%'"""
            )
            candidates = [int(row[0]) for row in cur.fetchall()]
            for node_id in candidates:
                child = cur.execute(
                    "SELECT 1 FROM categories WHERE parent_id = ? LIMIT 1", (node_id,)
                ).fetchone()
                if child:
                    continue
                cur.execute("DELETE FROM categories WHERE id = ?", (node_id,))
                removed_this_pass += 1
            cur.execute(
                """DELETE FROM categories
                   WHERE source_url = ?
                     AND id NOT IN (SELECT parent_id FROM categories
                                    WHERE parent_id IS NOT NULL)""",
                (FALLBACK_ROOT_SOURCE,),
            )
            removed_this_pass += cur.rowcount
        deleted += removed_this_pass
        if not removed_this_pass:
            break
    return deleted


def ensure_fallback_topic_nodes(conn: sqlite3.Connection) -> int:
    """Give every topic with stored questions a node in the tree.

    Extracted questions must be groupable even when the site's hierarchy
    was never (or could not be) fetched. Creates a «دسته‌بندی‌نشده» root
    plus one «مبحث N» node per topic not present in ``categories`` — using
    the topic's real ``/qbank/topic-id-N`` source_url, so a later real
    classification (discover or on-page breadcrumb) replaces it in place.
    Idempotent; returns how many nodes were created.
    """
    rows = conn.execute(
        "SELECT topic_id, count(*) FROM questions "
        "WHERE topic_id IS NOT NULL GROUP BY topic_id"
    ).fetchall()
    if not rows:
        return 0
    counts = {int(r[0]): int(r[1]) for r in rows}
    existing = set()
    for (url,) in conn.execute("SELECT source_url FROM categories").fetchall():
        m = _TOPIC_ID_RE.search(url or "")
        # A subtopic («?tag=») shares its parent's topic id but is not the
        # topic node: counting it as one left the topic itself missing from
        # the tree, so its questions had no row to be grouped under.
        if m and not _TAG_ID_RE.search(url or ""):
            existing.add(int(m.group(1)))
    missing = [t for t in counts if t not in existing]
    if not missing:
        _cleanup_fallback_root(conn)
        return 0
    root_id = upsert_category(
        conn, kind="category", title=FALLBACK_ROOT_TITLE,
        source_url=FALLBACK_ROOT_SOURCE, position=999,
    )
    for tid in sorted(missing):
        upsert_category(
            conn, kind="topic", title=f"مبحث {tid}",
            source_url=f"/qbank/topic-id-{tid}", parent_id=root_id,
            position=999, question_count=counts.get(tid, 0),
        )
    refresh_question_paths(conn)
    return len(missing)


_TREE_CAT_RE = re.compile(r"/qbank/cat-id-\d+")


def is_tree_backed_topic(conn: sqlite3.Connection, topic_id: int) -> bool:
    """True when the topic node already hangs off a discovered category.

    Such a node must never be re-parented by a breadcrumb pass: discovery put
    it under its real category (e.g. «دستیاری پزشکی » قلب»), while a
    breadcrumb chain would move it under «دستیاری پزشکی » 1405 » قلب» and
    split the archive away from the tree.
    """
    row = conn.execute(
        "SELECT parent_id FROM categories WHERE source_url = ?",
        (f"/qbank/topic-id-{topic_id}",),
    ).fetchone()
    if not row:
        return False
    parent = row["parent_id"]
    seen: set[int] = set()
    for _ in range(12):
        if parent is None or parent in seen:
            return False
        seen.add(int(parent))
        node = conn.execute(
            "SELECT source_url, parent_id FROM categories WHERE id = ?",
            (parent,),
        ).fetchone()
        if not node:
            return False
        if _TREE_CAT_RE.search(node["source_url"] or ""):
            return True
        parent = node["parent_id"]
    return False


def ensure_topic_path(conn: sqlite3.Connection, topic_id: int,
                      segments: list[str]) -> str:
    """Classify one topic from its on-page breadcrumb (no site tree needed).

    ``segments`` are the Persian titles in path order as shown on the
    question page itself, e.g. ['دستیاری پزشکی', '1405', 'غدد',
    'دیابت و هایپوگلایسمی'] — category, year, section, topic (the middle
    parts are optional). Reuses an existing root with the same title when
    one is already known (so breadcrumb chains attach to the discovered
    tree instead of duplicating it), builds the missing chain otherwise,
    then re-points the real topic node at it, rewrites this topic's
    ``questions.category_path`` and cleans up the placeholder root.
    Returns the stored path.
    """
    segs = [str(s).strip() for s in (segments or []) if str(s).strip()]
    if not segs:
        return ""

    topic_url = f"/qbank/topic-id-{topic_id}"
    existing = conn.execute(
        "SELECT id FROM categories WHERE source_url = ?", (topic_url,)
    ).fetchone()
    if existing:
        # The node already exists (discovery, fallback or an earlier pass).
        # Re-parenting it from a page breadcrumb is what used to split the
        # archive out of the real tree, so only the stored paths follow.
        refresh_question_paths(conn, topic_id)
        stored = build_topic_paths(conn).get(topic_url)
        return stored or " » ".join(segs)

    parent_id: Optional[int] = None
    row = conn.execute(
        "SELECT id FROM categories WHERE parent_id IS NULL "
        "AND kind = 'category' AND title = ? ORDER BY id LIMIT 1",
        (segs[0],),
    ).fetchone()
    if row:
        parent_id = int(row[0])
    else:
        parent_id = upsert_category(
            conn, kind="category", title=segs[0],
            source_url=f"medo:path:{segs[0]}", position=999,
        )

    chain = [segs[0]]
    for seg in segs[1:-1]:
        chain.append(seg)
        parent_id = upsert_category(
            conn, kind="section", title=seg,
            source_url="medo:path:" + "|".join(chain),
            parent_id=parent_id, position=999,
        )

    upsert_category(
        conn, kind="topic", title=segs[-1],
        source_url=f"/qbank/topic-id-{topic_id}",
        parent_id=parent_id, position=999,
    )
    refresh_question_paths(conn, topic_id)
    _cleanup_fallback_root(conn)

    stored = build_topic_paths(conn).get(f"/qbank/topic-id-{topic_id}")
    return stored or " » ".join(segs)


def reconcile_categories(conn: sqlite3.Connection) -> dict:
    """Bring the stored hierarchy and every question's path back in sync.

    Run after a scrape or a discovery: new topics get a fallback node, and a
    re-discovered tree (renamed/moved sections) rewrites the stored
    ``category_path`` of questions that were filed under the old one — the
    sidebar reads the tree, the question badge reads the path, and the two
    used to disagree after a discovery.
    """
    created = ensure_fallback_topic_nodes(conn)
    updated = refresh_question_paths(conn)
    fixed = normalize_category_kinds(conn)
    return {"created": created, "paths_updated": updated, "kinds_fixed": fixed}


def normalize_category_kinds(conn: sqlite3.Connection) -> int:
    """Store the true node kind instead of the crawl-depth guess.

    Discovery assigns kinds by depth (category/lesson/section), which files a
    subtopic («?tag=») as a section and a breadcrumb-built chain as whatever
    the depth happened to be.  The URL says what the node is; the UI and the
    export both read this field.
    """
    fixed = 0
    with transaction(conn) as cur:
        rows = cur.execute(
            "SELECT id, kind, source_url FROM categories"
        ).fetchall()
        for r in rows:
            url = r["source_url"] or ""
            if tag_id_of(url) is not None:
                want = "subtopic"
            elif url.startswith("medo:path:"):
                # Breadcrumb chains: the head (no «|» separator) is a root
                # category, every deeper segment is a section.
                want = "category" if "|" not in url else "section"
            elif "/by-year" in url:
                want = "section"
            elif _TOPIC_ID_RE.search(url):
                want = "topic"
            elif "/qbank/cat-id-" in url:
                want = "category"
            else:
                continue
            if r["kind"] != want:
                cur.execute("UPDATE categories SET kind = ? WHERE id = ?",
                            (want, r["id"]))
                fixed += 1
    return fixed


def get_category_progress(conn: sqlite3.Connection) -> dict[str, dict]:
    """Per-node progress: {source_url: {scraped, status, total_pages, last_page}}.

    scraped = questions stored for that topic (or for that subtopic when the
    node is a «?tag=» filter); status comes from the matching cursor table
    ('completed' | 'running' | 'paused' | 'limit' | ... | None if never run).
    """
    cur = conn.cursor()
    # Counts come from question_nodes: one question can sit in several
    # subtopics, and its parent topic must still count it exactly once.
    by_topic: dict[int, int] = {}
    by_tag: dict[tuple[int, int], int] = {}
    if _table_exists(conn, "question_nodes"):
        for r in cur.execute(
            "SELECT topic_id, count(DISTINCT question_id) AS n "
            "FROM question_nodes GROUP BY topic_id"
        ).fetchall():
            by_topic[int(r["topic_id"])] = int(r["n"])
        for r in cur.execute(
            "SELECT topic_id, tag_id, count(DISTINCT question_id) AS n "
            "FROM question_nodes WHERE tag_id > 0 "
            "GROUP BY topic_id, tag_id"
        ).fetchall():
            by_tag[(int(r["topic_id"]), int(r["tag_id"]))] = int(r["n"])
    else:  # a database opened without init_db: read the old columns
        for r in cur.execute(
            "SELECT topic_id, count(*) AS n FROM questions "
            "WHERE topic_id IS NOT NULL GROUP BY topic_id"
        ).fetchall():
            by_topic[int(r["topic_id"])] = int(r["n"])
        cols = {r[1] for r in cur.execute("PRAGMA table_info(questions)").fetchall()}
        if "tag_id" in cols:
            for r in cur.execute(
                "SELECT topic_id, tag_id, count(*) AS n FROM questions "
                "WHERE topic_id IS NOT NULL AND tag_id IS NOT NULL "
                "GROUP BY topic_id, tag_id"
            ).fetchall():
                by_tag[(int(r["topic_id"]), int(r["tag_id"]))] = int(r["n"])

    states = {
        r["topic_id"]: dict(r)
        for r in (dict(x) for x in cur.execute("SELECT * FROM scrape_state").fetchall())
    }
    micro_states: dict[tuple[int, int], dict] = {}
    if _table_exists(conn, "micro_state"):
        micro_states = {
            (int(r["topic_id"]), int(r["tag_id"])): dict(r)
            for r in (dict(x) for x in cur.execute("SELECT * FROM micro_state").fetchall())
        }

    out: dict[str, dict] = {}
    for n in get_categories(conn):
        m = _TOPIC_ID_RE.search(n["source_url"] or "")
        tid = int(m.group(1)) if m else None
        tag = tag_id_of(n["source_url"])
        if tid is None:
            st = None
        elif tag is not None:
            st = micro_states.get((tid, tag))
        else:
            st = states.get(tid)
        out[n["source_url"]] = {
            "scraped": (by_tag.get((tid, tag), 0) if tag is not None
                        else by_topic.get(tid, 0)) if tid is not None else 0,
            "declared": n.get("question_count") or 0,
            "status": (st or {}).get("status"),
            "last_page": (st or {}).get("last_page", 0),
            "total_pages": (st or {}).get("total_pages", 0),
        }
    return out


def declared_questions(conn: sqlite3.Connection, topic_id: int,
                       tag_id: Optional[int] = None) -> int:
    """How many questions the site's tree says this node holds (0 = unknown)."""
    url = f"/qbank/topic-id-{int(topic_id)}"
    if tag_id is not None:
        url += f"?tag={int(tag_id)}"
    try:
        row = conn.execute(
            "SELECT question_count FROM categories WHERE source_url = ?", (url,)
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int((row[0] if row else 0) or 0)


def subtopic_targets(conn: sqlite3.Connection, topic_id: int) -> list[dict]:
    """The «?tag=» subtopics stored for a topic, in the tree's own order.

    A whole-topic page never says which subtopic a question belongs to, so a
    download queues these instead of the unfiltered topic when they exist.
    """
    try:
        rows = conn.execute(
            "SELECT title, source_url FROM categories "
            "WHERE source_url LIKE ? ORDER BY position, id",
            (f"/qbank/topic-id-{int(topic_id)}?tag=%",),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[dict] = []
    for row in rows:
        tag = tag_id_of(row["source_url"])
        if tag:
            out.append({"topic_id": int(topic_id), "tag_id": int(tag),
                        "subtopic_title": row["title"]})
    return out


def record_usage(
    conn: sqlite3.Connection,
    started_at: str,
    finished_at: Optional[str],
    byte_count: int,
    questions: int,
    status: str = "completed",
    targets: Optional[list[dict]] = None,
) -> None:
    """Store the measured network cost of one download run."""
    payload = json.dumps(targets or [], ensure_ascii=False)
    with transaction(conn) as cur:
        cur.execute(
            "INSERT INTO usage_log "
            "(started_at, finished_at, bytes, questions, status, targets) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (started_at, finished_at, int(byte_count or 0), int(questions or 0),
             status, payload),
        )


def get_usage_summary(conn: sqlite3.Connection) -> dict:
    """Internet usage so far, in bytes.

    ``avg_bytes_per_question`` prefers the measured per-run history; before any
    run has finished it falls back to the per-question measurements stored on
    the questions themselves.  0 means «not measured yet» — callers must not
    invent an estimate from it.
    """
    out = {
        "total_bytes": 0, "total_questions": 0,
        "today_bytes": 0, "today_questions": 0,
        "avg_bytes_per_question": 0, "runs": 0,
    }
    try:
        row = conn.execute(
            "SELECT coalesce(sum(bytes), 0), coalesce(sum(questions), 0), "
            "count(*) FROM usage_log"
        ).fetchone()
        out["total_bytes"], out["total_questions"], out["runs"] = (
            int(row[0] or 0), int(row[1] or 0), int(row[2] or 0))
        row = conn.execute(
            "SELECT coalesce(sum(bytes), 0), coalesce(sum(questions), 0) "
            "FROM usage_log "
            "WHERE date(started_at, 'localtime') = date('now', 'localtime')"
        ).fetchone()
        out["today_bytes"], out["today_questions"] = (
            int(row[0] or 0), int(row[1] or 0))
    except sqlite3.OperationalError:
        pass
    # Typical per-question cost = real traffic ÷ real questions over runs
    # large enough for their one-off start-up load to be amortised.  A 1–2
    # question run is excluded: its app-shell load would triple the average.
    try:
        row = conn.execute(
            "SELECT coalesce(sum(bytes), 0), coalesce(sum(questions), 0) "
            "FROM usage_log WHERE questions >= 3"
        ).fetchone()
        if int(row[1] or 0) > 0:
            out["avg_bytes_per_question"] = int(row[0]) // int(row[1])
    except sqlite3.OperationalError:
        pass
    # No whole-run history yet (e.g. an archive from before usage_log): fall
    # back to the per-question measurements once there are enough of them.
    if out["avg_bytes_per_question"] <= 0:
        try:
            row = conn.execute(
                "SELECT coalesce(avg(download_bytes), 0), count(*)"
                " FROM questions WHERE download_bytes > 0"
            ).fetchone()
            if int(row[1] or 0) >= 3:
                out["avg_bytes_per_question"] = int(row[0])
        except sqlite3.OperationalError:
            pass
    return out


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
