"""SQLite schema and connection helpers for prism.

Schema mirrors the GEO tracking domain:
Multi-tenant: prism is a hosted GEO tool. A *tenant* is one client workspace —
the brand being tracked. Everything a tenant owns is scoped by tenant_id:
- brands: the tracked brand + its competitors (competitors link via brand_id)
- prompts: the questions repeatedly sent to answer engines
- runs: one (prompt, model, tenant) evaluation at a point in time
- mentions / citations: extracted from a run, isolated through runs.tenant_id
Engine keys (settings table) are global — set by the operator, shared by all
tenants. tenants.owner_id is scaffolding for per-user auth, added later.
"""

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("PRISM_DB_PATH", Path(__file__).resolve().parent.parent / "prism.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                  -- the tracked brand (client) display name
    slug TEXT NOT NULL UNIQUE,
    website TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    owner_id INTEGER                     -- auth scaffolding: scope per user when added
);

CREATE TABLE IF NOT EXISTS brands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    website TEXT DEFAULT '',
    aliases TEXT NOT NULL DEFAULT '',      -- newline-separated mention aliases
    is_own INTEGER NOT NULL DEFAULT 0,
    brand_id INTEGER REFERENCES brands(id), -- set for competitors -> the tracked brand
    tenant_id INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    tenant_id INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'api'   -- 'api' for LLM APIs, 'scraper' for scraped surfaces
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                 -- 'run_all', 'run_prompt'
    status TEXT NOT NULL DEFAULT 'queued',  -- queued|running|done|error|cancelled
    total INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL DEFAULT '',   -- JSON params (e.g. prompt_id)
    log TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_id INTEGER NOT NULL REFERENCES prompts(id),
    model_id INTEGER NOT NULL REFERENCES models(id),
    ran_at TEXT NOT NULL DEFAULT (datetime('now')),
    response_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ok',   -- 'ok' | 'error'
    error TEXT DEFAULT '',
    tenant_id INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
    UNIQUE (prompt_id, model_id, ran_at)
);
CREATE INDEX IF NOT EXISTS idx_runs_time ON runs(ran_at);
CREATE INDEX IF NOT EXISTS idx_runs_prompt ON runs(prompt_id, ran_at);

CREATE TABLE IF NOT EXISTS mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    brand_id INTEGER NOT NULL REFERENCES brands(id),
    position INTEGER,                    -- rank of first mention among all brands (1 = first), NULL if none
    count INTEGER NOT NULL DEFAULT 1,
    UNIQUE (run_id, brand_id)
);
CREATE INDEX IF NOT EXISTS idx_mentions_brand ON mentions(brand_id);

CREATE TABLE IF NOT EXISTS citations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    domain TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'other',
    UNIQUE (run_id, url)
);
CREATE INDEX IF NOT EXISTS idx_citations_domain ON citations(domain);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""


def init_db(path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial schema (idempotent)."""
    brands_cols = {r["name"] for r in conn.execute("PRAGMA table_info(brands)")}
    if "aliases" not in brands_cols:
        conn.execute("ALTER TABLE brands ADD COLUMN aliases TEXT NOT NULL DEFAULT ''")
    if "brand_id" not in brands_cols:
        conn.execute("ALTER TABLE brands ADD COLUMN brand_id INTEGER REFERENCES brands(id)")
    run_cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
    if "job_id" not in run_cols:
        conn.execute("ALTER TABLE runs ADD COLUMN job_id INTEGER")

    # Multi-tenant: ensure a default tenant exists and every row is scoped.
    # Note: SQLite can't ALTER-in a REFERENCES column with a non-NULL default,
    # so the migrated columns carry no FK (new tables created by SCHEMA do).
    if "tenant_id" not in brands_cols:
        conn.execute("ALTER TABLE brands ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1")
    prompt_cols = {r["name"] for r in conn.execute("PRAGMA table_info(prompts)")}
    if "tenant_id" not in prompt_cols:
        conn.execute("ALTER TABLE prompts ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1")
    if "tenant_id" not in run_cols:
        conn.execute("ALTER TABLE runs ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_runs_tenant ON runs(tenant_id, ran_at)")

    # Tenant 1 mirrors the pre-existing tracked brand (is_own = 1), if any.
    conn.execute(
        "INSERT OR IGNORE INTO tenants (id, name, slug, website) VALUES (1, 'Default', 'default', '')")
    own = conn.execute("SELECT name, website FROM brands WHERE is_own = 1 LIMIT 1").fetchone()
    if own:
        conn.execute(
            "UPDATE tenants SET name = ?, website = ? WHERE id = 1 AND name = 'Default'",
            (own["name"], own["website"]))


@contextmanager
def connect(path: Path | None = None, _retries: int = 6):
    # WAL lets readers proceed while the single writer works. Writers still
    # serialize, so a burst of concurrent writers (the job worker) can hit
    # SQLITE_BUSY even with busy_timeout — retry briefly on connect/commit.
    for attempt in range(_retries):
        conn = sqlite3.connect(str(path or DB_PATH), timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 15000")
            yield conn
            conn.commit()
        except sqlite3.OperationalError as exc:
            conn.close()
            if "locked" in str(exc).lower() and attempt < _retries - 1:
                time.sleep(0.05 * (attempt + 1))
                continue
            raise
        finally:
            conn.close()
        return


def q(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def q1(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()
