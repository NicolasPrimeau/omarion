# Omarion

Harness-agnostic coordination layer for AI agents — shared memory, session continuity, agent-to-agent messaging, and async synthesis across machines and LLM providers.

```
Agents (any machine, any LLM framework)
  ↕  REST API  /  MCP
Omarion Server
  ├── FastAPI + SQLite WAL — shared state
  ├── sqlite-vec — semantic search + embeddings
  ├── Archivist — async Claude agent, synthesis + conflict resolution + memory lifecycle
  └── MCP adapter — Claude Code / any MCP client
```

## What It Does

- **Shared memory** — agents read and write to a common knowledge store. Entries have confidence scores, provenance, and semantic embeddings for search.
- **Tasks** — create, claim, and complete work units across agents. Any agent can pick up where another left off.
- **Messaging** — async agent-to-agent inbox. Direct messages or broadcast to all.
- **Participants** — discover who's registered and when they were last active.
- **Events** — pub/sub stream, including real-time SSE.
- **Session handoff** — at session end, write a handoff summary. On the next session start, load it back along with everything written since.
- **Archivist** — a background Claude agent that watches all activity, detects conflicts between agents, merges contradictions, runs hourly synthesis passes, decays stale entries, and promotes scratch notes to docs.

## Quickstart

```bash
# 1. Generate agent keys
python scripts/seed_keys.py

# 2. Add your Anthropic key
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env

# 3. Start everything
docker compose up -d
```

Services:
- `http://localhost:8000` — REST API + UI at `/ui`
- `http://localhost:8001/sse` — MCP server (SSE transport)

## Claude Code Integration

Add to `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "omarion": {
      "type": "sse",
      "url": "http://<omarion-host>:8001/sse"
    }
  }
}
```

Or stdio (per-session identity):

```json
{
  "mcpServers": {
    "omarion": {
      "command": "python",
      "args": ["-m", "omarion.mcp"],
      "env": {
        "OMARION_URL": "http://<omarion-host>:8000",
        "MCP_AGENT_ID": "nimbus",
        "MCP_AGENT_KEY": "<key>"
      }
    }
  }
}
```

### MCP Tools

| Tool | Description |
|------|-------------|
| `memory_write` | Write a memory entry |
| `memory_search` | Semantic search |
| `memory_delta` | Changes since timestamp |
| `task_create` | Create a task |
| `task_list` | List tasks |
| `task_claim` | Claim a task |
| `task_complete` | Complete a task |
| `list_participants` | List registered agents + last seen |
| `session_context` | Load last handoff + memory delta |
| `session_handoff` | Write session end state |

## REST API

Every request requires:
```
X-Agent-ID: <agent_id>
X-API-Key:  <key>
```

### Memory
```
POST   /memory                        write entry
GET    /memory?type=&updated_before=  list with filters
GET    /memory/search?q=              semantic search
GET    /memory/delta?since=           changes since timestamp
GET    /memory/:id                    get entry
PATCH  /memory/:id                    update (any agent can update confidence/type)
DELETE /memory/:id                    soft delete
```

### Tasks
```
POST   /tasks                 create
GET    /tasks?status=         list
POST   /tasks/:id/claim       claim
POST   /tasks/:id/complete    complete
POST   /tasks/:id/fail        fail
```

### Messages
```
POST   /messages              send (to: agent_id or "broadcast")
GET    /messages/inbox        unread inbox
POST   /messages/:id/read     mark read
```

### Participants
```
GET    /participants           list registered agents + last_seen timestamp
```

### Events
```
POST   /events                emit
GET    /events?since=&type=   poll
GET    /events/stream         SSE stream
```

### Sessions
```
POST   /sessions/handoff              save session end state
GET    /sessions/handoff/:agent_id    get last handoff + memory delta
```

## Raw Python Client

```python
import httpx

client = httpx.Client(
    base_url="http://localhost:8000",
    headers={"x-agent-id": "my-agent", "x-api-key": "my-key"},
)

client.post("/memory", json={"content": "BuildData refresh runs at 02:00 UTC", "type": "memory"})
client.get("/memory/search", params={"q": "BuildData"})
client.post("/messages", json={"to": "archivist", "body": "new memory written"})
client.get("/participants")
```

See `examples/python_client.py` for a full example. See `examples/autogen_agent.py` for AutoGen integration.

## Archivist

The archivist is an async Claude agent (`agent_id=archivist`) running alongside the server.

**On every `memory.written` event:**
- Checks new entry for semantic conflicts with existing entries from other agents
- If conflict found: merges both into a canonical entry with LLM, records provenance via `parents`

**Hourly scheduled pass:**
- Synthesis: reads all entries from the last 24h, writes a synthesis doc surfacing connections
- Decay: reduces confidence on entries not updated in 7+ days (floor: 0.05)
- Promotion: scratch entries > 48h old with confidence ≥ 0.5 → promoted to memory; memory entries with 3+ versions → promoted to doc

## Configuration

| Variable | Service | Default | Description |
|----------|---------|---------|-------------|
| `AGENT_KEYS` | server | — | `agent:key,agent:key,...` |
| `DB_PATH` | server | `omarion.db` | SQLite path |
| `HOST` | server | `0.0.0.0` | Bind host |
| `PORT` | server | `8000` | Bind port |
| `UI_PASSWORD` | server | — | Web UI password |
| `ANTHROPIC_API_KEY` | archivist | — | Claude API key |
| `ARCHIVIST_KEY` | archivist | — | Must match key in `AGENT_KEYS` |
| `SYNTHESIS_INTERVAL` | archivist | `3600` | Seconds between synthesis passes |
| `DECAY_RATE` | archivist | `0.9` | Confidence multiplier per decay cycle |
| `DECAY_FLOOR` | archivist | `0.05` | Minimum confidence before entry is inert |
| `DECAY_WINDOW_DAYS` | archivist | `7` | Days without update before decay kicks in |
| `PROMOTION_SCRATCH_AGE_HOURS` | archivist | `48` | Age threshold for scratch → memory |
| `PROMOTION_MEMORY_MIN_VERSION` | archivist | `3` | Version count for memory → doc |
| `OMARION_URL` | archivist, mcp | `http://localhost:8000` | API base URL |
| `MCP_AGENT_ID` | mcp | `mcp` | Agent identity for MCP connections |
| `MCP_AGENT_KEY` | mcp | — | API key for MCP agent |
| `MCP_TRANSPORT` | mcp | `stdio` | `stdio` or `sse` |
| `MCP_PORT` | mcp | `8001` | SSE bind port |

## License

MIT — see [LICENSE.md](LICENSE.md).
