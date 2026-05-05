from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from ..config import settings

router = APIRouter(tags=["onboard"])

_SCRIPT = r"""#!/bin/sh
set -e

ARTEL_URL="{artel_url}"
PROJECT="{project}"

_git_name() {{
    remote=$(git remote get-url origin 2>/dev/null) || true
    if [ -n "$remote" ]; then
        basename "$remote" .git
    fi
}}
_repo=$(_git_name)
if [ -n "$_repo" ]; then
    DEFAULT_ID="$(hostname -s)-${{_repo}}"
else
    DEFAULT_ID="$(hostname -s)"
fi

_CREDS="$HOME/.config/artel/credentials"
if [ ! -f "$_CREDS" ] || ! grep -q '^MCP_AGENT_KEY=' "$_CREDS"; then
    printf "Agent name [%s]: " "$DEFAULT_ID"
    read AGENT_ID < /dev/tty
    AGENT_ID="${{AGENT_ID:-$DEFAULT_ID}}"
else
    AGENT_ID="$DEFAULT_ID"
fi

ARTEL_URL="$ARTEL_URL" BASE_ID="$AGENT_ID" PROJECT="$PROJECT" python3 -c "
import os, json, urllib.request, urllib.error, sys, pathlib

url     = os.environ['ARTEL_URL']
base_id = os.environ['BASE_ID']
project = os.environ.get('PROJECT') or None

creds = pathlib.Path.home() / '.config' / 'artel' / 'credentials'

def _load_creds():
    if not creds.exists():
        return None, None
    text = creds.read_text()
    aid = akey = None
    for line in text.splitlines():
        if line.startswith('MCP_AGENT_ID='): aid  = line.split('=', 1)[1].strip()
        if line.startswith('MCP_AGENT_KEY='): akey = line.split('=', 1)[1].strip()
    return aid, akey

def _valid(aid, akey):
    if not aid or not akey:
        return False
    req = urllib.request.Request(
        url + '/agents/me',
        headers={{'x-agent-id': aid, 'x-api-key': akey}},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status == 200
    except Exception:
        return False

def _register(agent_id):
    req = urllib.request.Request(
        url + '/agents/self-register',
        data=json.dumps({{'agent_id': agent_id, 'project': project}}).encode(),
        headers={{'content-type': 'application/json'}},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.URLError as e:
        print('error: could not reach {{}} — {{}}'.format(url, e.reason)); sys.exit(1)

def _mcp_base(api_url):
    import urllib.parse
    parsed = urllib.parse.urlparse(api_url)
    port = parsed.port or 8000
    try:
        import socket
        socket.setdefaulttimeout(1)
        socket.gethostbyname('artel.local')
        return 'http://artel.local:{{}}'.format(port + 1)
    except Exception:
        pass
    return '{{}}://{{}}:{{}}'.format(parsed.scheme, parsed.hostname, port + 1)

def _write_mcp(aid, akey):
    mcp_config = {{
        'mcpServers': {{
            'artel': {{
                'type': 'sse',
                'url': _mcp_base(url) + '/mcp',
                'headers': {{'x-agent-id': aid, 'x-api-key': akey}},
            }}
        }}
    }}
    with open('.mcp.json', 'w') as f:
        json.dump(mcp_config, f, indent=2); f.write('\n')

aid, akey = _load_creds()
refreshed = False

if _valid(aid, akey):
    _write_mcp(aid, akey)
    print('  agent    : ' + aid + '  (credentials valid, refreshed .mcp.json)')
    refreshed = True
else:
    if aid:
        print('  stale credentials for {{}} — cleaning up and re-registering'.format(aid))
        try:
            req = urllib.request.Request(
                url + '/agents/me',
                headers={{'x-agent-id': aid, 'x-api-key': akey}},
                method='DELETE',
            )
            urllib.request.urlopen(req)
        except Exception:
            pass
    data = _register(base_id)
    aid, akey = data['agent_id'], data['api_key']
    creds.parent.mkdir(parents=True, exist_ok=True)
    creds.write_text('MCP_AGENT_ID={{}}\nMCP_AGENT_KEY={{}}\n'.format(aid, akey))
    _write_mcp(aid, akey)
    print('  agent    : ' + aid)
    if project:
        print('  project  : ' + project)
    print('  creds    : ~/.config/artel/credentials')

bashrc = pathlib.Path.home() / '.bashrc'
marker = '~/.config/artel/credentials'
if bashrc.exists() and marker not in bashrc.read_text():
    with open(bashrc, 'a') as f:
        f.write('\n[ -f ~/.config/artel/credentials ] && {{ set -a; source ~/.config/artel/credentials; set +a; }}\n')

if not refreshed:
    print('  .mcp.json written, ~/.bashrc updated')
    print()
    print('source ~/.bashrc, then run /reload-plugins in Claude Code to connect')
else:
    print()
    print('run /reload-plugins in Claude Code to reconnect')
"
"""


@router.get("/onboard", response_class=PlainTextResponse)
async def onboard(request: Request, project: str | None = Query(default=None)):
    artel_url = settings.public_url or str(request.base_url).rstrip("/")
    return _SCRIPT.format(artel_url=artel_url, project=project or "")
