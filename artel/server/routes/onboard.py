from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from ..config import settings

router = APIRouter(tags=["onboard"])

_SCRIPT = r"""#!/bin/sh
set -e

ARTEL_URL="{artel_url}"
REG_KEY="{reg_key}"
PROJECT="{project}"

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

ARTEL_URL="$ARTEL_URL" REG_KEY="$REG_KEY" BASE_ID="$AGENT_ID" PROJECT="$PROJECT" python3 -c "
import os, json, urllib.request, urllib.error, sys
url, reg_key, base_id = os.environ['ARTEL_URL'], os.environ['REG_KEY'], os.environ['BASE_ID']
project = os.environ.get('PROJECT') or None

for attempt in range(1, 100):
    agent_id = base_id if attempt == 1 else '{{}}-{{}}'.format(base_id, attempt)
    payload = {{'agent_id': agent_id}}
    if project:
        payload['project'] = project
    req = urllib.request.Request(
        url + '/agents/register',
        data=json.dumps(payload).encode(),
        headers={{'content-type': 'application/json', 'x-registration-key': reg_key}},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req) as r:
            data = json.loads(r.read())
        break
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try: detail = json.loads(body).get('detail', body)
        except Exception: detail = body
        if e.code == 409:
            continue
        print('error {{}}: {{}}'.format(e.code, detail)); sys.exit(1)
    except urllib.error.URLError as e:
        print('error: could not reach {{}} — {{}}'.format(url, e.reason)); sys.exit(1)
else:
    print('error: could not find unique name for', base_id); sys.exit(1)

with open('.mcp.json', 'w') as f:
    json.dump(data['mcp_config'], f, indent=2); f.write('\n')
lines = open('.env').read().splitlines() if os.path.exists('.env') else []
lines = [l for l in lines if not l.startswith('ARTEL_AGENT_ID=') and not l.startswith('MCP_PROJECT=')]
lines.append('ARTEL_AGENT_ID=' + data['agent_id'])
if project:
    lines.append('MCP_PROJECT=' + project)
open('.env', 'w').write('\n'.join(lines) + '\n')
print('  agent id : ' + data['agent_id'])
if project:
    print('  project  : ' + project)
print('  .mcp.json written, .env updated')
print()
print('run /reload-plugins in Claude Code to connect')
"
"""


@router.get("/onboard", response_class=PlainTextResponse)
async def onboard(request: Request, project: str | None = Query(default=None)):
    artel_url = settings.public_url or str(request.base_url).rstrip("/")
    return _SCRIPT.format(
        artel_url=artel_url,
        reg_key=settings.registration_key,
        project=project or "",
    )
