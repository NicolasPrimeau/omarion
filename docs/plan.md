# Omarion — Execution Plan

## Phase 1: Core Server (MVP)

- [x] Project scaffolding — uv, FastAPI, SQLite WAL, directory structure
- [x] DB schema + migrations (memory, tasks, messages, events tables)
- [x] Agent auth middleware (API key + agent_id header)
- [x] Memory endpoints (POST, GET, PATCH, DELETE, search)
- [x] Semantic search (sqlite-vec embeddings, cosine similarity)
- [x] Delta endpoint (/memory/delta?since=) for efficient context sync
- [x] Task endpoints (create, list, claim, complete, fail)
- [x] Message endpoints (send, inbox, read)
- [x] Event endpoints (emit, poll, SSE stream)
- [x] Session handoff (POST /sessions/handoff, GET /sessions/handoff/:agent_id)
- [x] .env setup + agent key seeding script (scripts/seed_keys.py)
- [x] Docker deployment on server (Dockerfile + docker-compose.yml)

## Phase 2: Archivist

- [x] Archivist agent scaffold (Claude API, async loop)
- [x] Conflict detection via embedding similarity on write
- [x] LLM merge call — semantic diff + produce canonical entry, record parents
- [x] Hourly synthesis pass — link discovery across agents
- [x] Confidence decay — entries not reinforced lose confidence over time
- [x] Promotion logic — scratch → memory → doc
- [x] Archivist writes synthesis docs back as agent_id=archivist

## Phase 3: MCP Adapter

- [x] MCP server wrapping core REST API
- [x] Tools: memory_write, memory_search, memory_delta, task_create, task_list, task_claim, task_complete, session_context, session_handoff
- [x] Claude Code integration — SSE (remote) and stdio (local) transports

## Phase 4: Client Integrations

- [x] MCP config documented for Claude Code (README — Option A SSE, Option B stdio)
- [x] Nimbus .mcp.json wired to omarion MCP (SSE transport)
- [x] Steward agent pre-registered, ready to connect via .mcp.json
- [x] Session handoff replacing server-sync skill

## Phase 5: Open Source Prep

- [x] README — problem statement, architecture diagram, quickstart, API reference
- [x] Docker Compose for self-hosting
- [x] Example integrations (raw Python client, AutoGen)
- [x] Participants endpoint (GET /participants — list agents + last_seen)
- [x] GitHub release (v0.1.0)
