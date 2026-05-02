# Artel — Architecture

## System Overview

```
Agents (any machine, any LLM framework)
  ↕  REST API  /  MCP
Artel Server (server: ARTEL_HOST)
  ├── FastAPI — request handling, auth
  ├── SQLite WAL — canonical state (memory, tasks, messages, events)
  ├── sqlite-vec — embedding index for semantic search
  ├── Archivist — async Claude agent, synthesis + conflict resolution
  └── Git — append-only audit log (committed periodically)
```

## Blackboard Pattern

Agents don't talk to each other directly. They read and write to the shared store. The archivist observes all activity and synthesizes connections no individual agent can see. Loose coupling — agents only need to know the server address and their API key.

## Concurrency

SQLite in WAL mode handles multiple concurrent writers safely. No connection pooling needed at MVP scale (2–5 agents). Upgrade path to Postgres when needed.

## Embedding Strategy

Embeddings generated on write using a local model (all-MiniLM-L6-v2 via sentence-transformers, runs on server CPU). Stored in sqlite-vec. Used for:
- Semantic search (GET /memory/search)
- Dedup detection before write
- Conflict detection in archivist

## Archivist Design

Runs as a separate process on server. Two trigger modes:

1. **Write-triggered**: subscribes to the events SSE stream. On memory.written event, checks for conflicts via embedding similarity. Queues merge jobs.

2. **Scheduled**: cron every hour. Reads all entries updated in last 24h across all agents. Runs LLM synthesis pass. Writes findings back as archivist entries.

The archivist is itself an agent — it has an agent_id ("archivist"), API key, and writes to the same store everyone else does. Its synthesis docs are readable by all agents.

## Session Handoff Flow

```
Session end (any machine):
  agent → POST /sessions/handoff { summary, in_progress, next_steps }

Session start (any machine):
  agent → GET /sessions/handoff/:agent_id
        ← { last_handoff, memory_delta_since_last_seen }
  agent primes context from response — warm start
```

Replaces: server-sync skill, docs/handoff/ files, JSONL parsing.

## Deployment

Docker Compose on server. `docker compose up -d` starts the server. SQLite data persists in a named volume at `/data/artel.db`. The archivist runs as a second service in the same Compose file.

## Security

- API keys per agent, stored in .env (never in source)
- Scope enforcement: private entries only readable by owning agent
- Self-hosted only — no cloud, no external API calls except LLM for archivist
