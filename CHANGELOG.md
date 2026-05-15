# Changelog

## [0.7.0] — 2026-05-15

### Owner role
- `role` column on agents table (`owner` | `agent`, default `agent`)
- UI agent auto-promoted to `owner` on startup
- Owner bypasses all ownership checks — memory patch/delete, task update/complete/fail/unclaim, agent rename/delete
- `role` exposed on registration response and participants list

### Directive entry type
- New `entry_type="directive"` for standing instructions that shape agent and archivist behavior
- Only `owner`-role agents can write directives
- Directive confidence locked at `1.0` — never decayed, never synthesized, never promoted
- Archivist loads directives as a preamble before synthesis, excluded from the synthesis pool
- Archivist detects conflicting directives (embedding similarity) and messages the UI agent
- Archivist emits `DIRECTIVE SUGGESTION:` lines in synthesis output — suggestions only, never auto-writes
- `expires_at` nullable field on all memory entries
- UI: directive cards in blue, pinned above docs and memories, lock icon prefix, write form gated to owner

### Tests
- 21 new scenario tests covering owner role, directive write gating, ownership bypass, and directive lifecycle

## [0.6.0] — 2026-05-15

### Feeds
- RSS/Atom feed subscriptions: `feed_subscribe`, `feed_unsubscribe`, `feed_list` MCP tools
- Feed items are automatically fetched and written as `unprocessed`-tagged memories for archivist triage

### MCP
- Notification queue persisted to SQLite — queued notifications survive server restarts

### Container
- Dropped standalone MCP daemon from container; MCP runs in-process (completed in 0.5.0, finalized here)

## [0.5.0] — 2026-05-14

### Tasks
- `task_unclaim` (REST + MCP) — release a claimed task back to open
- Per-task comment log: `POST/GET /tasks/:id/comments`. Lifecycle ops (`claim`, `unclaim`, `complete`, `fail`) accept an optional body recorded as a kind-tagged entry. `task_get` renders the comment log inline.

### Dashboard
- Unclaim button and comment-thread view in the task modal

### Fixes
- `/agents/register` response uses the actual server port (was hard-coded to a stale `8001`)
- `ui_agent_id` default corrected to `artel-ui` to match documented behavior

### Container
- Dropped the standalone MCP daemon and `supervisord` from the image; MCP is served in-process at `/mcp` on port 8000
- Removed `supervisor` runtime dep and `boto3` dev dep

### Repo hygiene
- Git history rewritten to remove personal dev scripts and a hostname reference. Tags `v0.1.0`–`v0.4.0` retired; `v0.5.0` is the clean baseline.
- Dropped 32MB of intermediate `.cast` recordings (the user-facing `.gif` versions remain)
- Removed `scripts/join.py` (duplicated the `/onboard` flow) and `scripts/migration/001_scope_rename.py` (one-shot migration that already ran)

## [0.1.0] — 2026-05-04

Initial public release.

### Core primitives
- Shared memory store with semantic search (sqlite-vec embeddings), confidence scores, version history, and soft delete
- Task queue with claim/complete/fail lifecycle across agents and machines
- Async agent-to-agent messaging with inbox and broadcast
- SSE event stream for real-time coordination
- Session handoff: save state at end of session, reload with full memory delta at next start

### Archivist
- Background Claude agent for conflict detection and resolution across agent writes
- Periodic synthesis: surfaces connections no individual agent can see
- Confidence decay for stale entries

### Infrastructure
- Self-hosted FastAPI + SQLite (WAL mode)
- MCP server over streamable HTTP for Claude Code integration
- One-line onboarding: `curl http://<host>:8000/onboard | sh`
- Docker Compose deployment with health checks
- Web UI for memory, tasks, messages, sessions, and participants
- Multi-tenant: project-scoped agents, memory, and tasks
