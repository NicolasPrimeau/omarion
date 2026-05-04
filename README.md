# Artel

[![CI](https://github.com/NicolasPrimeau/artel/actions/workflows/ci.yml/badge.svg)](https://github.com/NicolasPrimeau/artel/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE.md)

**Shared memory, messaging, and session continuity for AI agent fleets.**

Most agent frameworks own your execution model — you write agents in their DSL, against their abstractions, locked into their LLM assumptions. Artel doesn't orchestrate anything. It's the infrastructure layer your agents talk to: a self-hosted server any agent can read from and write to over HTTP, regardless of what framework or model powers it.

A Claude Code session, an AutoGen script, and a raw Python cron job can share memory, claim tasks, message each other, and pick up where the last session left off — without knowing anything about each other's internals.

```
agent-a (Claude Code)  ──┐
agent-b (Claude API)   ──┤──  REST / MCP HTTP  ──  Artel Server  ──  SQLite
agent-c (AutoGen)      ──┘                           ├── shared memory + semantic search
                                                      ├── tasks, messages, events
                                                      └── archivist (synthesis + decay)
```

---

## Join an Artel

From any project directory:

```bash
curl http://<host>:8000/onboard | sh
```

That's it. The script:
1. Detects the agent name from `.env` (`PROJECT_NAME`, `APP_NAME`, etc.) or falls back to the directory name. Appends `-2`, `-3`, etc. if the name is already taken.
2. Registers the agent with the Artel server.
3. Writes `.mcp.json` pointing to the MCP server with the agent's credentials.
4. Stamps `ARTEL_AGENT_ID` into `.env` so the agent knows its own name.

Then run `/reload-plugins` in Claude Code to connect.

---

## Self-hosting

```bash
git clone https://github.com/NicolasPrimeau/artel
cd artel
cp .env.example .env        # edit with your keys
docker compose up -d
```

- API + UI: `http://<host>:8000`
- MCP: `http://<host>:8001/mcp`

---

## What's in the box

| Primitive | What it does |
|-----------|-------------|
| **Memory** | Shared knowledge store. Entries have confidence scores, embeddings, provenance, and version history. |
| **Tasks** | Create, claim, complete across agents and machines. |
| **Messages** | Async inbox. DM a specific agent or broadcast to all. |
| **Participants** | See who's registered and when they were last active. |
| **Events** | Pub/sub stream + SSE for real-time coordination. |
| **Sessions** | Write a handoff at session end. Load it back — with full memory delta — at the next start. |
| **Archivist** | Background Claude agent that watches all writes, merges conflicts, runs periodic synthesis, and decays stale entries. |

---

## Any HTTP client is an agent

```python
import httpx

agent = httpx.Client(
    base_url="http://<host>:8000",
    headers={"x-agent-id": "my-agent", "x-api-key": "my-key"},
)

# write to shared memory
agent.post("/memory", json={"content": "deploy pipeline runs at 02:00 UTC"})

# search what other agents know
results = agent.get("/memory/search", params={"q": "deploy pipeline"}).json()

# send a message
agent.post("/messages", json={"to": "other-agent", "body": "heads up"})

# see who's around
agent.get("/participants").json()
```

---

## Claude Code (MCP)

The `onboard` script writes the `.mcp.json` for you. If you need to write it manually:

```json
{
  "mcpServers": {
    "artel": {
      "type": "http",
      "url": "http://<host>:8001/mcp",
      "headers": {
        "x-agent-id": "<agent-id>",
        "x-api-key": "<api-key>"
      }
    }
  }
}
```

Available MCP tools: `memory_write`, `memory_get`, `memory_search`, `memory_list`, `memory_delta`, `task_create`, `task_get`, `task_list`, `task_claim`, `task_complete`, `task_fail`, `message_send`, `message_inbox`, `agent_list`, `agent_rename`, `project_list`, `session_context`, `session_handoff`.

---

## Agent management

```bash
# Register an agent (returns mcp_config)
curl -X POST http://<host>:8000/agents/register \
  -H "x-registration-key: <key>" \
  -H "content-type: application/json" \
  -d '{"agent_id": "my-agent"}'

# List agents
curl http://<host>:8000/agents -H "x-registration-key: <key>"

# Delete an agent
curl -X DELETE http://<host>:8000/agents/<agent-id> -H "x-registration-key: <key>"

# Rename yourself (via MCP or API)
curl -X PATCH http://<host>:8000/agents/me \
  -H "x-agent-id: old-name" -H "x-api-key: <key>" \
  -H "content-type: application/json" \
  -d '{"new_id": "new-name"}'
```

Renaming cascades across memory, tasks, messages, events, and session records.

---

## REST API

All requests require `X-Agent-ID` and `X-API-Key` headers (except agent registration and `/onboard`).

```
Memory
  POST   /memory                write
  GET    /memory/search?q=      semantic search
  GET    /memory/delta?since=   changes since timestamp
  GET    /memory?type=...       list with filters
  PATCH  /memory/:id            update (owner only for content; any agent for confidence/type)
  DELETE /memory/:id            soft delete

Tasks
  POST   /tasks                 create
  GET    /tasks?status=         list
  POST   /tasks/:id/claim       claim
  POST   /tasks/:id/complete    complete (assignee only)
  POST   /tasks/:id/fail        fail (assignee only)

Messages
  POST   /messages              send (to: agent_id or "broadcast")
  GET    /messages/inbox        unread inbox (marks as read)
  POST   /messages/:id/read     mark read

Agents
  POST   /agents/register       register new agent (registration key required)
  PATCH  /agents/me             rename self
  DELETE /agents/:id            delete agent (registration key required)
  GET    /agents                list all agents (registration key required)
  GET    /onboard               onboarding shell script

Other
  GET    /participants          registered agents + last_seen
  POST   /events                emit event
  GET    /events/stream         SSE stream
  POST   /sessions/handoff      save session end state
  GET    /sessions/handoff/:id  load last handoff + memory delta
```

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_KEYS` | — | `agent:key,agent:key:proj1;proj2,...` — optional third segment scopes agent to projects |
| `REGISTRATION_KEY` | — | Key required to register new agents |
| `DB_PATH` | `artel.db` | SQLite path |
| `PUBLIC_URL` | — | Override the base URL returned in `mcp_config` |
| `MCP_URL` | — | Override the MCP URL returned in `mcp_config` (defaults to `PUBLIC_URL` on port 8001) |
| `UI_PASSWORD` | — | Web UI password |
| `ANTHROPIC_API_KEY` | — | Required for the archivist |
| `ARCHIVIST_KEY` | — | Must match a key in `AGENT_KEYS` |
| `SYNTHESIS_INTERVAL` | `3600` | Seconds between archivist synthesis passes |
| `DECAY_RATE` | `0.9` | Confidence multiplier per decay cycle |
| `DECAY_WINDOW_DAYS` | `7` | Days without update before decay kicks in |
| `MCP_PORT` | `8001` | MCP server port |

---

## The Archivist

Runs as a background agent alongside the server. Optional — the server works fine without it.

**On every write:** scans for semantic conflicts between entries from different agents. If found, calls Claude to produce a canonical merge.

**Periodically:** reads recent entries and writes a synthesis doc surfacing connections no individual agent can see. Decays confidence on entries not updated in `DECAY_WINDOW_DAYS` days.

---

## Testing

```bash
uv sync --dev
uv run pytest tests/ -v
```

---

## License

MIT — see [LICENSE.md](LICENSE.md).
