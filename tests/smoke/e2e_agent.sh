#!/usr/bin/env bash
# End-to-end test: two real Claude Code agents coordinate via Artel MCP.
#
# Agent-A writes a memory entry and creates a task.
# Agent-B reads the memory and claims the task.
# Both are verified via REST.
#
# Usage: bash tests/smoke/e2e_agent.sh
# Requires: claude CLI authenticated, uv, artel installed.
set -euo pipefail

PASS=0; FAIL=0
ok()   { echo "  ✓ $*"; PASS=$((PASS+1)); }
fail() { echo "  ✗ $*"; FAIL=$((FAIL+1)); }
die()  { echo "FATAL: $*"; cleanup; exit 1; }

# ── Config ────────────────────────────────────────────────────────────────────

API_PORT=19010
MCP_A_PORT=19011
MCP_B_PORT=19012
TMP=$(mktemp -d)
DB="$TMP/artel.db"
API_URL="http://localhost:$API_PORT"
REG_KEY="e2ekey"
SRV_PID=""
MCP_A_PID=""
MCP_B_PID=""

DIR_A="$TMP/agent-a"
DIR_B="$TMP/agent-b"
mkdir -p "$DIR_A" "$DIR_B"

cleanup() {
    [ -n "$SRV_PID"   ] && kill "$SRV_PID"   2>/dev/null || true
    [ -n "$MCP_A_PID" ] && kill "$MCP_A_PID" 2>/dev/null || true
    [ -n "$MCP_B_PID" ] && kill "$MCP_B_PID" 2>/dev/null || true
    rm -rf "$TMP"
}
trap cleanup EXIT

wait_http() {
    local url="$1" label="$2" tries="${3:-20}"
    for i in $(seq 1 "$tries"); do
        CODE=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || true)
        if [ -n "$CODE" ] && [ "$CODE" != "000" ]; then
            ok "$label up (${i}s)"; return 0
        fi
        sleep 1
    done
    fail "$label did not start"; return 1
}

# Run claude non-interactively with an MCP config; returns the final result text
claude_run() {
    local dir="$1" mcp_cfg="$2" prompt="$3"
    (cd "$dir" && claude -p "$prompt" \
        --mcp-config "$mcp_cfg" \
        --output-format stream-json \
        --verbose \
        --dangerously-skip-permissions \
        2>/dev/null) | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        ev = json.loads(line)
        if ev.get('type') == 'result':
            print(ev.get('result', ''))
    except Exception:
        pass
" 2>/dev/null || true
}

# Extract tool_result text for a given tool name from stream-json output
tool_result() {
    local stream="$1" tool="$2"
    echo "$stream" | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        ev = json.loads(line)
    except Exception:
        continue
    # tool_result events carry the output
    if ev.get('type') == 'tool_result':
        print(json.dumps(ev)); break
    # assistant messages may contain tool_use blocks followed by results
    msg = ev.get('message', {})
    for block in msg.get('content', []):
        if isinstance(block, dict) and block.get('type') == 'tool_result':
            print(json.dumps(block)); break
" 2>/dev/null || true
}

# Extract the final text response from stream-json
final_text() {
    local stream="$1"
    echo "$stream" | python3 -c "
import sys, json
last = ''
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        ev = json.loads(line)
    except Exception:
        continue
    if ev.get('type') == 'result':
        print(ev.get('result', '')); break
    # fallback: accumulate text deltas
    for block in ev.get('message', {}).get('content', []):
        if isinstance(block, dict) and block.get('type') == 'text':
            last = block.get('text', '')
if not last: print(last)
" 2>/dev/null || true
}

# ── Start server ──────────────────────────────────────────────────────────────

echo
echo "── 1. Infrastructure ────────────────────────────────────────────────────"

DB_PATH=$DB PORT=$API_PORT REGISTRATION_KEY=$REG_KEY \
    uv run python -m artel.server >"$TMP/server.log" 2>&1 &
SRV_PID=$!

wait_http "$API_URL/agents" "Artel server" 30 || die "server failed"

# Register agents
for agent in smoke-agent-a smoke-agent-b; do
    curl -sf -X POST "$API_URL/agents/self-register" \
        -H "x-registration-key: $REG_KEY" \
        -H "content-type: application/json" \
        -d "{\"agent_id\":\"$agent\"}" -o /dev/null
done

KEY_A=$(curl -sf "$API_URL/agents" -H "x-registration-key: $REG_KEY" | \
    python3 -c "import sys,json; agents=json.load(sys.stdin); print(next(a['api_key'] for a in agents if a['agent_id']=='smoke-agent-a'))")
KEY_B=$(curl -sf "$API_URL/agents" -H "x-registration-key: $REG_KEY" | \
    python3 -c "import sys,json; agents=json.load(sys.stdin); print(next(a['api_key'] for a in agents if a['agent_id']=='smoke-agent-b'))")

ok "agents registered (smoke-agent-a, smoke-agent-b)"

# Start MCP servers
MCP_AGENT_ID=smoke-agent-a MCP_AGENT_KEY=$KEY_A \
MCP_TRANSPORT=streamable-http MCP_PORT=$MCP_A_PORT ARTEL_URL=$API_URL \
    uv run artel-mcp >"$TMP/mcp-a.log" 2>&1 &
MCP_A_PID=$!

MCP_AGENT_ID=smoke-agent-b MCP_AGENT_KEY=$KEY_B \
MCP_TRANSPORT=streamable-http MCP_PORT=$MCP_B_PORT ARTEL_URL=$API_URL \
    uv run artel-mcp >"$TMP/mcp-b.log" 2>&1 &
MCP_B_PID=$!

