from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from ..config import settings

router = APIRouter(tags=["onboard"])

_SCRIPT = r"""#!/bin/sh
set -e

ARTEL_URL="{artel_url}"
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

AGENT_ID="${{AGENT_ID:-$DEFAULT_ID}}"

ARTEL_URL="$ARTEL_URL" BASE_ID="$AGENT_ID" PROJECT="$PROJECT" REG_KEY="$REG_KEY" python3 << 'PYEOF'
import os, json, urllib.request, urllib.error, sys, pathlib

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

aid, akey = _load_creds()

valid = _valid(aid, akey)
if valid is None:
    print('error: cannot reach {{}} — is the server running?'.format(url)); sys.exit(1)
elif valid:
    print('  agent    : ' + aid + '  (credentials valid)')
else:
    data = _register(base_id)
    aid, akey = data['agent_id'], data['api_key']
    creds_dir.mkdir(parents=True, exist_ok=True)
    _creds_path(aid).write_text('MCP_AGENT_ID={{}}\nMCP_AGENT_KEY={{}}\n'.format(aid, akey))
    print('  agent    : ' + aid)
    if project:
        print('  project  : ' + project)
    print('  creds    : ~/.config/artel/' + aid)

print()
print('tip: Ask Claude to install the Artel plugin — or follow the steps below.')
print('     The plugin bundles the MCP server and session hooks (loads handoff + reads')
print('     inbox on every prompt).')
print()
print('  REPL (prompts for credentials, stores key in keychain):')
print('       /plugin marketplace add NicolasPrimeau/artel')
print('       /plugin install artel@artel')
print('     Use the values from ~/.config/artel/' + aid + ' when prompted.')
print()
print('  Shell / scripted (no credential prompt — must configure after):')
print('       claude plugin marketplace add NicolasPrimeau/artel')
print('       claude plugin install artel@artel')
print('     Then open a Claude Code session and run /plugin config artel to set')
print('     artel_url, agent_id, and agent_key. Or ask Claude to do it for you.')
PYEOF
"""


@router.get("/onboard", response_class=PlainTextResponse)
async def onboard(
    request: Request,
    project: str | None = Query(default=None),
):
    artel_url = settings.public_url or str(request.base_url).rstrip("/")
    return _SCRIPT.format(artel_url=artel_url, project=project or "")
