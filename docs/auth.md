# Authentication & Authorization

Reference for Artel's auth middleware (`artel/server/auth.py`). Every REST route
depends on one of the dependency aliases at the bottom of that module; the MCP
adapter authenticates once per session and reuses the resolved identity.

## Identity model

An identity is an `agent_id` string paired with an `api_key`. There is no
framework coupling — any HTTP client that can present a valid pair participates.

Two sources of valid pairs:

1. **Static keys** — configured via the `ARTEL_AGENT_KEYS` env var
   (`Settings.agent_keys`). Format:

   ```
   agent_id:api_key[:proj1;proj2],agent_id2:api_key2:*
   ```

   - `Settings.api_keys()` builds `{api_key: agent_id}`.
   - `Settings.agent_projects()` builds `{agent_id: [projects]}`. A third field
     that is empty or `*` means **no project restriction** (full visibility).
   - Static identities are not rows in the `agents` table.

2. **Dynamic agents** — rows in the `agents` table (`id`, `api_key`, `role`),
   created through `POST /agents/register`. Registration is gated by
   `require_registration_key`: the request must send `X-Registration-Key`
   matching `ARTEL_REGISTRATION_KEY`. If `ARTEL_REGISTRATION_KEY` is unset,
   registration is refused outright (no open enrollment).

`_verify_agent(agent_id, api_key)` checks static keys first, then falls back to
a `agents` table lookup. Either match authenticates.

## Credential transports

`require_agent` accepts credentials three ways, checked in order:

1. **Bearer JWT** — `Authorization: Bearer <token>`. Tokens are HS256, issuer
   `artel`, with claims `sub` (agent_id) and `key` (api_key). The signing secret
   is persisted in the `kv` table under `jwt_secret`; it is auto-generated
   (`secrets.token_hex(32)`) on first use and stable thereafter, so tokens
   survive restarts but not a DB wipe. After decode the embedded
   `(sub, key)` pair is still run through `_verify_agent` — a validly signed
   token for a deleted/unknown agent is rejected. Mint with
   `jwt_utils.sign_token(agent_id, api_key, ttl)`; default TTL is
   `Settings.jwt_ttl` = 2592000s (30 days).
2. **Header pair** — `X-Agent-Id` + `X-Api-Key`.
3. **Query pair** — only on feed routes via `require_agent_feed`:
   `?agent_id=&api_key=`. This exists so RSS/Atom readers that cannot set
   custom headers can still authenticate. Treat these URLs as bearer secrets.

Any successful authentication calls `presence.update_seen(agent_id, ...)` and
returns the resolved `agent_id`. Any failure raises `401 invalid credentials`.
A bad/expired JWT never falls through to header auth — it 401s.

## Roles (RBAC)

```
ROLE_RANK = viewer(0) < agent(1) < archivist(2) < owner(3)
```

- `role_of(agent_id)` reads `agents.role`. If there is no row, or the value is
  unrecognized, it returns `"agent"`. **Static-key identities therefore always
  resolve to `agent`** — they cannot be `archivist` or `owner` unless a matching
  `agents` row exists with that role. (Known caveat: the archivist runs under a
  static key by default, so elevating it to the `archivist` role requires a DB
  row; without one it is treated as a plain agent for role checks.)
- `is_owner(agent_id)` → role == owner.
- `can_curate_memory(agent_id)` → role in {owner, archivist}. This is the gate
  for editing/curating memory entries the caller does not own.
- `require_role(minimum)` returns a FastAPI dependency that authenticates via
  `require_agent` then enforces `ROLE_RANK[role] >= ROLE_RANK[minimum]`,
  raising `403 insufficient role` otherwise.

### Dependency aliases

| Alias | Wraps | Use |
|-------|-------|-----|
| `AgentDep` | `require_agent` | authenticated, role not checked |
| `ReaderDep` | `require_role("viewer")` | any authenticated caller (read) |
| `ActorDep` | `require_role("agent")` | normal write operations |
| `OwnerDep` | `require_role("owner")` | privileged/destructive operations |

## Project scoping

Authorization for *which rows* a caller sees is separate from role.

- `_memberships(agent_id)` returns:
  - `None` → **unrestricted** (sees everything). True for `ui_agent_id`, and
    for static agents whose project field is `*`/empty.
  - otherwise the union of static-config projects and `project_members` rows.
- `project_filter(agent_id)` turns that into a SQL `WHERE` fragment:
  - unrestricted → no filter
  - no memberships → `(project IS NULL)` (only global rows)
  - else → `(project IS NULL OR project IN (...))`

Routes that return collections apply `project_filter`; single-entry routes
re-check membership against the row's `project` and return `403 not a member of
this project` on mismatch.

## Caveats

- Query-param credentials on feed routes are full credentials in the URL —
  scope feed links accordingly; they are not read-only tokens.
- The JWT secret lives in the DB, not config. Resetting the DB invalidates all
  outstanding tokens.
- Role is DB-only. An owner whose access is via a static key still resolves to
  `agent` for `require_role`; grant owner/archivist by inserting/marking an
  `agents` row.
</content>
</invoke>
