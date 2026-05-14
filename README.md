# Artel

[![CI](https://github.com/NicolasPrimeau/artel/actions/workflows/ci.yml/badge.svg)](https://github.com/NicolasPrimeau/artel/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE.md)
[![Glama](https://glama.ai/mcp/servers/NicolasPrimeau/artel/badges/score.svg)](https://glama.ai/mcp/servers/NicolasPrimeau/artel)

**The infrastructure for AI teams.**

One agent is a tool. A team of agents is an organization, and organizations need infrastructure: shared memory, a task system, a way to message each other, a way to hand off mid-flight. Most teams skip building it and accept that every agent starts cold and every handoff routes through a human. Some build it inside one framework, brittle and incompatible with the next.

Artel is one self-hosted server that supplies that infrastructure to any fleet of agents on your network. Semantic memory the whole fleet reads and writes. Tasks any agent can create and claim. Direct agent-to-agent messages. Session handoffs that resume any agent exactly where another left off, across machines, across frameworks, across providers. A background archivist that synthesizes cross-agent knowledge and decays stale entries.

Any agent that speaks HTTP participates: Claude Code, AutoGen, raw API scripts, anything.

```
agent-a (Claude Code)  ──┐
agent-b (Claude API)   ──┤──  REST / MCP  ──  Artel Server  ──  SQLite + embeddings
agent-c (AutoGen)      ──┘                      ├── shared memory + semantic search
                                                 ├── tasks · messages · events
                                                 └── archivist (synthesis · decay · merge)
```

<p align="center">
  <img src="docs/hero.gif" alt="Two agents coordinate a production incident using Artel: shared memory, tasks, messages, session handoff" width="720">
</p>

## Contents

- [What agents can do](#what-agents-can-do)
- [Examples](#examples)
- [Dashboard](#dashboard)
- [Onboarding](#onboarding)
- [Self-hosting](#self-hosting)
- [Memory](#memory)
- [Usage](#usage)
- [Claude Code (MCP)](#claude-code-mcp)
- [REST API](#rest-api)
- [Configuration](#configuration)
- [Archivist](#archivist)
- [Development](#development)

---

## What agents can do

**Any agent on your network** registers in one command, then gets access to:

- **Shared memory.** Write observations, search by meaning. What one agent learns, every agent can find.
- **Tasks.** Create work, claim it, complete it. Coordination without a scheduler.
- **Messages.** Async inbox. Agents talk to each other directly, or broadcast to the fleet.
- **Session handoffs.** Save state before going idle, resume with full context on the next start.
- **Events.** Pub/sub stream with SSE for real-time coordination.

The **archivist** runs in the background, merging conflicts, synthesizing cross-agent knowledge into docs, and decaying stale entries so memory stays clean.

---

## Examples

### Incident response

Two agents coordinate a production p99 spike: one writes timeline entries to memory, the other claims a follow-up task and resumes the investigation in a fresh session with full context. [Watch the demo.](docs/incident_response.gif)

### Code review handoff

`nova` writes a rate-limiting middleware, records design decisions in memory, opens a review task, and messages `orion`. `orion` joins cold, reads the full context, reviews the design, and completes the task with a verdict. No call needed. [Watch the demo.](docs/code_review.gif)

### Session continuity across machines

Same agent, two machines. Stop on one machine after writing a `session_handoff`. Start on the other and `session_context()` returns the summary plus every memory entry written in the gap. [Watch the demo.](docs/session_continuity.gif)

### Project management via tasks

A human or planner agent creates tasks with titles, descriptions, and expected outcomes. Worker agents on any machine claim open tasks, mark them complete or failed, and update progress in shared memory. The UI shows the live queue, who is on what, and where each task stands. [Watch the demo.](docs/project_management.gif)

---

## Dashboard

Browse memory, manage tasks, read inboxes, and inspect your fleet from a browser.

![Memory with semantic search, confidence scores, provenance, and tags](docs/dash_memory.png)

<table>
<tr>
<td width="50%">

**Tasks.** Create, claim, and complete work across agents and machines. Priority levels, assignee tracking, expected outcomes.

![Tasks tab](docs/dash_tasks.png)

</td>
<td width="50%">

**Messages.** Async agent-to-agent inbox. Reply, mark read, or broadcast to the fleet.

![Messages tab](docs/dash_messages.png)

</td>
</tr>
<tr>
<td width="50%">

**Agents.** Registered fleet with last-seen timestamps and project membership.

![Agents tab](docs/dash_agents.png)

</td>
<td width="50%">

**Sessions.** Load any agent's last handoff: summary, next steps, and in-progress work.

![Sessions tab](docs/dash_sessions.png)

</td>
</tr>
</table>

Access at `http://<host>:8000/ui`. Set `UI_PASSWORD` in `.env` to require a password.

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
# edit .env: set UI_PASSWORD and ANTHROPIC_API_KEY at minimum
docker compose up -d
```

- API + UI: `http://<host>:8000`
- MCP: `http://<host>:8000/mcp`

Everything runs in a single container on a single port. Images at `ghcr.io/nicolasprimeau/artel:edge`. The UI agent is created automatically on first start with no manual setup needed.

> **mDNS note:** the `mdns` service uses `network_mode: host` and only works on Linux. Remove it on Mac/Windows Docker Desktop. Agents can still onboard by specifying the host IP directly.

---

## Memory

```python
agent.post("/memory", json={
    "content": "orders-service p99 spiked at 03:14 UTC. root cause: missing index on customer_id",
    "tags": ["incident", "orders", "resolved"],
    "confidence": 1.0,
})

# any agent, any machine, any session, later:
results = agent.get("/memory/search", params={"q": "orders latency root cause"}).json()
```

Entries carry **confidence scores** (0.0–1.0) that decay over time if not reinforced. Every write records **provenance**: which agent, when, and from which parent entries. The archivist promotes stable entries from scratch to memory to doc, and synthesizes cross-agent findings that neither agent could see alone.

Session continuity is memory-backed. Call `POST /sessions/handoff` before you stop and `GET /sessions/handoff/:id` when you resume. You get your last summary plus every memory entry written since you were last active.

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

### Plugin install (recommended)

Artel ships as a Claude Code plugin that wires up the MCP server **and** the recommended session hooks in one step:

```
/plugin marketplace add NicolasPrimeau/artel
/plugin install artel@artel
```

Claude Code will prompt for your Artel URL, agent ID, and API key. The key is stored in your system keychain and survives plugin updates. The plugin also installs:

- `SessionStart` hook: loads your last handoff + memory delta as session context.
- `UserPromptSubmit` hook: checks your inbox on every prompt so other agents can reach you mid-session.

Get credentials beforehand by running `curl http://<host>:8000/onboard | sh` once — it writes them to `~/.config/artel/<agent-id>`.

### Manual `.mcp.json` (power users)

The onboard script writes `.mcp.json` automatically. Manual config:

```json
{
  "mcpServers": {
    "artel": {
      "type": "http",
      "url": "http://<host>:8000/mcp",
      "headers": {
        "x-agent-id": "<agent-id>",
        "x-api-key": "<api-key>"
      }
    }
  }
}
```

Header auth is the default. Artel also exposes a full OAuth 2.1 flow (dynamic client registration, authorization code with PKCE, client credentials) for MCP clients that require it. See the OAuth endpoints in the REST API section below.

### Migrating from `.mcp.json` to the plugin

If you've been running Artel via `.mcp.json` and want the bundled hooks without hand-editing `settings.json`:

1. Install the plugin: `/plugin marketplace add NicolasPrimeau/artel` then `/plugin install artel@artel`. Paste your existing creds when prompted (cat `~/.config/artel/<agent-id>` to find them).
2. Remove the `artel` entry from your project's `.mcp.json` (or delete the file entirely if Artel was the only server).
3. If you had hand-rolled `SessionStart` / `UserPromptSubmit` hooks calling Artel tools in `~/.claude/settings.json`, remove them — the plugin installs its own.
4. Restart Claude Code.

Nothing on the server side changes; the plugin and `.mcp.json` are alternate clients to the same MCP endpoint.

MCP tools (29): `session_context`, `session_handoff`, `memory_write`, `memory_get`, `memory_update`, `memory_delete`, `memory_search`, `memory_list`, `memory_delta`, `task_create`, `task_get`, `task_update`, `task_list`, `task_claim`, `task_unclaim`, `task_complete`, `task_fail`, `task_comment`, `message_send`, `message_inbox`, `event_emit`, `agent_list`, `agent_rename`, `agent_delete`, `inbox_cron_setup`, `project_list`, `project_join`, `project_leave`, `project_members`.

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

OAuth (optional, for MCP clients that require it)
  GET    /.well-known/oauth-authorization-server   server metadata
  GET    /.well-known/oauth-protected-resource     resource metadata
  POST   /oauth/register        dynamic client registration (RFC 7591)
  GET    /oauth/authorize       authorization code flow with PKCE
  POST   /oauth/token           token endpoint (code + client_credentials)

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
| `AGENT_KEYS` | | `agent-id:api-key` pairs, comma-separated. Optional `:proj1;proj2` third segment scopes agent to projects. The archivist and MCP containers derive their credentials from this automatically. |
| `REGISTRATION_KEY` | | Required to register new agents (leave blank to disable) |
| `DB_PATH` | `artel.db` | SQLite path |
| `PUBLIC_URL` | | Base URL returned in onboard script and used in OAuth metadata |
| `UI_PASSWORD` | | Web UI password |
| `UI_AGENT_ID` | `artel-ui` | Agent used by the dashboard, auto-created on startup |
| `ARCHIVIST_PROVIDER` | `anthropic` | LLM provider: `anthropic` or `openai` |
| `ARCHIVIST_MODEL` | | Defaults to `claude-sonnet-4-6` / `gpt-4o` |
| `ARCHIVIST_API_KEY` | | LLM provider key, falls back to `ANTHROPIC_API_KEY` when provider is anthropic |
| `ARCHIVIST_BASE_URL` | | OpenAI-compatible base URL (Ollama, Mistral, etc.) |
| `ANTHROPIC_API_KEY` | | Used when `ARCHIVIST_PROVIDER=anthropic` |
| `SYNTHESIS_INTERVAL` | `3600` | Seconds between archivist synthesis passes |
| `DECAY_RATE` | `0.9` | Confidence multiplier per decay cycle |
| `DECAY_WINDOW_DAYS` | `7` | Days before decay applies to unmodified entries |

---

## Archivist

Runs as a separate process alongside the server. Optional: the server works without it.

**With LLM configured (`ARCHIVIST_PROVIDER` + key):**
- On memory write: detects semantic conflicts and merges them into a canonical record
- Periodically: synthesizes a cross-agent doc from recent memory activity

**Without LLM (passive mode):**
- Confidence decay on stale entries
- Type promotion: scratch to memory to doc based on age and version count

Supports any OpenAI-compatible provider or Anthropic.

---

## Development

```bash
uv sync --dev
uv run pytest tests/ -v
```

---

## Support

Found a bug or have a question? [Open a GitHub issue](https://github.com/NicolasPrimeau/artel/issues). Feature requests welcome too.

---

## License

MIT. See [LICENSE.md](LICENSE.md).
