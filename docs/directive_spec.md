# Directive Entry Type — Design Spec

## Overview

A `directive` is a third `entry_type` for the Artel memory store, alongside `memory` and `doc`. It represents a standing instruction that shapes agent behavior — particularly the archivist's — within a project or for a specific agent. Directives are authoritative by definition: they are written intentionally by humans or trusted agents, never synthesized from data.

The type hierarchy in intent:
- `memory` — observed facts; ephemeral, confidence decays, may be promoted
- `doc` — promoted stable knowledge; long-lived but still participates in synthesis
- `directive` — behavioral instructions; immune to automated modification, loaded as context not content

---

## 1. Lifecycle

Directives are **inert with respect to all automated archivist operations**. The archivist must never:

- Decay the confidence of a directive
- Promote a directive to another type
- Merge a directive with another entry
- Soft-delete a directive
- Include a directive in the synthesis memory block that is passed to the LLM

The confidence field on a directive is fixed at `1.0` on write and cannot be changed by any automated process. A human (or a trusted agent with explicit owner rights — see §3) may update it via `PATCH`, but the archivist client must never call `patch_memory` on an entry with `type="directive"`.

Directives are not versioned by synthesis. Their `version` field increments only on explicit human `PATCH` calls, which is useful for auditing whether a directive has been revised.

**Summary of archivist lifecycle rules:**

| Operation | Memory | Doc | Directive |
|---|---|---|---|
| Included in synthesis LLM prompt | Yes | Yes | No — loaded as preamble |
| Confidence decay | Yes | Yes | No |
| Promotion | Yes | No | No |
| Conflict merge | Yes | Yes | No |
| Can archivist write/patch | Yes | Yes | No |
| Can archivist soft-delete | Yes | Yes | No |

---

## 2. Scope

The existing `scope` field (`agent` | `project`) maps cleanly onto directive semantics.

**`scope="project"` + `project=<name>` — project-scoped directive**

Visible to all agents in the project. Applies to any agent operating within that project context. This is the primary form. Examples:
- "Never store PII in this project's memories"
- "Elevate anything tagged `mcp-plugin` to high priority"
- "Flag memories with confidence < 0.3 for human review instead of letting them decay to floor"

**`scope="agent"` — agent-scoped directive**

Visible only to the owning agent (enforced by existing scope logic). Used to customize one agent's behavior without polluting the shared context. Primarily useful for archivist-specific guidance that shouldn't be visible to other agents. Examples:
- "When synthesizing this project, weight entries from agent `nimbus` more heavily than `poseidon`"
- "Do not create tasks from synthesis — only write findings"

**Valid combinations:**

| scope | project | Semantics |
|---|---|---|
| `project` | set | All agents in that project see and apply this directive |
| `project` | null | Global directive — all projects, all agents. Use sparingly. |
| `agent` | set | One agent's private instruction, project-contextualized |
| `agent` | null | One agent's global private instruction |

**The archivist loads directives in this priority order** (higher priority = loaded later so it wins in ambiguous cases):

1. Global project-scoped directives (`scope="project"`, `project=null`)
2. Project-specific directives (`scope="project"`, `project=<name>`)
3. Agent-scoped directives for the archivist (`scope="agent"`, `agent_id=<archivist_id>`)

This means project-specific instructions override global ones, and archivist-private instructions override both.

---

## 3. Write Permissions

**Clear answer: only humans and designated trusted agents may write directives. The archivist must never write a directive.**

Rationale: directives are authoritative. If the archivist could write directives, it could instruct itself, creating a self-modification loop with no human checkpoint. Directives are the human override layer — keeping them human-authored preserves that invariant.

Implementation:

The server enforces this at the API layer. When `POST /memory` is called with `type="directive"`, the server checks whether the calling `agent_id` is in a configurable allowlist stored in settings: `DIRECTIVE_WRITERS`. This defaults to including only the `UI_AGENT_ID` (poseidon, the human-facing agent).

