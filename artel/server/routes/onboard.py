from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from ..config import settings

router = APIRouter(tags=["onboard"])

_SCRIPT = r"""#!/bin/sh
set -e

ARTEL_URL="{artel_url}"
MCP_URL="{mcp_url}"
PROJECT="{project}"
REG_KEY="${{ARTEL_REG_KEY:-}}"

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
    _EXISTING_ID=$(python3 -c "import json,sys; h=json.load(open('.mcp.json')).get('mcpServers',{{}}).get('artel',{{}}).get('headers',{{}}); print(h.get('x-agent-id',''))" 2>/dev/null || true)
fi

AGENT_ID="${{AGENT_ID:-${{_EXISTING_ID:-$DEFAULT_ID}}}}"

ARTEL_URL="$ARTEL_URL" MCP_URL="$MCP_URL" BASE_ID="$AGENT_ID" PROJECT="$PROJECT" REG_KEY="$REG_KEY" python3 << 'PYEOF'
import os, json, urllib.request, urllib.error, sys, pathlib

url     = os.environ['ARTEL_URL']
mcp_url = os.environ['MCP_URL']
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
        if e.code == 401:
            print('error: registration requires a key.')
            print('action: add this line to ~/.bashrc and re-run:')
            print('  export ARTEL_REG_KEY=<your-registration-key>')
        else:
            print('error: registration failed ({{}}) — {{}}'.format(e.code, detail))
        sys.exit(1)
    except urllib.error.URLError as e:
        print('error: could not reach {{}} — {{}}'.format(url, e.reason)); sys.exit(1)

def _write_mcp(aid, akey):
    mcp_config = {{
        'mcpServers': {{
            'artel': {{
                'type': 'http',
                'url': mcp_url + '/mcp',
                'headers': {{'x-agent-id': aid, 'x-api-key': akey}},
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
    _write_mcp(aid, akey)
    print('  agent    : ' + aid + '  (credentials valid, refreshed .mcp.json)')
    refreshed = True
else:
    data = _register(base_id)
    aid, akey = data['agent_id'], data['api_key']
    creds_dir.mkdir(parents=True, exist_ok=True)
    _creds_path(aid).write_text('MCP_AGENT_ID={{}}\nMCP_AGENT_KEY={{}}\n'.format(aid, akey))
    _write_mcp(aid, akey)
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
            '    if [ -f ".mcp.json" ]; then\n'
            '        aid=$(python3 -c "import json; print(json.load(open(\'.mcp.json\'))[\'mcpServers\'][\'artel\'][\'headers\'].get(\'x-agent-id\', \'\'))" 2>/dev/null || true)\n'
            '        if [ -n "$aid" ]; then\n'
            '            creds="$HOME/.config/artel/$aid"\n'
            '            [ -f "$creds" ] && {{ set -a; source "$creds"; set +a; }}\n'
            '        fi\n'
            '    fi\n'
            '}}\n'
            'if [ -n "$PROMPT_COMMAND" ]; then\n'
            '    export PROMPT_COMMAND="_artel_load;$PROMPT_COMMAND"\n'
            'else\n'
            '    export PROMPT_COMMAND="_artel_load"\n'
            'fi\n'
        )

if not refreshed:
    print('  .mcp.json written')
    print()
    print('start a new Claude Code session to connect')
else:
    print()
    print('start a new Claude Code session to reconnect')

print()
print('tip: Claude Code users can install the Artel plugin instead — it bundles the')
print('     MCP server with session hooks (loads handoff + reads inbox on every prompt).')
print()
print('  In the Claude Code REPL (human-driven, prompts for credentials):')
print('       /plugin marketplace add NicolasPrimeau/artel')
print('       /plugin install artel@artel')
print('     The install prompts you for artel_url, agent_id, agent_key. The agent_key')
print('     is stored in your system keychain. Use the values from ~/.config/artel/' + aid + '.')
print()
print('  From a shell (e.g. when scripting or running as an agent):')
print('       claude plugin marketplace add NicolasPrimeau/artel')
print('       claude plugin install artel@artel')
print('     The CLI install does NOT prompt for userConfig. After install, run')
print('     /plugin config artel in a Claude Code session to set credentials, OR')
print('     edit ~/.claude/settings.json directly:')
print('       pluginConfigs.artel.options = {{artel_url: "' + url + '", agent_id: "' + aid + '"}}')
print('     (the sensitive agent_key still has to come from the slash command or keychain).')
PYEOF
"""


@router.get("/onboard", response_class=PlainTextResponse)
async def onboard(
    request: Request,
    project: str | None = Query(default=None),
):
    artel_url = settings.public_url or str(request.base_url).rstrip("/")
    return _SCRIPT.format(artel_url=artel_url, project=project or "", mcp_url=artel_url)
