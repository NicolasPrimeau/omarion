from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from ..config import settings

router = APIRouter(tags=["onboard"])

_SCRIPT = r"""#!/bin/sh
set -e

ARTEL_URL="{artel_url}"
REG_KEY="{reg_key}"

AGENT_ID=""
if [ -f ".env" ]; then
    for key in PROJECT_NAME APP_NAME SERVICE_NAME NAME; do
        val=$(grep "^${{key}}=" .env 2>/dev/null | head -1 | sed 's/^[^=]*=//' | tr -d "\"' ")
        if [ -n "$val" ]; then AGENT_ID="$val"; break; fi
    done
fi
[ -z "$AGENT_ID" ] && AGENT_ID=$(basename "$(pwd)")
AGENT_ID=$(echo "$AGENT_ID" | tr -cs 'a-zA-Z0-9_-' '-' | sed 's/^-*//;s/-*$//')
[ -z "$AGENT_ID" ] && AGENT_ID="agent-$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n')"

printf "registering '%s' with %s ...\n" "$AGENT_ID" "$ARTEL_URL"

curl -sf -X POST "$ARTEL_URL/agents/register" \
    -H "content-type: application/json" \
    -H "x-registration-key: $REG_KEY" \
    -d "{{\"agent_id\": \"$AGENT_ID\"}}" \
| python3 -c "
import sys, json, os
data = json.load(sys.stdin)
if 'detail' in data:
    print('error:', data['detail']); sys.exit(1)
with open('.mcp.json', 'w') as f:
    json.dump(data['mcp_config'], f, indent=2); f.write('\n')
lines = open('.env').read().splitlines() if os.path.exists('.env') else []
lines = [l for l in lines if not l.startswith('ARTEL_AGENT_ID=')]
lines.append('ARTEL_AGENT_ID=' + data['agent_id'])
open('.env', 'w').write('\n'.join(lines) + '\n')
print('  agent id : ' + data['agent_id'])
print('  .mcp.json written, .env updated')
print()
print('run /reload-plugins in Claude Code to connect')
"
"""


@router.get("/onboard", response_class=PlainTextResponse)
async def onboard(request: Request):
    artel_url = settings.public_url or str(request.base_url).rstrip("/")
    return _SCRIPT.format(artel_url=artel_url, reg_key=settings.registration_key)
