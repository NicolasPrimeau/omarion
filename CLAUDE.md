# Artel

Harness-agnostic coordination layer for AI agents — shared memory, session continuity, agent-to-agent communication, and async archival synthesis across machines and LLM providers.

## What It Is

Artel is a self-hosted server that acts as the shared brain for a fleet of AI agents. Any agent that can make HTTP calls can participate — Claude Code, AutoGen, raw API scripts, anything. Agents read and write memory, pass messages, claim tasks, and emit events. An async archivist agent watches all activity and synthesizes connections no individual agent can see.

## Stack

- Python 3.13, FastAPI, SQLite (WAL mode), sqlite-vec (embeddings)
- MCP adapter on top of core REST API
- Runs on server (ARTEL_HOST), accessible from all machines

## Layout

```
artel/
  server/       — FastAPI app, routes, auth
  store/        — SQLite models, migrations
  archivist/    — async synthesis agent
  mcp/          — MCP adapter over REST
scripts/
  migration/    — DB migrations
docs/
  plan.md       — execution plan
  spec.md       — protocol and data model spec
  architecture.md — system design
.claude/
  memory/       — agent memory
  skills/       — project skills
```

## Core Primitives

- **Memory** — shared knowledge store with embeddings, confidence scores, provenance
- **Tasks** — create/claim/complete units of work across agents
- **Messages** — direct agent-to-agent async inbox
- **Events** — pub/sub stream for real-time coordination

## Agent Identity

API key + `agent_id` string. No framework coupling. Any HTTP client participates.

## Conventions

- Conventional commits (feat:, fix:, refactor:, docs:)
- No secrets in files — env vars only
- No comments or docstrings
- Pydantic models, no hardcoded strings

## Running

```bash
uv run python -m artel.server
```
