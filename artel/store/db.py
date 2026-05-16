import sqlite3

import sqlite_vec

from .schema import SCHEMA

_conn: sqlite3.Connection | None = None


def get_db(path: str = "artel.db") -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.enable_load_extension(True)
        sqlite_vec.load(_conn)
        _conn.enable_load_extension(False)
        _conn.executescript(SCHEMA)
        _migrate(_conn)
        _init_vec_table(_conn)
    return _conn


def _migrate(conn: sqlite3.Connection) -> None:
    agent_cols = {r[1] for r in conn.execute("PRAGMA table_info(agents)").fetchall()}
    if "project" not in agent_cols:
        conn.execute("ALTER TABLE agents ADD COLUMN project TEXT")
        conn.commit()
    if "last_seen_at" not in agent_cols:
        conn.execute("ALTER TABLE agents ADD COLUMN last_seen_at TEXT")
        conn.commit()
    task_cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "expected_outcome" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN expected_outcome TEXT NOT NULL DEFAULT ''")
        conn.commit()
    mem_cols = {r[1] for r in conn.execute("PRAGMA table_info(memory)").fetchall()}
    if "expires_at" not in mem_cols:
        conn.execute("ALTER TABLE memory ADD COLUMN expires_at TEXT")
        conn.commit()
    if "role" not in agent_cols:
        conn.execute(
            "ALTER TABLE agents ADD COLUMN role TEXT NOT NULL DEFAULT 'agent' CHECK (role IN ('owner', 'agent'))"
        )
        conn.commit()
    agents_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='agents'"
    ).fetchone()
    if agents_sql and "CHECK (role IN" in agents_sql[0]:
        conn.executescript(
            """
            PRAGMA foreign_keys=off;
            BEGIN;
            ALTER TABLE agents RENAME TO _agents_old;
            CREATE TABLE agents (
                id            TEXT PRIMARY KEY,
                api_key       TEXT NOT NULL UNIQUE,
                project       TEXT,
                created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                last_seen_at  TEXT,
                role          TEXT NOT NULL DEFAULT 'agent'
            );
            INSERT INTO agents (id, api_key, project, created_at, last_seen_at, role)
                SELECT id, api_key, project, created_at, last_seen_at, role FROM _agents_old;
            DROP TABLE _agents_old;
            COMMIT;
            PRAGMA foreign_keys=on;
            """
        )
        conn.commit()


def _init_vec_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec
        USING vec0(
            id TEXT PRIMARY KEY,
            embedding FLOAT[384]
        )
    """)
    conn.commit()
