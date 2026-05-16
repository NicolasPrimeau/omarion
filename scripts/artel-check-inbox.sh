#!/usr/bin/env bash
# Artel UserPromptSubmit hook: surface unread inbox messages as additional
# context. Gated on artel_url being configured; never blocks a prompt (always
# exits 0). Read-only — GET /messages/inbox does not mark messages read.

url="${CLAUDE_PLUGIN_OPTION_ARTEL_URL:-}"
aid="${CLAUDE_PLUGIN_OPTION_AGENT_ID:-}"
key="${CLAUDE_PLUGIN_OPTION_API_KEY:-}"

[ -z "$url" ] && exit 0
[ -z "$aid" ] && exit 0
[ -z "$key" ] && exit 0

resp="$(curl -sS --max-time 5 \
  -H "x-agent-id: $aid" -H "x-api-key: $key" \
  "${url%/}/messages/inbox" 2>/dev/null)" || exit 0
[ -z "$resp" ] && exit 0

printf '%s' "$resp" | python3 -c '
import json, sys
try:
    msgs = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if not isinstance(msgs, list) or not msgs:
    sys.exit(0)
lines = []
for m in msgs[:10]:
    lines.append(str(m.get("from_agent", "?")) + ": " + str(m.get("body", "")))
ctx = "[Artel] " + str(len(msgs)) + " unread message(s): " + " | ".join(lines)
print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ctx}}))
' 2>/dev/null || exit 0

exit 0
