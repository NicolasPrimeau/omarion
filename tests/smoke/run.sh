#!/usr/bin/env bash
# Smoke test: server startup → registration → re-onboard → MCP tool call
# Usage: bash tests/smoke/run.sh
set -euo pipefail

PASS=0; FAIL=0
ok()   { echo "  ✓ $*"; PASS=$((PASS+1)); }
fail() { echo "  ✗ $*"; FAIL=$((FAIL+1)); }
die()  { echo "FATAL: $*"; cleanup; exit 1; }

# Parse an SSE response body, return first JSON-RPC result/error object
sse_json() {
    python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if line.startswith('data:'):
        line = line[5:].strip()
    if line.startswith('{'):
        try:
            d = json.loads(line)
            if 'result' in d or 'error' in d:
                print(json.dumps(d)); break
        except Exception:
            pass
" 2>/dev/null || true
}

# Make an MCP JSON-RPC call via streamable-http; writes raw headers+body to a tmp file
# Usage: mcp_call <id> <method> <params_json>  →  stdout = raw response
mcp_call() {
    local id="$1" method="$2" params="$3"
    local extra_headers=()
    [ -n "${MCP_SID:-}" ] && extra_headers+=(-H "mcp-session-id: $MCP_SID")
    curl -si --max-time 10 -X POST "$MCP_URL/mcp" \
        -H "content-type: application/json" \
        -H "accept: application/json, text/event-stream" \
        "${extra_headers[@]}" \
        -d "{\"jsonrpc\":\"2.0\",\"id\":$id,\"method\":\"$method\",\"params\":$params}" \
        2>/dev/null || true
}

# ── Config ────────────────────────────────────────────────────────────────────

API_PORT=19000
MCP_PORT=19001
TMP=$(mktemp -d)
DB="$TMP/artel.db"
API_URL="http://localhost:$API_PORT"
MCP_URL="http://localhost:$MCP_PORT"
MCP_SID=""
REG_KEY="smokekey"
SRV_PID=""
MCP_PID=""

cleanup() {
    [ -n "$SRV_PID" ] && kill "$SRV_PID" 2>/dev/null || true
    [ -n "$MCP_PID" ] && kill "$MCP_PID" 2>/dev/null || true
    rm -rf "$TMP"
}
trap cleanup EXIT

# ── Start server ──────────────────────────────────────────────────────────────

echo
echo "── 1. Server startup ────────────────────────────────────────────────────"

DB_PATH=$DB PORT=$API_PORT REGISTRATION_KEY=$REG_KEY \
    uv run python -m artel.server >"$TMP/server.log" 2>&1 &
SRV_PID=$!

for i in $(seq 1 30); do
    if curl -sf "$API_URL/agents" -H "x-registration-key: $REG_KEY" -o /dev/null 2>&1; then
        ok "server started (${i}s)"
        break
    fi
    sleep 1
    [ "$i" -eq 30 ] && die "server did not start in 30s"
done

# ── Setup: onboard endpoint + registration ────────────────────────────────────

echo
echo "── 2. Setup (onboard endpoint + registration) ───────────────────────────"

ONBOARD=$(curl -sf "$API_URL/onboard" 2>/dev/null || true)

echo "$ONBOARD" | grep -q "ARTEL_URL=" \
    && ok "GET /onboard returns a valid shell script" \
    || fail "GET /onboard returned unexpected content"

echo "$ONBOARD" | grep -q "self-register" \
    && ok "onboard script contains registration logic" \
    || fail "onboard script missing registration logic"

echo "$ONBOARD" | grep -q "mcp.json" \
    && ok "onboard script writes .mcp.json" \
    || fail "onboard script missing .mcp.json step"

ONBOARD_URL=$(echo "$ONBOARD" | grep '^ARTEL_URL=' | head -1 | cut -d'"' -f2 || true)
[ -n "$ONBOARD_URL" ] \
    && ok "onboard script has server URL: $ONBOARD_URL" \
    || fail "onboard script missing ARTEL_URL"

# Register agent-a
REG_A=$(curl -sf -X POST "$API_URL/agents/self-register" \
    -H "x-registration-key: $REG_KEY" \
    -H "content-type: application/json" \
    -d '{"agent_id":"smoke-a"}' 2>/dev/null || true)
AGENT_A=$(echo "$REG_A" | python3 -c "import sys,json; print(json.load(sys.stdin).get('agent_id',''))" 2>/dev/null || true)
KEY_A=$(echo   "$REG_A" | python3 -c "import sys,json; print(json.load(sys.stdin).get('api_key',''))"  2>/dev/null || true)

[ -n "$AGENT_A" ] && ok "agent-a registered: $AGENT_A" || fail "agent-a registration failed: $REG_A"

STATUS=$(curl -sf -o /dev/null -w "%{http_code}" "$API_URL/agents/me" \
    -H "x-agent-id: $AGENT_A" -H "x-api-key: $KEY_A" 2>/dev/null || true)
[ "$STATUS" = "200" ] && ok "/agents/me → 200 (auth works)" || fail "/agents/me → $STATUS"

# Register agent-b
REG_B=$(curl -sf -X POST "$API_URL/agents/self-register" \
    -H "x-registration-key: $REG_KEY" \
    -H "content-type: application/json" \
    -d '{"agent_id":"smoke-b"}' 2>/dev/null || true)
AGENT_B=$(echo "$REG_B" | python3 -c "import sys,json; print(json.load(sys.stdin).get('agent_id',''))" 2>/dev/null || true)
KEY_B=$(echo   "$REG_B" | python3 -c "import sys,json; print(json.load(sys.stdin).get('api_key',''))"  2>/dev/null || true)

