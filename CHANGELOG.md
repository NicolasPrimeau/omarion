# Changelog

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
