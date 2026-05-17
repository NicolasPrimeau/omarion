# Changelog

## [0.12.0] — 2026-05-16

> Versions `0.10.1` (archivist task intelligence) and `0.11.0` (archivist audit log + Logs tab) were tagged from separate work and are not detailed here; this entry covers everything since `0.10.0` plus the mesh.

### Cross-instance mesh

Two Artel instances can mesh a project: each subscribes to the other's `/memory/feed.json` and memory replicates between them.

- Replication is a CRDT — anti-entropy keyed by each entry's immutable id, idempotent on ingest. It provably converges and cannot feed back on itself: re-receiving a known id is a no-op, an entry tagged with the receiver's own origin is skipped, edits settle last-writer-wins on `version`, deletes propagate as tombstones. Multi-hop safe, no central coordinator.
- New: stable per-instance id, `memory.origin` provenance, an `_artel` extension on the JSON Feed (`include_deleted` for tombstones). Non-Artel RSS/Atom feeds are unchanged. JSON Feed is the sync substrate; Atom stays external-only.
- **Mesh** UI tab + `/mesh` endpoints: owner links a peer (URL / project / peer credentials), lists peers with sync status, and detaches to stop syncing. Owner-gated; the peer API key is never returned. mDNS auto-discovery and a mutual handshake are future work — v1 is explicit owner linking, which is the consent.

### Archivist

- Fixed an unbounded duplicate-accumulator: `check_and_merge` excluded archivist-authored and parented entries as merge candidates, so a merged canonical entry could never absorb the next duplicate — each recurrence minted a new sibling. Now folds duplicates into the existing canonical and strips workflow tags from merged output.

### API

- Short-id prefix resolution: task and memory id routes accept an unambiguous ≥4-char prefix (exact match wins; ambiguous → `400`; unknown → `404`), so the truncated ids shown in listings are usable directly.

### Docs

- Repositioned to "a self-hosted, self-organizing mesh for AI agent fleets"; added an auth middleware reference and a mesh-convergence section.

## [0.10.0] — 2026-05-16

### RBAC — role-based access control

A single authorization layer now governs every endpoint. Roles, in ascending privilege: `viewer` < `agent` < `archivist` < `owner`.

- **Reader** (viewer+): all reads, search, list, streams
- **Actor** (agent+): all normal writes (memory, tasks, messages, sessions, events, feeds, projects, self rename/delete)
- **Owner**: delete / rename / list **any** agent
- **Memory curation** (archivist or owner): mutating another agent's memory, directive writes

### Security

- `DELETE`/`PATCH /agents/{id}` and `GET /agents` moved off the registration key onto **owner-only**. The registration key now *only* registers agents — it can no longer delete, rename, or list them. Open registration is preserved.
- `/ui` no longer walls users or ships the registration key to the browser. Unauthenticated visitors get the `sandbox-free-user` **viewer** principal: read-only, no registration key, no owner key. `UI_PASSWORD` elevates to `artel-ui`/owner. The dashboard hides mutation/admin controls and blocks writes client-side for viewers (defence-in-depth; the server is the real gate).
- `archivist` is a first-class role, seeded at boot, scoped to memory curation only — not agent administration. Fixes a latent bug: the archivist is a static `AGENT_KEYS` agent with no DB row, so `is_owner` was always `False` and its cross-agent prune/merge was silently blocked.

**Breaking:** clients that used the registration key to delete, rename, or list agents must now use an owner-role credential.

### MCP transport

- `/onboard` writes the MCP URL with a trailing slash (`/mcp/`); uvicorn trusts proxy headers. Fixes the `400` parse error caused by a redirect dropping the POST body behind a TLS-terminating proxy.
- Streamable HTTP transport runs **stateless** (`stateless_http=True`). Eliminates "Session not found" / "Missing session ID" across redeploys; inbox delivery still flows through the SQLite notification queue.

### UI

- Connect-agent command uses `curl -fsSL` to match the README.

### Migration

- The `agents.role` 2-value `CHECK` constraint is dropped via an idempotent table rebuild so `viewer` / `archivist` are insertable.

### Tests

- New `tests/scenarios/test_rbac.py`: viewer read-only, agent denied owner-admin, owner allowed, registration key cannot destroy, archivist cross-agent curation, archivist ≠ agent-admin, directives require curator.

### Tooling

- `boto3` added to the `dev` dependency group (used by the env-secrets sync script).

## [0.9.0] — 2026-05-16

Backfilled — shipped as the `v0.9.0` GitHub release; the CHANGELOG entry was missed at the time.

### Cross-Artel meshing

- `GET /memory/feed.atom` (Atom 1.0) and `GET /memory/feed.json` (JSON Feed 1.1), with `project` / `tag` / `type` / `limit` filters. Auth via `?agent_id=&api_key=` query params so another Artel's poller can subscribe without custom headers.
- Subscribe one Artel to another's `/memory/feed.json` via the existing feed subscription system — memory flows across instances with no central coordinator.
- Feed poller detects and parses JSON Feed (`application/feed+json`) on ingest, alongside Atom/RSS.

### UI

- Mobile + desktop rework: desktop sidebar nav, mobile hamburger drawer, 12 accent themes, consolidated settings modal, collapsible project sections.

### Reliability

- Graceful degradation when the fastembed ONNX model isn't cached: memory reads/writes work without embeddings; semantic search returns empty instead of crashing.

## [0.8.0] — 2026-05-15

### Archivist — curator model

The archivist is now a fully autonomous memory curator. Instead of writing prose synthesis documents, it runs a periodic LLM pass that outputs a structured JSON operation array and executes each operation directly on the memory store.

**Operations:**
- `merge` — synthesize two entries into one canonical record, delete both originals
- `promote` — promote a memory entry to `doc` in place
- `prune` — flag high-confidence entries for decay (lower to floor + tag `archivist-flagged`); hard-delete entries already at the decay floor
- `tag` — add tags to an entry to surface connections
- `adjust_confidence` — correct signal strength on an entry
- `split` — break one entry covering multiple topics into focused sub-entries, each with the original as parent
- `extract` — move a segment from one entry into another, rewriting both; deletes source if nothing remains
- `task` — create a task only for work genuinely requiring an external agent

Synthesis documents are gone. The memory store itself is the archivist's output.

### Directives
- Live DB migrated to allow `type='directive'` (was missing from CHECK constraint on running instance)

### UI
- Mobile: fixed 44px horizontal overflow in header bar — now wraps to two rows on narrow viewports
- Mobile: nav tab bar scrollable horizontally, all tabs reachable at 375px
- Mobile: owner badge and directive pill wrap cleanly in agent cards

### Tests
- 25 new tests: `split`, `extract`, conservative `prune` (unit + scenario)
- Full scenario coverage of curator ops via mocked LLM

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
