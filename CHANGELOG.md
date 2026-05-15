# Changelog

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
