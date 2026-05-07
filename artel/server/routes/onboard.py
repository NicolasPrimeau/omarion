from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from ..config import settings

router = APIRouter(tags=["onboard"])

_SCRIPT = r"""#!/bin/sh
set -e

ARTEL_URL="{artel_url}"
PROJECT="{project}"
REG_KEY="{reg_key}"

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

_MCP=".mcp.json"

if [ -f "$_MCP" ] && command -v python3 >/dev/null 2>&1; then
    _EXISTING_ID=$(python3 -c "import json,base64,sys;h=json.load(open('$_MCP'))['mcpServers']['artel']['headers'];x=h.get('x-agent-id','');[print(x) or sys.exit() for _ in [1] if x];a=h.get('Authorization','');[print(json.loads(base64.b64decode(a[7:].split('.')[1]+'==')).get('sub','')) for _ in [1] if a.startswith('Bearer ') and len(a[7:].split('.'))>=2]" 2>/dev/null || true)
fi

if [ -n "$_EXISTING_ID" ]; then
    AGENT_ID="$_EXISTING_ID"
elif [ -t 0 ]; then
    printf "Agent name [%s]: " "$DEFAULT_ID"
    read AGENT_ID
    AGENT_ID="${{AGENT_ID:-$DEFAULT_ID}}"
else
    AGENT_ID="$DEFAULT_ID"
fi

ARTEL_URL="$ARTEL_URL" BASE_ID="$AGENT_ID" PROJECT="$PROJECT" REG_KEY="$REG_KEY" python3 << 'PYEOF'
import os, json, urllib.request, urllib.error, sys, pathlib, urllib.parse

url     = os.environ['ARTEL_URL']
base_id = os.environ['BASE_ID']
project = os.environ.get('PROJECT') or None
reg_key = os.environ.get('REG_KEY') or None

creds_dir = pathlib.Path.home() / '.config' / 'artel'

def _creds_path(aid):
    return creds_dir / aid

def _load_creds():
    candidate = _creds_path(base_id)
    if candidate.exists():
        return _parse_creds(candidate)
    legacy = creds_dir / 'credentials'
    if legacy.exists():
        return _parse_creds(legacy)
    return None, None

def _parse_creds(path):
    text = path.read_text()
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
    except urllib.error.HTTPError:
        return False
    except Exception:
        return None

def _register(agent_id):
    headers = {{'content-type': 'application/json'}}
    if reg_key:
        headers['x-registration-key'] = reg_key
    req = urllib.request.Request(
        url + '/agents/self-register',
        data=json.dumps({{'agent_id': agent_id, 'project': project}}).encode(),
        headers=headers,
        method='POST',
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            detail = json.loads(body).get('detail', body)
        except Exception:
            detail = body
        print('error: registration failed ({{}}) — {{}}'.format(e.code, detail)); sys.exit(1)
    except urllib.error.URLError as e:
        print('error: could not reach {{}} — {{}}'.format(url, e.reason)); sys.exit(1)

def _get_token(aid, akey):
    data = urllib.parse.urlencode({{
        'grant_type': 'client_credentials',
        'client_id': aid,
        'client_secret': akey,
    }}).encode()
    req = urllib.request.Request(
        url + '/oauth/token',
        data=data,
        headers={{'content-type': 'application/x-www-form-urlencoded'}},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())['access_token']
    except Exception as e:
        print('warning: could not get token: {{}}'.format(e))
        return None

def _mcp_base(api_url):
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

def _write_mcp(aid, akey, token):
    headers = {{'x-agent-id': aid, 'x-api-key': akey}}
    if token:
        headers = {{'Authorization': 'Bearer ' + token}}
    mcp_config = {{
        'mcpServers': {{
            'artel': {{
                'type': 'http',
                'url': _mcp_base(url) + '/mcp',
                'headers': headers,
            }}
        }}
    }}
    with open('.mcp.json', 'w') as f:
        json.dump(mcp_config, f, indent=2); f.write('\n')

aid, akey = _load_creds()
refreshed = False

valid = _valid(aid, akey)
if valid is None:
    print('error: cannot reach {{}} — is the server running?'.format(url)); sys.exit(1)
elif valid:
    token = _get_token(aid, akey)
    _write_mcp(aid, akey, token)
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
        base_id = aid
    data = _register(base_id)
    aid, akey = data['agent_id'], data['api_key']
    token = _get_token(aid, akey)
    creds_dir.mkdir(parents=True, exist_ok=True)
    _creds_path(aid).write_text('MCP_AGENT_ID={{}}\nMCP_AGENT_KEY={{}}\n'.format(aid, akey))
    _write_mcp(aid, akey, token)
    print('  agent    : ' + aid)
    if project:
        print('  project  : ' + project)
    print('  creds    : ~/.config/artel/' + aid)

bashrc = pathlib.Path.home() / '.bashrc'
marker = '_artel_load()'
if bashrc.exists() and marker not in bashrc.read_text():
    with open(bashrc, 'a') as f:
        f.write(
            '\n_artel_load() {{\n'
            '    local mcp=".mcp.json" aid creds\n'
            '    if [ -f "$mcp" ]; then\n'
            '        aid=$(python3 -c "import json,base64,sys;h=json.load(open(\'.mcp.json\'))[\'mcpServers\'][\'artel\'][\'headers\'];x=h.get(\'x-agent-id\',\'\');[print(x) or sys.exit() for _ in [1] if x];a=h.get(\'Authorization\',\'\');[print(json.loads(base64.b64decode(a[7:].split(\'.\')[1]+\'==\')).get(\'sub\',\'\')) for _ in [1] if a.startswith(\'Bearer \') and len(a[7:].split(\'.\'))>=2]" 2>/dev/null)\n'
            '    fi\n'
            '    if [ -n "$aid" ]; then creds="$HOME/.config/artel/$aid"\n'
            '    else creds="$HOME/.config/artel/credentials"; fi\n'
            '    [ -f "$creds" ] && {{ set -a; source "$creds"; set +a; }}\n'
            '}}\n'
            'if [ -n "$PROMPT_COMMAND" ]; then\n'
            '    export PROMPT_COMMAND="_artel_load;$PROMPT_COMMAND"\n'
            'else\n'
            '    export PROMPT_COMMAND="_artel_load"\n'
            'fi\n'
        )

if not refreshed:
    print('  .mcp.json written, ~/.bashrc updated')

    try:
        sys.stdout.write('  join project [blank to skip]: ')
        sys.stdout.flush()
        proj_input = input().strip()
    except Exception:
        proj_input = ''
    if proj_input:
        join_req = urllib.request.Request(
            url + '/projects/' + proj_input + '/join',
            data=b'',
            headers={{'x-agent-id': aid, 'x-api-key': akey}},
            method='POST',
        )
        try:
            urllib.request.urlopen(join_req)
            print('  joined project: ' + proj_input)
        except Exception as e:
            print('  could not join project: ' + str(e))

    print()
    print('source ~/.bashrc, then run /reload-plugins in Claude Code to connect')
else:
    print()
    print('run /reload-plugins in Claude Code to reconnect')
PYEOF
"""


@router.get("/onboard", response_class=PlainTextResponse)
async def onboard(
    request: Request,
    project: str | None = Query(default=None),
    key: str | None = Query(default=None),
):
    artel_url = settings.public_url or str(request.base_url).rstrip("/")
    return _SCRIPT.format(artel_url=artel_url, project=project or "", reg_key=key or "")
