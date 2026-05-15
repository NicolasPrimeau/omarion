SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS agents (
    id            TEXT PRIMARY KEY,
    api_key       TEXT NOT NULL UNIQUE,
    project       TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_seen_at  TEXT,
    role          TEXT NOT NULL DEFAULT 'agent' CHECK (role IN ('owner', 'agent'))
);

CREATE TABLE IF NOT EXISTS memory (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL CHECK (type IN ('memory','doc','directive')),
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
    deleted_at  TEXT,
    expires_at  TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    expected_outcome TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','claimed','completed','failed')),
    created_by       TEXT NOT NULL,
    assigned_to      TEXT,
    project          TEXT,
    priority         TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low','normal','high')),
    due_at           TEXT,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS task_comments (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'comment' CHECK (kind IN ('comment','claim','unclaim','complete','fail')),
    body        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
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

CREATE TABLE IF NOT EXISTS project_members (
    project_id  TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    joined_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (project_id, agent_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_agent     ON memory (agent_id);
CREATE INDEX IF NOT EXISTS idx_memory_project   ON memory (project);
CREATE INDEX IF NOT EXISTS idx_memory_updated   ON memory (updated_at);
CREATE INDEX IF NOT EXISTS idx_memory_deleted   ON memory (deleted_at);
CREATE INDEX IF NOT EXISTS idx_tasks_status     ON tasks (status);
CREATE INDEX IF NOT EXISTS idx_tasks_assigned   ON tasks (assigned_to);
CREATE INDEX IF NOT EXISTS idx_task_comments    ON task_comments (task_id, created_at);
CREATE TABLE IF NOT EXISTS message_reads (
    agent_id    TEXT NOT NULL,
    message_id  TEXT NOT NULL,
    PRIMARY KEY (agent_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_to      ON messages (to_agent, read);
CREATE INDEX IF NOT EXISTS idx_message_reads    ON message_reads (agent_id);
CREATE INDEX IF NOT EXISTS idx_events_created   ON events (created_at);
CREATE INDEX IF NOT EXISTS idx_handoff_agent    ON session_handoffs (agent_id, created_at);
CREATE INDEX IF NOT EXISTS idx_proj_members     ON project_members (agent_id);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_codes (
    code            TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL,
    api_key         TEXT NOT NULL,
    client_id       TEXT NOT NULL,
    code_challenge  TEXT,
    redirect_uri    TEXT NOT NULL,
    expires_at      REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_oauth_codes_expires ON oauth_codes (expires_at);

CREATE TABLE IF NOT EXISTS ui_sessions (
    token        TEXT PRIMARY KEY,
    created_at   REAL NOT NULL,
    last_seen_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ui_sessions_last_seen ON ui_sessions (last_seen_at);

CREATE TABLE IF NOT EXISTS feed_subscriptions (
    id           TEXT PRIMARY KEY,
    agent_id     TEXT NOT NULL,
    project      TEXT NOT NULL,
    url          TEXT NOT NULL,
    name         TEXT NOT NULL,
    tags         TEXT NOT NULL DEFAULT '[]',
    interval_min INTEGER NOT NULL DEFAULT 30,
    max_per_poll INTEGER NOT NULL DEFAULT 20,
    last_fetched_at TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_feed_subs_agent ON feed_subscriptions (agent_id);
CREATE INDEX IF NOT EXISTS idx_feed_subs_fetch ON feed_subscriptions (last_fetched_at);

CREATE TABLE IF NOT EXISTS feed_items_seen (
    feed_id    TEXT NOT NULL,
    item_guid  TEXT NOT NULL,
    seen_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (feed_id, item_guid)
);

CREATE TABLE IF NOT EXISTS mcp_notification_queue (
    id          TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    message     TEXT NOT NULL,
    queued_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    delivered_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mcp_notif_agent ON mcp_notification_queue (agent_id, delivered_at);
"""
