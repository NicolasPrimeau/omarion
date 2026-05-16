#!/usr/bin/env bash
# Artel SessionStart hook: inject the agent's last handoff + memory delta as
# additional context. Gated on artel_url being configured; never blocks a
# session (always exits 0) so a missing/down Artel server is harmless.

url="${CLAUDE_PLUGIN_OPTION_ARTEL_URL:-}"
aid="${CLAUDE_PLUGIN_OPTION_AGENT_ID:-}"
key="${CLAUDE_PLUGIN_OPTION_API_KEY:-}"

[ -z "$url" ] && exit 0
[ -z "$aid" ] && exit 0
[ -z "$key" ] && exit 0

resp="$(curl -sS --max-time 10 \
  -H "x-agent-id: $aid" -H "x-api-key: $key" \
  "${url%/}/sessions/handoff/$aid" 2>/dev/null)" || exit 0
[ -z "$resp" ] && exit 0

printf '%s' "$resp" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if not isinstance(d, dict):
    sys.exit(0)
h = d.get("last_handoff") or {}
delta = d.get("memory_delta") or []
parts = []
if h:
    parts.append("Last session (" + str(h.get("created_at", ""))[:16] + "): " + str(h.get("summary", "")))
    ns = h.get("next_steps") or []
    if ns:
        parts.append("Next: " + "; ".join(str(x) for x in ns))
    ip = h.get("in_progress") or []
    if ip:
        parts.append("In progress: " + ", ".join(str(x) for x in ip))
if delta:
    parts.append(str(len(delta)) + " memory entries changed since last session")
if not parts:
    sys.exit(0)
print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "[Artel] " + "  ".join(parts)}}))
' 2>/dev/null || exit 0

exit 0
