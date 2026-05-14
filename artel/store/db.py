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
    import os

    agent_cols = {r[1] for r in conn.execute("PRAGMA table_info(agents)").fetchall()}
    if "project" not in agent_cols:
        conn.execute("ALTER TABLE agents ADD COLUMN project TEXT")
        conn.commit()
    if "last_seen_at" not in agent_cols:
        conn.execute("ALTER TABLE agents ADD COLUMN last_seen_at TEXT")
        conn.commit()
    if "role" not in agent_cols:
        conn.execute("ALTER TABLE agents ADD COLUMN role TEXT NOT NULL DEFAULT 'member'")
        ui_agent_id = os.getenv("UI_AGENT_ID", "nimbus")
        conn.execute("UPDATE agents SET role='admin' WHERE id=?", (ui_agent_id,))
        conn.commit()
    task_cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "expected_outcome" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN expected_outcome TEXT NOT NULL DEFAULT ''")
        conn.commit()
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='task_comments'"
    ).fetchone()
    if row and "'reopen'" not in row["sql"]:
        conn.executescript("""
            ALTER TABLE task_comments RENAME TO task_comments_old;
            CREATE TABLE task_comments (
                id          TEXT PRIMARY KEY,
                task_id     TEXT NOT NULL,
                agent_id    TEXT NOT NULL,
                kind        TEXT NOT NULL DEFAULT 'comment' CHECK (kind IN ('comment','claim','unclaim','complete','fail','reopen')),
                body        TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            INSERT INTO task_comments SELECT * FROM task_comments_old;
            DROP TABLE task_comments_old;
            CREATE INDEX IF NOT EXISTS idx_task_comments ON task_comments (task_id, created_at);
        """)
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