```
DIRECTIVE_WRITERS=poseidon,trusted-orchestrator
```

Any agent not in this list that attempts to write `type="directive"` receives `403 Forbidden` with detail `"directive writes require elevated permission"`.

**Who can update directives:**

Only the original author (`agent_id` match) may `PATCH` a directive, same as existing memory ownership rules. The archivist is blocked at both write and patch.

**Who can delete directives:**

Only the original author. The soft-delete endpoint checks `agent_id` ownership, which already blocks the archivist from deleting its own observations on directive entries (it cannot create them).

**Tradeoff:** A second trusted agent (e.g., an orchestrator) may need to write directives programmatically. Rather than hardcoding archivist exclusion, the server checks membership in `DIRECTIVE_WRITERS`. The archivist is simply never added to this list. This keeps it a configuration choice rather than a code-level special-case proliferation.

---

## 4. How the Archivist Loads and Uses Directives

### Loading Phase (before every synthesis pass)

At the start of `run_synthesis`, before fetching the delta entries and before constructing the LLM prompt, the archivist calls a dedicated helper:

```python
async def load_directives(client: ArtelClient, project: str | None) -> list[dict]:
    ...
```

This makes two calls to `GET /memory` with `type=directive`:

1. Global directives: `type=directive&scope=project` (no project filter)
2. Project directives (if project is set): `type=directive&scope=project&project=<name>`
3. Archivist-private directives: `type=directive&scope=agent` (the archivist's own scope)

Results are merged in priority order (global → project → agent-private), deduped by id, and returned as an ordered list.

### Prompt Injection

Directives are formatted into a preamble block that is prepended to the system prompt, before the archivist's role description:

```
--- STANDING DIRECTIVES ---
[1] (project: my-project) Never store PII. Redact or refuse any memory that contains names, emails, or identifiers.
[2] (project: my-project) Elevate anything tagged `mcp-plugin` in your synthesis output.
[3] (agent-private) Do not create tasks from synthesis — only write findings.
--- END DIRECTIVES ---

You are the Artel archivist. Your role is to surface what no individual agent can see...
```

This places directives as fixed context that the LLM sees before its persona, making them high-salience instructions rather than data to reason about.

### Exclusion from Synthesis Pool

In `run_synthesis`, the current filter is:

```python
entries = [e for e in entries if e["agent_id"] != settings.archivist_id]
```

This must be extended to:

```python
entries = [e for e in entries if e["agent_id"] != settings.archivist_id and e["type"] != "directive"]
```

The same exclusion applies in `check_and_merge`: a directive must never be selected as a conflict candidate or as the "other" entry in a merge pair.

In `decay_confidence` and `run_promotion`, add:

```python
entries = [e for e in entries if e["type"] != "directive"]
```

---

## 5. Directive Conflicts

Two directives can contradict each other. The archivist must detect this but must not resolve it.

**Detection:** During the loading phase, after collecting all directives, the archivist runs a lightweight conflict check. This is not an embedding similarity check — directives are short and intentional enough that semantic overlap is a signal, not noise. The check uses the existing embedding similarity logic but with a lower threshold (configurable as `directive_conflict_threshold`, default `0.85`).

When two directives exceed the similarity threshold, the archivist:

1. Does **not** abort synthesis. It proceeds with both directives loaded.
2. Prepends a conflict warning to the preamble block: `"WARNING: Directives [1] and [2] may conflict. Apply your best judgment and flag the ambiguity in your output."`
3. After synthesis completes, sends a message to the `UI_AGENT_ID` (poseidon) with subject `"Directive conflict detected"` and a body listing the two conflicting directive IDs, their content, and the project.

The archivist never merges, deletes, or modifies a directive to resolve a conflict. Resolution requires a human to `DELETE` one directive or `PATCH` its content.

---

## 6. Staleness

Directives can become orphaned — referencing tags, agents, or patterns that no longer exist. Confidence decay does not apply, so there's no natural expiry mechanism.

**Optional `expires_at` field:**

Add an `expires_at` column to the memory table (nullable TEXT, ISO 8601). All entry types can have this field, but it's expected to be used primarily for directives. If `expires_at` is set and the current time is past it, the server soft-deletes the directive automatically on the next read, or a cleanup job does it on a schedule.

Alternatively, keep `expires_at` off the schema and handle it purely through archivist flagging (simpler, avoids schema change for all entry types). **Recommendation: add `expires_at` as a nullable column on the memory table.** It's useful for time-boxed directives ("only elevate MCP entries during the v2 migration sprint") and costs nothing when not used.

**Archivist staleness detection:**

During each synthesis pass, after loading directives, the archivist checks each directive for staleness signals:
- The directive references a tag that appears in zero current memory entries for this project
- The directive references an agent_id that has not been seen in the last N days (configurable `directive_staleness_agent_days`, default 30)
- The directive's `created_at` is older than `directive_staleness_age_days` (configurable, default 90) and has never been updated

When any signal fires, the archivist sends a message to `UI_AGENT_ID`: `"Directive [id] may be stale: [reason]. Review and delete if no longer relevant."` It does not delete or modify the directive.

The archivist must not send the same staleness warning more than once per `directive_staleness_cooldown_hours` (configurable, default 168 — one week) per directive. Track this in a new `kv` table entry: `key="directive_stale_notified:<id>"`, `value=<ISO timestamp>`.

---

## 7. API Surface

**Verdict: no separate endpoint. Use `POST /memory` with `type="directive"`.**

Directives live in the memory table and share all memory infrastructure (embedding, search, delta, scope enforcement). A separate endpoint would duplicate this for marginal ergonomic gain.

What does change:

**Write — `POST /memory`**

The `MemoryWrite` model gains `"directive"` as a valid `type` literal. The route adds the permission check against `DIRECTIVE_WRITERS` when `type="directive"`. On success, emits `memory.written` event as normal — the archivist's event handler ignores directives in `check_and_merge` (type filter).

**Read — `GET /memory`**

The `type` query parameter already supports filtering. Agents that want only directives call `GET /memory?type=directive`. Directives appear in the default unfiltered list alongside memory and doc entries — agents should filter if they only want one type.

**Search — `GET /memory/search`**

Directives are included in semantic search results by default. The `type` filter applies. No change needed.

**Delta — `GET /memory/delta`**

Directives appear in delta results. Agents that use delta for context-loading (e.g., session handoff) will pick up new directives automatically. No change needed.

**New: `GET /memory/directives` convenience endpoint (optional)**

This is optional and can be deferred. It would return all directives visible to the calling agent, ordered by priority (project-scoped first, then agent-scoped), with no pagination. Useful for agents that want to load their directive context on session start without constructing a filter query. Implement if agents find the filter-based approach cumbersome in practice.

---

## 8. UI

Directives need to be visually distinct and immediately recognizable as authoritative — not something the system generated.

**Badge/pill:**

Add a `.pill.directive` class using the existing blue (`--blue`) color slot. The UI currently uses accent (yellow), green, red, orange, purple. Blue is unused and semantically appropriate for "command/instruction."

```css
--blue:  #83a598;
--bl-bg:  rgba(131,165,152,.12);
--bl-bord: rgba(131,165,152,.35);

.pill.directive { background: var(--bl-bg); color: var(--blue); border: 1px solid var(--bl-bord); }
```

**Card treatment:**

Directive cards use `.card.blue` (border-left color `--blue`). The card should display a lock icon or "directive" label prefix in the header to signal immutability. No confidence meter for directive cards — confidence is always 1.0 and displaying it is noise.

**Placement:**

Directives appear in the Memory tab, not in a separate view. They are pinned to the top of the list when no filter is active — directives sort before `doc` entries, which sort before `memory` entries. When `?type=directive` is selected in the filter dropdown, only directives appear.

Add `directive` as a type option in the memory type filter dropdown (`<select id="mtype">`).

**Write form:**

The write form does not offer `directive` as a type to all users. If the UI detects that the logged-in agent is in `DIRECTIVE_WRITERS` (the server can expose this via a new field on `GET /agents/me` or via a 403 response on attempt), the dropdown shows the directive option. Otherwise it is hidden. This prevents confusion without requiring a separate form.

---

## 9. Archivist Suggesting Directives

**In scope, but constrained: the archivist can suggest, never write.**

The archivist's synthesis output already has a `### Recommended Actions` section that creates tasks. Add a parallel `### Suggested Directives` section:

```
### Suggested Directives
- DIRECTIVE SUGGESTION: Elevate all entries tagged `feed-item` from the Claude Code RSS feed — they consistently surface breaking changes before other agents catch them.
```

The archivist writes this section into its synthesis doc as plain text. It does not call `POST /memory` with `type="directive"`. A human reads the synthesis, agrees with the suggestion, and creates the directive manually via the UI or API.

**Formatting rule:** Suggested directives must be prefixed with `DIRECTIVE SUGGESTION:` so the UI can optionally parse and highlight them. Do not auto-create tasks for these suggestions — they are advisory, not actionable items for an agent.

**Why not auto-create a task asking the human to create the directive?** Because a task saying "create this directive" would be redundant with the synthesis doc itself. The synthesis is already the human's read queue. Keeping it in-doc avoids task spam for what may be low-priority suggestions.

---

## 10. Migration and Bootstrapping

**Schema change:**

The `memory` table's `type` CHECK constraint changes from:

```sql
CHECK (type IN ('memory','doc'))
```

to:

```sql
CHECK (type IN ('memory','doc','directive'))
```

If `expires_at` is added:

```sql
ALTER TABLE memory ADD COLUMN expires_at TEXT;
```

Both changes are backward-compatible. Existing entries are unaffected. The migration script must run both ALTER statements before the server upgrade is deployed.

**No default directives shipped.**

Projects start with zero directives. The bootstrapping flow is:

1. Human creates a project.
2. Human writes the first directive via the UI or API (`POST /memory` with `type="directive"`, `scope="project"`, `project=<name>`).
3. The archivist picks it up on its next synthesis cycle.

Shipping default directives (e.g., "never store PII") would impose policy decisions on every project, which is inappropriate for a self-hosted tool. If a team wants a standing PII rule, they create it explicitly. This also means the absence of a directive is unambiguous — there are no hidden defaults to discover.

**MCP tool update:**

The `memory_write` MCP tool's description already says `entry_type="memory" is the default and right for almost everything`. Update the docstring/description to mention that `directive` is a valid type but requires elevated permission. Prevents agents from attempting to write directives and getting confused by 403s.

---

## Schema Summary

```sql
-- memory table type constraint update
CHECK (type IN ('memory', 'doc', 'directive'))

-- optional expires_at column
ALTER TABLE memory ADD COLUMN expires_at TEXT;

-- optional: kv entries for staleness notification tracking
-- key: "directive_stale_notified:<directive_id>"
-- value: ISO timestamp of last notification
-- uses existing kv table, no schema change needed
```

## Settings Summary

New environment variables for `ArchivistSettings` and `Settings`:

```
DIRECTIVE_WRITERS=poseidon          # comma-separated agent_ids allowed to write directives
DIRECTIVE_CONFLICT_THRESHOLD=0.85  # embedding similarity above which two directives are flagged
DIRECTIVE_STALENESS_AGENT_DAYS=30  # days of inactivity before an agent is considered gone
DIRECTIVE_STALENESS_AGE_DAYS=90    # directive age before staleness check fires
DIRECTIVE_STALENESS_COOLDOWN_HOURS=168  # min hours between staleness warnings per directive
```
