# Omarion Protocol Spec

## Overview

Omarion is a blackboard architecture — agents read and write to a shared state space, react to what they find. No direct coupling between agents. The server is the single source of truth. Git is the audit log only.

## Agent Identity

Every request carries:
```
X-Agent-ID: nimbus
X-API-Key: <key>
```

No framework assumptions. Any HTTP client is a valid agent.

---

## Primitives

### 1. Memory

Persistent knowledge entries. Shared across all agents by default.

```
Entry {
  id:           uuid
  type:         memory | doc | task | reference | scratch
  agent_id:     str                  # who wrote it
  project:      str | null           # optional project scope
  scope:        private | shared | global
  content:      str                  # markdown
  embedding:    vector               # for semantic search
  confidence:   float 0.0–1.0        # degrades without reinforcement
  parents:      [uuid]               # merge provenance
  tags:         [str]
  created_at:   timestamp
  updated_at:   timestamp
  version:      int
}
```

**Endpoints:**
```
POST   /memory              write entry (dedup check via embedding similarity)
GET    /memory/:id          get entry
PATCH  /memory/:id          update entry
GET    /memory/search?q=    semantic + keyword search
GET    /memory/delta?since= entries changed since timestamp (for context skill)
DELETE /memory/:id          soft delete
```

**Conflict resolution:** When two agents write conflicting entries about the same subject, the archivist merges them with an LLM call. Both parents are recorded. The merge is a new versioned entry.

---

### 2. Tasks

Units of work that cross agent boundaries.

```
Task {
  id:           uuid
  title:        str
  description:  str
  status:       open | claimed | completed | failed
  created_by:   agent_id
  assigned_to:  agent_id | null
  project:      str | null
  priority:     low | normal | high
  due_at:       timestamp | null
  created_at:   timestamp
  updated_at:   timestamp
}
```

**Endpoints:**
```
POST   /tasks               create task
GET    /tasks?status=&agent= list tasks
POST   /tasks/:id/claim     claim a task
POST   /tasks/:id/complete  mark complete
POST   /tasks/:id/fail      mark failed
```

---

### 3. Messages

Async agent-to-agent inbox.

```
Message {
  id:           uuid
  from:         agent_id
  to:           agent_id | broadcast
  subject:      str
  body:         str
  read:         bool
  created_at:   timestamp
}
```

**Endpoints:**
```
POST   /messages            send message
GET    /messages/inbox      get unread messages for calling agent
POST   /messages/:id/read   mark read
```

---

### 4. Events

Pub/sub stream. Agents subscribe to event types and receive notifications.

```
Event {
  id:           uuid
  type:         memory.written | task.created | task.claimed |
                task.completed | message.received | archivist.synthesis
  agent_id:     str              # who emitted it
  payload:      json
  created_at:   timestamp
}
```

**Endpoints:**
```
GET    /events/stream       SSE stream (filter by type)
POST   /events              emit event
GET    /events?since=       poll recent events
```

---

## Archivist

An async Claude agent running on poseidon. Two modes:

**Immediate** (triggered by write events):
- Conflict detection: new entry vs existing entries via embedding similarity
- If conflict found: queue for merge

**Scheduled** (hourly):
- Holistic synthesis pass across all agents' recent writes
- Link discovery: surface connections between entries from different agents
- Confidence decay: reduce confidence on entries not reinforced recently
- Promotion: scratch → memory → doc based on reinforcement pattern
- Write synthesis docs back into shared memory as `agent_id: archivist`

---

## Session Handoff

At session end, any agent can POST a handoff:

```
POST /sessions/handoff
{
  "agent_id": "nimbus",
  "host": "poseidon",
  "summary": "...",
  "in_progress": ["task_id_1", "task_id_2"],
  "next_steps": ["..."],
  "memory_refs": ["entry_id_1"]
}
```

On session start:
```
GET /sessions/handoff/:agent_id
```

Returns latest handoff + delta of memory changes since last seen. Context skill becomes a single API call.

---

## Authentication

MVP: static API keys in config. Each agent has its own key tied to its `agent_id`.

---

## Self-Hosting

Single binary / `uv run` on poseidon. SQLite WAL mode handles concurrent reads/writes safely. No cloud dependency.