[ -n "$AGENT_B" ] && ok "agent-b registered: $AGENT_B" || fail "agent-b registration failed"

# ── Update: re-registration deduplication ────────────────────────────────────

echo
echo "── 3. Update (re-registration deduplication) ────────────────────────────"

REG_A2=$(curl -sf -X POST "$API_URL/agents/self-register" \
    -H "x-registration-key: $REG_KEY" \
    -H "content-type: application/json" \
    -d '{"agent_id":"smoke-a"}' 2>/dev/null || true)
AGENT_A2=$(echo "$REG_A2" | python3 -c "import sys,json; print(json.load(sys.stdin).get('agent_id',''))" 2>/dev/null || true)

if [ -n "$AGENT_A2" ] && [ "$AGENT_A2" != "$AGENT_A" ]; then
    ok "duplicate agent_id resolved to unique ID: $AGENT_A2"
else
    fail "duplicate self-register failed or returned same ID: $REG_A2"
fi

STILL_UP=$(curl -sf -o /dev/null -w "%{http_code}" "$API_URL/agents/me" \
    -H "x-agent-id: $AGENT_A" -H "x-api-key: $KEY_A" 2>/dev/null || true)
[ "$STILL_UP" = "200" ] && ok "original agent-a credentials still valid after re-registration" \
                          || fail "original agent-a credentials broken: $STILL_UP"

# ── MCP: start server, initialize, tool call ─────────────────────────────────

echo
echo "── 4. MCP server ────────────────────────────────────────────────────────"

MCP_AGENT_ID=$AGENT_A MCP_AGENT_KEY=$KEY_A \
MCP_TRANSPORT=streamable-http MCP_PORT=$MCP_PORT \
ARTEL_URL=$API_URL \
    uv run artel-mcp >"$TMP/mcp.log" 2>&1 &
MCP_PID=$!

for i in $(seq 1 20); do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$MCP_URL/mcp" \
        -H "content-type: application/json" -d '{}' 2>/dev/null || true)
    if [ -n "$HTTP_CODE" ] && [ "$HTTP_CODE" != "000" ]; then
        ok "MCP server up (${i}s, HTTP $HTTP_CODE)"
        break
    fi
    sleep 1
    if [ "$i" -eq 20 ]; then
        fail "MCP server did not start in 20s"
        cat "$TMP/mcp.log"
    fi
done

# MCP initialize — session ID comes back in response header
INIT_RAW=$(mcp_call 1 initialize \
    '{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}')

MCP_SID=$(echo "$INIT_RAW" | grep -i "^mcp-session-id:" | head -1 | awk '{print $2}' | tr -d '\r' || true)
INIT_JSON=$(echo "$INIT_RAW" | sse_json)

if echo "$INIT_JSON" | python3 -c "import sys,json; assert 'result' in json.load(sys.stdin)" 2>/dev/null; then
    ok "MCP initialize succeeded (session: ${MCP_SID:-no-id})"
else
    fail "MCP initialize failed"
    echo "    raw: $(echo "$INIT_RAW" | tail -3)"
fi

# tools/list
TOOLS_RAW=$(mcp_call 2 "tools/list" '{}')
TOOL_COUNT=$(echo "$TOOLS_RAW" | sse_json | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(len(d.get('result', {}).get('tools', [])))
" 2>/dev/null || echo "0")

[ "$TOOL_COUNT" -gt 0 ] && ok "tools/list returned $TOOL_COUNT tools" \
                          || fail "tools/list returned no tools"

# memory_write tool call via MCP
WRITE_RAW=$(mcp_call 3 "tools/call" \
    '{"name":"memory_write","arguments":{"content":"smoke test entry written via MCP","tags":["smoke"]}}')
MEM_ID=$(echo "$WRITE_RAW" | sse_json | python3 -c "
import sys, json, re
d = json.load(sys.stdin)
for b in d.get('result', {}).get('content', []):
    t = b.get('text', '')
    m = re.search(r'\[([0-9a-f-]{36})\]', t)
    if m: print(m.group(1)); break
" 2>/dev/null || true)

[ -n "$MEM_ID" ] && ok "memory_write via MCP succeeded (id: $MEM_ID)" \
                  || fail "memory_write via MCP failed"

# Verify via REST (same agent)
if [ -n "$MEM_ID" ]; then
    MEM_REST=$(curl -sf -o /dev/null -w "%{http_code}" "$API_URL/memory/$MEM_ID" \
        -H "x-agent-id: $AGENT_A" -H "x-api-key: $KEY_A" 2>/dev/null || true)
    [ "$MEM_REST" = "200" ] && ok "MCP-written memory readable via REST" \
                             || fail "MCP memory not found via REST: $MEM_REST"
fi

# Cross-agent read (agent-b reads unscoped memory)
if [ -n "$MEM_ID" ] && [ -n "$AGENT_B" ]; then
    CROSS=$(curl -sf -o /dev/null -w "%{http_code}" "$API_URL/memory/$MEM_ID" \
        -H "x-agent-id: $AGENT_B" -H "x-api-key: $KEY_B" 2>/dev/null || true)
    [ "$CROSS" = "200" ] && ok "cross-agent memory visibility confirmed" \
                          || fail "cross-agent read returned $CROSS"
fi

# ── Summary ───────────────────────────────────────────────────────────────────

echo
echo "─────────────────────────────────────────────────────────────────────────"
printf "  passed: %d   failed: %d\n" "$PASS" "$FAIL"
echo "─────────────────────────────────────────────────────────────────────────"
echo

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
