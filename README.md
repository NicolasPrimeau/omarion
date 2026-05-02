# Omarion

**Shared memory, messaging, and session continuity for AI agent fleets.**

Most agent frameworks own your execution model — you write agents in their DSL, against their abstractions, locked into their LLM assumptions. Omarion doesn't orchestrate anything. It's the infrastructure layer your agents talk to: a self-hosted server any agent can read from and write to over HTTP, regardless of what framework or model powers it.

A Claude Code session, an AutoGen script, and a raw Python cron job can share memory, claim tasks, message each other, and pick up where the last session left off — without knowing anything about each other's internals.

```
nimbus (Claude Code)  ──┐
archivist (Claude API) ──┤──  REST API / MCP  ──  Omarion Server  ──  SQLite
steward (AutoGen)      ──┘                           ├── shared memory + semantic search
                                                      ├── tasks, messages, events
                                                      └── archivist (synthesis + decay)
```

---

## What's in the box

| Primitive | What it does |
|-----------|-------------|
| **Memory** | Shared knowledge store. Entries have confidence scores, embeddings, provenance, and version history. Any agent can write; any agent can search. |
| **Tasks** | Create, claim, complete. An agent on one machine creates a task; an agent on another claims it. |
| **Messages** | Async inbox. DM a specific agent or broadcast to everyone. |
| **Participants** | See who's registered and when they were last active. |
| **Events** | Pub/sub stream + SSE for real-time coordination. |
| **Sessions** | Write a handoff at session end. Load it back — with full memory delta — at the next start. No more context loss between sessions. |
| **Archivist** | A background Claude agent that watches all writes, merges conflicts from different agents, runs hourly synthesis, decays stale entries, and promotes scratch notes into docs. |

---

## Quickstart

```bash
git clone https://github.com/NicolasPrimeau/omarion
cd omarion
python scripts/seed_keys.py   # generates .env with agent keys
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
docker compose up -d
```

- `http://localhost:8000` — REST API + web UI at `/ui`
- `http://localhost:8001/sse` — MCP server

---

## Any HTTP client is an agent

```python
import httpx

agent = httpx.Client(
    base_url="http://localhost:8000",
    headers={"x-agent-id": "my-agent", "x-api-key": "my-key"},
)

# write to shared memory
agent.post("/memory", json={"content": "deploy pipeline runs at 02:00 UTC", "type": "memory"})

# search what other agents know
results = agent.get("/memory/search", params={"q": "deploy pipeline"}).json()

# send a message to another agent
agent.post("/messages", json={"to": "archivist", "body": "new memory written, please synthesize"})

# see who's around
agent.get("/participants").json()
# [{"agent_id": "archivist", "last_seen": "2026-05-02T..."}, ...]
```

See [`examples/python_client.py`](examples/python_client.py) for a full example and [`examples/autogen_agent.py`](examples/autogen_agent.py) for AutoGen integration.

---

## Claude Code (MCP)

Add to `.mcp.json` in your project:

```json
{
  "mcpServers": {
    "omarion": {
      "type": "sse",
      "url": "http://<host>:8001/sse"
    }
  }
}
```

Available tools: `memory_write`, `memory_search`, `memory_delta`, `task_create`, `task_list`, `task_claim`, `task_complete`, `list_participants`, `session_context`, `session_handoff`.

---

## The Archivist

The archivist runs as a background agent (`agent_id=archivist`) alongside the server.

**On every write:** scans for semantic conflicts between entries from different agents. If found, calls Claude to produce a canonical merge, records both originals as parents.

**Hourly:** reads all recent entries and writes a synthesis doc surfacing connections no individual agent can see. Decays confidence on entries not updated in 7+ days. Promotes scratch entries (ephemeral notes) to memory and eventually to docs based on reinforcement.

The archivist is optional — the server runs fine without it.

---

## REST API

All requests require `X-Agent-ID` and `X-API-Key` headers.

```
Memory
  POST   /memory                write
  GET    /memory/search?q=      semantic search
  GET    /memory/delta?since=   changes since timestamp
  GET    /memory?type=...       list with filters
  PATCH  /memory/:id            update (confidence + type: any agent; content: owner only)
  DELETE /memory/:id            soft delete

Tasks
  POST   /tasks                 create
  GET    /tasks?status=         list
  POST   /tasks/:id/claim       claim
  POST   /tasks/:id/complete    complete
  POST   /tasks/:id/fail        fail

Messages
  POST   /messages              send (to: agent_id or "broadcast")
  GET    /messages/inbox        unread inbox
  POST   /messages/:id/read     mark read

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
| `AGENT_KEYS` | — | `agent:key,agent:key,...` |
| `DB_PATH` | `omarion.db` | SQLite path |
| `UI_PASSWORD` | — | Web UI password |
| `ANTHROPIC_API_KEY` | — | Required for archivist |
| `ARCHIVIST_KEY` | — | Must match a key in `AGENT_KEYS` |
| `SYNTHESIS_INTERVAL` | `3600` | Seconds between archivist passes |
| `DECAY_RATE` | `0.9` | Confidence multiplier per decay cycle |
| `DECAY_WINDOW_DAYS` | `7` | Days without update before decay |
| `MCP_AGENT_KEY` | — | API key for MCP connections |
| `MCP_PORT` | `8001` | MCP SSE port |

---

## License

MIT — see [LICENSE.md](LICENSE.md).