# Write MCP configs
python3 -c "
import json
for agent, key, port, d in [
    ('smoke-agent-a', '$KEY_A', $MCP_A_PORT, '$DIR_A'),
    ('smoke-agent-b', '$KEY_B', $MCP_B_PORT, '$DIR_B'),
]:
    cfg = {'mcpServers': {'artel': {
        'type': 'http',
        'url': f'http://localhost:{port}/mcp',
        'headers': {'x-agent-id': agent, 'x-api-key': key},
    }}}
    open(d + '/.mcp.json', 'w').write(json.dumps(cfg, indent=2))
"
ok ".mcp.json written for each agent"

# Wait for both MCP servers
for port in $MCP_A_PORT $MCP_B_PORT; do
    for i in $(seq 1 15); do
        CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "http://localhost:$port/mcp" \
            -H "content-type: application/json" -d '{}' 2>/dev/null || true)
        [ -n "$CODE" ] && [ "$CODE" != "000" ] && break
        sleep 1
        [ "$i" -eq 15 ] && die "MCP :$port did not start"
    done
done
ok "MCP servers up (:$MCP_A_PORT, :$MCP_B_PORT)"

# ── Agent A: write memory + create task ───────────────────────────────────────

echo
echo "── 2. Agent-A writes memory and creates a task ──────────────────────────"

STREAM_A=$(claude_run "$DIR_A" "$DIR_A/.mcp.json" \
    "You are smoke-agent-a testing Artel coordination. Do both of these using Artel MCP tools:
1. Call memory_write with content='e2e test: agent-a was here' and tags=['e2e','smoke'].
2. Call task_create with title='E2E smoke task' and description='Created by agent-a for agent-b to claim'.
After both calls, output exactly this format and nothing else:
MEMORY_ID=<the memory id>
TASK_ID=<the task id>")

MEM_ID_A=$(echo "$STREAM_A" | grep "MEMORY_ID=" | head -1 | cut -d= -f2 | tr -d '[:space:]' || true)
TASK_ID_A=$(echo "$STREAM_A" | grep "TASK_ID=" | head -1 | cut -d= -f2 | tr -d '[:space:]' || true)

[ -n "$MEM_ID_A" ] && ok "agent-a wrote memory: $MEM_ID_A" \
                    || fail "agent-a memory_write failed (output: $(echo "$STREAM_A" | tail -3))"

[ -n "$TASK_ID_A" ] && ok "agent-a created task: $TASK_ID_A" \
                     || fail "agent-a task_create failed"

# Verify memory exists in REST
if [ -n "$MEM_ID_A" ]; then
    STATUS=$(curl -sf -o /dev/null -w "%{http_code}" "$API_URL/memory/$MEM_ID_A" \
        -H "x-agent-id: smoke-agent-a" -H "x-api-key: $KEY_A" 2>/dev/null || true)
    [ "$STATUS" = "200" ] && ok "memory confirmed in DB via REST" \
                           || fail "memory not found in DB: $STATUS"
fi

# ── Agent B: find and claim the task ──────────────────────────────────────────

echo
echo "── 3. Agent-B finds memory and claims the task ──────────────────────────"

STREAM_B=$(claude_run "$DIR_B" "$DIR_B/.mcp.json" \
    "You are smoke-agent-b testing Artel coordination. Do both of these using Artel MCP tools:
1. Call memory_search with query='e2e test agent-a' to find what agent-a wrote.
2. Call task_list with status='open' to see available tasks, then call task_claim on the task titled 'E2E smoke task'.
After both operations, output exactly this format and nothing else:
FOUND_MEMORY=<yes or no>
CLAIMED_TASK=<the task id you claimed, or 'none'>")

FOUND_MEM=$(echo "$STREAM_B" | grep "FOUND_MEMORY=" | head -1 | cut -d= -f2 | tr -d '[:space:]' || true)
CLAIMED_TASK=$(echo "$STREAM_B" | grep "CLAIMED_TASK=" | head -1 | cut -d= -f2 | tr -d '[:space:]' || true)

[ "$FOUND_MEM" = "yes" ] && ok "agent-b found agent-a's memory via search" \
                          || fail "agent-b did not find memory (FOUND_MEMORY=$FOUND_MEM)"

[ -n "$CLAIMED_TASK" ] && [ "$CLAIMED_TASK" != "none" ] \
    && ok "agent-b claimed task: $CLAIMED_TASK" \
    || fail "agent-b did not claim task"

# Verify task status via REST
if [ -n "$TASK_ID_A" ]; then
    TASK_STATUS=$(curl -sf "$API_URL/tasks/$TASK_ID_A" \
        -H "x-agent-id: smoke-agent-a" -H "x-api-key: $KEY_A" 2>/dev/null | \
        python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || true)
    [ "$TASK_STATUS" = "claimed" ] && ok "task status=claimed confirmed in DB" \
                                    || fail "task status is '$TASK_STATUS' (expected 'claimed')"

    ASSIGNEE=$(curl -sf "$API_URL/tasks/$TASK_ID_A" \
        -H "x-agent-id: smoke-agent-a" -H "x-api-key: $KEY_A" 2>/dev/null | \
        python3 -c "import sys,json; print(json.load(sys.stdin).get('assigned_to',''))" 2>/dev/null || true)
    [ "$ASSIGNEE" = "smoke-agent-b" ] && ok "task assigned to smoke-agent-b" \
                                       || fail "task assignee is '$ASSIGNEE'"
fi

# ── Summary ───────────────────────────────────────────────────────────────────

echo
echo "─────────────────────────────────────────────────────────────────────────"
printf "  passed: %d   failed: %d\n" "$PASS" "$FAIL"
echo "─────────────────────────────────────────────────────────────────────────"
echo

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
