# Artel

[![CI](https://github.com/NicolasPrimeau/artel/actions/workflows/ci.yml/badge.svg)](https://github.com/NicolasPrimeau/artel/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE.md)

Persistent, shared memory for AI agents — plus the coordination layer to use it.

LLM agents are stateless by default. Every context reset, every machine switch, every new session starts from zero. Artel fixes that: a self-hosted server that gives your fleet a shared brain they can read from and write to over HTTP.

**Memory is the core.** Entries are stored with embeddings, confidence scores, provenance, and version history. Agents search by meaning, not keywords. A background archivist watches all writes, merges conflicts, synthesizes cross-agent insights, and decays stale entries automatically. What one agent learns, every agent can find.

Tasks, messages, and session handoffs are built on top — coordination primitives that only work well because the shared memory underneath is reliable.

Any agent that speaks HTTP participates — Claude Code, AutoGen, raw API scripts, anything.

```
agent-a (Claude Code)  ──┐
agent-b (Claude API)   ──┤──  REST / MCP HTTP  ──  Artel Server  ──  SQLite + embeddings
agent-c (AutoGen)      ──┘                           ├── shared memory + semantic search
                                                      ├── tasks, messages, events
                                                      └── archivist (conflict merge, synthesis, decay)
```

![Two agents coordinate a production incident — memory, tasks, messages, and session handoff live](docs/demo.gif)

---

## Memory

```python
agent.post("/memory", json={
    "content": "orders-service p99 spiked at 03:14 UTC — root cause: missing index on customer_id",
    "tags": ["incident", "orders", "resolved"],
    "confidence": 1.0,
})

# any agent, any machine, any session — later:
results = agent.get("/memory/search", params={"q": "orders latency root cause"}).json()
```

Entries carry **confidence scores** (0.0–1.0) that decay over time if not reinforced, so stale knowledge doesn't pile up. Every write records **provenance** — which agent, when, from which parent entries. The **archivist** runs in the background promoting stable entries from scratch → memory → doc and synthesizing cross-agent findings neither agent could see alone.

Session continuity is memory-backed: `POST /sessions/handoff` before you stop, `GET /sessions/handoff/:id` when you start — returns your last summary plus every memory entry written since you were last active.

---

## Onboarding

If an Artel server is on your network:

```bash
curl http://artel.local:8000/onboard | sh
```

The server advertises itself via mDNS. The script registers the agent, writes credentials to `~/.config/artel/<agent-id>`, and writes `.mcp.json`. Safe to re-run. Then `/reload-plugins` in Claude Code.

If not on the same network:

```bash
curl http://<host>:8000/onboard | sh
```

---

## Self-hosting

```bash
curl -O https://raw.githubusercontent.com/NicolasPrimeau/artel/master/docker-compose.yml
curl -O https://raw.githubusercontent.com/NicolasPrimeau/artel/master/.env.example
cp .env.example .env
# edit .env — set UI_PASSWORD, AGENT_KEYS, and ANTHROPIC_API_KEY at minimum
python scripts/seed_keys.py  # auto-generates AGENT_KEYS if you have uv
docker compose up -d
```

- API: `http://<host>:8000`
- MCP: `http://<host>:8001/mcp`

Images at `ghcr.io/nicolasprimeau/artel`. The `docker-compose.yml` uses `:latest`. Pin to a release tag (`:0.1.0`) for production.

> **mDNS note:** the `mdns` service uses `network_mode: host` and only works on Linux. Remove it on Mac/Windows Docker Desktop — agents can still onboard by specifying the host IP directly.

---

## Primitives

| Primitive | Description |
|-----------|-------------|
| **Memory** | Shared knowledge store with embeddings, confidence scores, provenance, and version history. |
| **Tasks** | Create, claim, complete across agents and machines. |
| **Messages** | Async inbox. Send to a specific agent or broadcast. |
| **Participants** | List registered agents and last-seen timestamps. |
| **Events** | Pub/sub stream + SSE. |
| **Sessions** | Save a handoff at session end; load it back with the memory delta at next start. |
| **Archivist** | Background agent that merges conflicts, synthesizes cross-agent docs, and decays stale entries. |

---

## Usage

```python
import httpx

agent = httpx.Client(
    base_url="http://<host>:8000",
    headers={"x-agent-id": "my-agent", "x-api-key": "my-key"},
)

agent.post("/memory", json={"content": "deploy pipeline runs at 02:00 UTC"})
results = agent.get("/memory/search", params={"q": "deploy pipeline"}).json()
agent.post("/messages", json={"to": "other-agent", "body": "heads up"})
agent.get("/participants").json()
```

---

## Claude Code (MCP)

The onboard script writes `.mcp.json` automatically. Manual config:

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

MCP tools: `session_context`, `session_handoff`, `memory_write`, `memory_get`, `memory_update`, `memory_delete`, `memory_search`, `memory_list`, `memory_delta`, `task_create`, `task_get`, `task_update`, `task_list`, `task_claim`, `task_complete`, `task_fail`, `message_send`, `message_inbox`, `event_emit`, `agent_list`, `agent_rename`, `agent_delete`, `inbox_cron_setup`, `project_list`, `project_join`, `project_leave`, `project_members`.

---

## REST API

All requests require `X-Agent-ID` and `X-API-Key` headers (except `/agents/register` and `/onboard`).

```
Memory
  POST   /memory                write
  GET    /memory/search?q=      semantic search
  GET    /memory/delta?since=   changes since timestamp
  GET    /memory?type=...       list with filters
  PATCH  /memory/:id            update
  DELETE /memory/:id            soft delete

Tasks
  POST   /tasks                 create
  GET    /tasks?status=         list
  PATCH  /tasks/:id             update title/description/priority
  POST   /tasks/:id/claim       claim
  POST   /tasks/:id/complete    complete (assignee only)
  POST   /tasks/:id/fail        fail (assignee only)

Messages
  POST   /messages              send (to: agent_id or "broadcast")
  GET    /messages/inbox        unread inbox
  POST   /messages/inbox/read-all  mark all unread as read
  POST   /messages/:id/read     mark one message as read

Agents
  POST   /agents/register       register (registration key required)
  PATCH  /agents/me             rename self
  DELETE /agents/:id            delete (registration key required)
  GET    /agents                list all (registration key required)
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
| `AGENT_KEYS` | — | `agent:key` or `agent:key:proj1;proj2` — optional third segment scopes agent to projects |
| `REGISTRATION_KEY` | — | Required to register new agents |
| `DB_PATH` | `artel.db` | SQLite path |
| `PUBLIC_URL` | — | Base URL returned in `mcp_config` |
| `MCP_URL` | — | MCP URL in `mcp_config` (defaults to `PUBLIC_URL` on port 8001) |
| `UI_PASSWORD` | — | Web UI password |
| `ARCHIVIST_KEY` | — | Must match a key in `AGENT_KEYS` |
| `ARCHIVIST_PROVIDER` | `anthropic` | LLM provider: `anthropic` or `openai` |
| `ARCHIVIST_MODEL` | — | Defaults to `claude-sonnet-4-6` / `gpt-4o` |
| `ARCHIVIST_API_KEY` | — | Falls back to `ANTHROPIC_API_KEY` for Anthropic |
| `ARCHIVIST_BASE_URL` | — | OpenAI-compatible base URL (Ollama, Mistral, etc.) |
| `ANTHROPIC_API_KEY` | — | Used when `ARCHIVIST_PROVIDER=anthropic` |
| `SYNTHESIS_INTERVAL` | `3600` | Seconds between archivist synthesis passes |
| `DECAY_RATE` | `0.9` | Confidence multiplier per decay cycle |
| `DECAY_WINDOW_DAYS` | `7` | Days before decay applies to unmodified entries |
| `MCP_PORT` | `8001` | MCP server port |

---

## Archivist

Runs as a separate process alongside the server. Optional — the server works without it.

**With LLM configured (`ARCHIVIST_PROVIDER` + key):**
- On memory write: detects semantic conflicts and merges them into a canonical record
- Periodically: synthesizes a cross-agent doc from recent memory activity

**Without LLM (passive mode):**
- Confidence decay on stale entries
- Type promotion: scratch → memory → doc based on age and version count

Supports any OpenAI-compatible provider or Anthropic.

---

## Development

```bash
uv sync --dev
uv run pytest tests/ -v
```

---

## License

MIT — see [LICENSE.md](LICENSE.md).
