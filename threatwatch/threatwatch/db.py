"""SQLite schema and connection helper for THREATWATCH.

Kept deliberately boring (plain SQLite, no ORM) so D.A.V.E.'s query.py can
read from it directly with a simple `sqlite3.connect()` — no new dependency
needed on the retrieval side for v1/v2.
"""
import sqlite3
from pathlib import Path
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS kev (
    cve_id TEXT PRIMARY KEY,
    vendor_project TEXT,
    product TEXT,
    vulnerability_name TEXT,
    date_added TEXT,
    short_description TEXT,
    required_action TEXT,
    due_date TEXT,
    known_ransomware_use TEXT,
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cves (
    cve_id TEXT PRIMARY KEY,
    published TEXT,
    last_modified TEXT,
    description TEXT,
    cvss_v3_score REAL,
    cvss_v3_severity TEXT,
    cwe_ids TEXT,
    source_identifier TEXT,
    raw_json TEXT,
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS attack_groups (
    stix_id TEXT PRIMARY KEY,
    name TEXT,
    aliases TEXT,
    description TEXT,
    country_hint TEXT,
    last_seen TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS attack_techniques (
    stix_id TEXT PRIMARY KEY,
    technique_id TEXT,
    name TEXT,
    tactic TEXT,
    description TEXT,
    last_seen TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    link TEXT NOT NULL UNIQUE,
    summary TEXT,
    published TEXT,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS otx_pulses (
    pulse_id TEXT PRIMARY KEY,
    name TEXT,
    description TEXT,
    author TEXT,
    tags TEXT,
    indicator_count INTEGER,
    created TEXT,
    modified TEXT,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS mitigations (
    cve_id TEXT PRIMARY KEY,
    guidance TEXT NOT NULL,
    model TEXT,
    generated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_briefs (
    brief_date TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'threat',  -- 'threat' (asset-relevant), 'news' (top industry stories + relevance notes), or 'pulse'
    brief TEXT NOT NULL,
    model TEXT,
    generated_at TEXT NOT NULL DEFAULT (datetime('now')),
    suggestions TEXT,  -- JSON array of auto-generated follow-up questions for the brief-chat box, or NULL for briefs saved before that feature existed
    PRIMARY KEY (brief_date, kind)
);

CREATE TABLE IF NOT EXISTS ingestion_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collector TEXT NOT NULL,
    status TEXT NOT NULL,
    records_in INTEGER DEFAULT 0,
    detail TEXT,
    run_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cves_published ON cves(published);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published);
CREATE INDEX IF NOT EXISTS idx_kev_date_added ON kev(date_added);
"""


def get_db_path() -> Path:
    p = Path(config.DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@contextmanager
def connect():
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(conn):
    """Schema patches for columns/keys added after a table already existed
    in the wild -- CREATE TABLE IF NOT EXISTS won't touch an existing
    table, so anything that changes an existing table's shape needs an
    explicit, idempotent patch here instead."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(daily_briefs)").fetchall()}
    if cols and "kind" not in cols:
        # Old schema: brief_date alone was the primary key (one brief/day).
        # New schema adds `kind` so a 'threat' brief and a 'news' brief can
        # coexist for the same date. Rebuild the table rather than ALTER,
        # since SQLite can't add a column into a composite primary key.
        conn.executescript("""
            ALTER TABLE daily_briefs RENAME TO daily_briefs_old;
            CREATE TABLE daily_briefs (
                brief_date TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'threat',
                brief TEXT NOT NULL,
                model TEXT,
                generated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (brief_date, kind)
            );
            INSERT INTO daily_briefs (brief_date, kind, brief, model, generated_at)
                SELECT brief_date, 'threat', brief, model, generated_at FROM daily_briefs_old;
            DROP TABLE daily_briefs_old;
        """)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(daily_briefs)").fetchall()}
    if cols and "suggestions" not in cols:
        # Plain ADD COLUMN is fine here -- unlike the kind migration above,
        # this doesn't touch the primary key, so no table rebuild needed.
        conn.execute("ALTER TABLE daily_briefs ADD COLUMN suggestions TEXT")


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def log_run(collector: str, status: str, records_in: int = 0, detail: str = ""):
    with connect() as conn:
        conn.execute(
            "INSERT INTO ingestion_log (collector, status, records_in, detail) VALUES (?, ?, ?, ?)",
            (collector, status, records_in, detail),
        )
