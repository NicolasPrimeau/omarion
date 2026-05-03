SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS agents (
    id          TEXT PRIMARY KEY,
    api_key     TEXT NOT NULL UNIQUE,
    project     TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS memory (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL CHECK (type IN ('memory','doc','task','reference','scratch')),
    agent_id    TEXT NOT NULL,
    project     TEXT,
    scope       TEXT NOT NULL DEFAULT 'shared' CHECK (scope IN ('private','shared','global')),
    content     TEXT NOT NULL,
    confidence  REAL NOT NULL DEFAULT 1.0,
    parents     TEXT NOT NULL DEFAULT '[]',
    tags        TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    version     INTEGER NOT NULL DEFAULT 1,
    deleted_at  TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','claimed','completed','failed')),
    created_by  TEXT NOT NULL,
    assigned_to TEXT,
    project     TEXT,
    priority    TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low','normal','high')),
    due_at      TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    from_agent  TEXT NOT NULL,
    to_agent    TEXT NOT NULL,
    subject     TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL,
    read        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS session_handoffs (
    id          TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    host        TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL,
    in_progress TEXT NOT NULL DEFAULT '[]',
    next_steps  TEXT NOT NULL DEFAULT '[]',
    memory_refs TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_memory_agent     ON memory (agent_id);
CREATE INDEX IF NOT EXISTS idx_memory_project   ON memory (project);
CREATE INDEX IF NOT EXISTS idx_memory_updated   ON memory (updated_at);
CREATE INDEX IF NOT EXISTS idx_memory_deleted   ON memory (deleted_at);
CREATE INDEX IF NOT EXISTS idx_tasks_status     ON tasks (status);
CREATE INDEX IF NOT EXISTS idx_tasks_assigned   ON tasks (assigned_to);
CREATE INDEX IF NOT EXISTS idx_messages_to      ON messages (to_agent, read);
CREATE INDEX IF NOT EXISTS idx_events_created   ON events (created_at);
CREATE INDEX IF NOT EXISTS idx_handoff_agent    ON session_handoffs (agent_id, created_at);
"""
