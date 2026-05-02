# Omarion

Harness-agnostic coordination layer for AI agents — shared memory, session continuity, agent-to-agent communication, and async synthesis across machines and LLM providers.

## What It Is

Any agent that can make HTTP calls can participate. Agents read and write memory, pass messages, claim tasks, and emit events. An async archivist watches all activity and synthesizes connections no individual agent can see.

```
Agents (any machine, any LLM framework)
  ↕  REST API  /  MCP
Omarion Server
  ├── FastAPI + SQLite WAL — shared state
  ├── sqlite-vec — semantic search
  ├── Archivist — async Claude agent, synthesis + conflict resolution
  └── MCP adapter — Claude Code integration
```

## Quickstart

```bash
# 1. Generate agent keys
python scripts/seed_keys.py

# 2. Add your Anthropic key to .env
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env

# 3. Start everything
docker compose up -d
```

Services:
- `http://localhost:8000` — REST API
- `http://localhost:8001/sse` — MCP server (SSE transport)

## Primitives

| Primitive | Purpose |
|-----------|---------|
| **Memory** | Persistent knowledge entries with embeddings, confidence, provenance |
| **Tasks** | Create/claim/complete units of work across agents |
| **Messages** | Async agent-to-agent inbox |
| **Events** | Pub/sub stream for real-time coordination |
| **Sessions** | Handoff context between sessions and machines |

## REST API

Every request requires:
```
X-Agent-ID: nimbus
X-API-Key: <key>
```

### Memory
```
POST   /memory                write entry
GET    /memory/search?q=      semantic search
GET    /memory/delta?since=   changes since timestamp
GET    /memory/:id            get entry
PATCH  /memory/:id            update
DELETE /memory/:id            soft delete
```

### Tasks
```
POST   /tasks                 create
GET    /tasks?status=&agent=  list
POST   /tasks/:id/claim       claim
POST   /tasks/:id/complete    complete
POST   /tasks/:id/fail        fail
```

### Messages
```
POST   /messages              send
GET    /messages/inbox        unread inbox
POST   /messages/:id/read     mark read
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
GET    /sessions/handoff/:agent_id    get context + memory delta
```

## Claude Code Integration (MCP)

### Option A: Remote SSE (recommended — runs on poseidon)

Add to `.claude/settings.json`:

```json
{
  "mcpServers": {
    "omarion": {
      "type": "sse",
      "url": "http://ARTEL_HOST:8001/sse"
    }
  }
}
```

The MCP server runs as `agent_id=nimbus` by default (set `MCP_AGENT_ID` in `.env` to change).

### Option B: Local stdio (per-session identity)

```json
{
  "mcpServers": {
    "omarion": {
      "command": "python",
      "args": ["-m", "omarion.mcp"],
      "env": {
        "OMARION_URL": "http://ARTEL_HOST:8000",
        "MCP_AGENT_ID": "nimbus",
        "MCP_AGENT_KEY": "<nimbus-key>"
      }
    }
  }
}
```

### Available MCP tools

| Tool | Description |
|------|-------------|
| `memory_write` | Write a memory entry |
| `memory_search` | Semantic search |
| `memory_delta` | Changes since timestamp |
| `task_create` | Create a task |
| `task_list` | List tasks |
| `task_claim` | Claim a task |
| `task_complete` | Complete a task |
| `session_context` | Get last handoff + memory delta |
| `session_handoff` | Save session end state |

## Archivist

The archivist is an async Claude agent (`agent_id=archivist`) that:

1. **Conflict detection** — on every `memory.written` event, searches for semantically similar entries from other agents and merges conflicts via LLM
2. **Hourly synthesis** — reads all entries from the last 24h, writes a synthesis doc surfacing connections no individual agent can see
3. **Confidence decay** — (planned) reduces confidence on stale entries

The archivist writes synthesis docs back into shared memory. Any agent can read them.

## Configuration

| Variable | Service | Default | Description |
|----------|---------|---------|-------------|
| `AGENT_KEYS` | server | — | `agent:key,agent:key,...` pairs |
| `DB_PATH` | server | `omarion.db` | SQLite path |
| `HOST` | server | `0.0.0.0` | Bind host |
| `PORT` | server | `8000` | Bind port |
| `ANTHROPIC_API_KEY` | archivist | — | Claude API key |
| `ARCHIVIST_KEY` | archivist | — | Must match key in AGENT_KEYS |
| `ARCHIVIST_ID` | archivist | `archivist` | Agent ID |
| `SYNTHESIS_INTERVAL` | archivist | `3600` | Seconds between synthesis runs |
| `OMARION_URL` | archivist, mcp | `http://localhost:8000` | API base URL |
| `MCP_AGENT_ID` | mcp | `mcp` | Agent identity for MCP connections |
| `MCP_AGENT_KEY` | mcp | — | API key for MCP agent |
| `MCP_TRANSPORT` | mcp | `stdio` | `stdio` or `sse` |
| `MCP_HOST` | mcp | `0.0.0.0` | SSE bind host |
| `MCP_PORT` | mcp | `8001` | SSE bind port |

## Agent Identity

Any HTTP client is a valid agent. No framework coupling.

```python
import httpx

client = httpx.Client(
    base_url="http://ARTEL_HOST:8000",
    headers={"x-agent-id": "my-agent", "x-api-key": "my-key"},
)

client.post("/memory", json={"content": "BuildData refresh pipeline is broken", "type": "memory"})
client.get("/memory/search", params={"q": "BuildData refresh"})
```

## Development

```bash
uv run python -m omarion.server      # REST API
uv run python -m omarion.archivist   # archivist
uv run python -m omarion.mcp         # MCP (stdio)
```
