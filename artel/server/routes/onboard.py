from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from ..config import settings

router = APIRouter(tags=["onboard"])

_SCRIPT = r"""#!/bin/sh
set -e

ARTEL_URL="{artel_url}"
PROJECT="{project}"

AGENT_ID=$(hostname -s)

ARTEL_URL="$ARTEL_URL" BASE_ID="$AGENT_ID" PROJECT="$PROJECT" python3 -c "
import os, json, urllib.request, urllib.error, sys, pathlib, re

url     = os.environ['ARTEL_URL']
base_id = os.environ['BASE_ID']
project = os.environ.get('PROJECT') or None

req = urllib.request.Request(
    url + '/agents/self-register',
    data=json.dumps({{'agent_id': base_id, 'project': project}}).encode(),
    headers={{'content-type': 'application/json'}},
    method='POST',
)
try:
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
except urllib.error.URLError as e:
    print('error: could not reach {{}} — {{}}'.format(url, e.reason)); sys.exit(1)

creds = pathlib.Path.home() / '.config' / 'artel' / 'credentials'
creds.parent.mkdir(parents=True, exist_ok=True)
creds.write_text('MCP_AGENT_ID={{}}\nMCP_AGENT_KEY={{}}\n'.format(data['agent_id'], data['api_key']))

mcp_config = {{
    'mcpServers': {{
        'artel': {{
            'type': 'http',
            'url': url.replace(':8000', ':8001') + '/mcp',
            'headers': {{'x-agent-id': '\${{MCP_AGENT_ID}}', 'x-api-key': '\${{MCP_AGENT_KEY}}'}},
        }}
    }}
}}
with open('.mcp.json', 'w') as f:
    json.dump(mcp_config, f, indent=2); f.write('\n')

bashrc = pathlib.Path.home() / '.bashrc'
marker = '~/.config/artel/credentials'
if bashrc.exists() and marker not in bashrc.read_text():
    with open(bashrc, 'a') as f:
        f.write('\n[ -f ~/.config/artel/credentials ] && {{ set -a; source ~/.config/artel/credentials; set +a; }}\n')

print('  agent    : ' + data['agent_id'])
if project:
    print('  project  : ' + project)
print('  creds    : ~/.config/artel/credentials')
print('  .mcp.json written, ~/.bashrc updated')
print()
print('source ~/.bashrc, then run /reload-plugins in Claude Code to connect')
"
"""


@router.get("/onboard", response_class=PlainTextResponse)
async def onboard(request: Request, project: str | None = Query(default=None)):
    artel_url = settings.public_url or str(request.base_url).rstrip("/")
    return _SCRIPT.format(artel_url=artel_url, project=project or "")
