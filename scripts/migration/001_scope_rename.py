#!/usr/bin/env python3
"""
Migrate memory scope values from (private, shared, global) to (agent, project).
Also creates the project_members table if it doesn't exist.

SQLite doesn't allow ALTER COLUMN for CHECK constraints, so we rebuild the memory table.

Usage:
    python scripts/migration/001_scope_rename.py [--db /path/to/artel.db]
"""

import argparse
import sqlite3
from pathlib import Path

ARTEL_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB = ARTEL_ROOT / "artel.db"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB), help="path to artel.db")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=OFF")

    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS project_members (
                project_id  TEXT NOT NULL,
                agent_id    TEXT NOT NULL,
                joined_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                PRIMARY KEY (project_id, agent_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_proj_members ON project_members (agent_id)")

        private_count = conn.execute(
            "SELECT COUNT(*) FROM memory WHERE scope = 'private'"
        ).fetchone()[0]
        shared_count = conn.execute(
            "SELECT COUNT(*) FROM memory WHERE scope IN ('shared', 'global')"
        ).fetchone()[0]

        conn.execute("DROP TABLE IF EXISTS memory_new")
        conn.execute("""
            CREATE TABLE memory_new (
                id          TEXT PRIMARY KEY,
                type        TEXT NOT NULL CHECK (type IN ('memory','doc')),
                agent_id    TEXT NOT NULL,
                project     TEXT,
                scope       TEXT NOT NULL DEFAULT 'project' CHECK (scope IN ('agent','project')),
                content     TEXT NOT NULL,
                confidence  REAL NOT NULL DEFAULT 1.0,
                parents     TEXT NOT NULL DEFAULT '[]',
                tags        TEXT NOT NULL DEFAULT '[]',
                created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                version     INTEGER NOT NULL DEFAULT 1,
                deleted_at  TEXT
            )
        """)
        conn.execute("""
            INSERT INTO memory_new
            SELECT
                id,
                CASE type WHEN 'doc' THEN 'doc' ELSE 'memory' END,
                agent_id, project,
                CASE scope
                    WHEN 'private' THEN 'agent'
                    ELSE 'project'
                END,
                content, confidence, parents, tags,
                created_at, updated_at, version, deleted_at
            FROM memory
        """)
        conn.execute("DROP TABLE memory")
        conn.execute("ALTER TABLE memory_new RENAME TO memory")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_agent   ON memory (agent_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_project ON memory (project)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_updated ON memory (updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_deleted ON memory (deleted_at)")

    conn.execute("PRAGMA foreign_keys=ON")
    print(f"migrated {private_count} private → agent")
    print(f"migrated {shared_count} shared/global → project")
    print("created project_members table")
    print("rebuilt memory table with updated CHECK constraint")
    conn.close()


if __name__ == "__main__":
    main()
