#!/usr/bin/env python3
"""
Register a project as an Artel agent and write its .mcp.json for Claude Code.

Usage (from the target project):
    python /path/to/Artel/scripts/join.py

Or point at another directory:
    python /path/to/Artel/scripts/join.py --dir /path/to/project

Config is resolved in this order: CLI flags → target .env → Artel .env → env vars.

Agent ID defaults to PROJECT_NAME, APP_NAME, SERVICE_NAME, or the directory name.
ARTEL_URL and REGISTRATION_KEY are read from Artel's own .env if not set elsewhere.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ARTEL_ROOT = Path(__file__).resolve().parent.parent


def _read_dotenv(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        result[key.strip()] = val.strip().strip("\"'")
    return result


def _agent_id(env: dict[str, str], fallback: str) -> str:
    raw = (
        env.get("PROJECT_NAME")
        or env.get("APP_NAME")
        or env.get("SERVICE_NAME")
        or env.get("NAME")
        or fallback
    )
    return re.sub(r"[^a-zA-Z0-9_-]", "-", raw).strip("-") or fallback


def _post(url: str, key: str, agent_id: str) -> dict:
    payload = json.dumps({"agent_id": agent_id}).encode()
    req = urllib.request.Request(
        f"{url}/agents/register",
        data=payload,
        headers={"content-type": "application/json", "x-registration-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            detail = json.loads(body).get("detail", body)
        except Exception:
            detail = body
        print(f"error {e.code}: {detail}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"error: could not reach {url} — {e.reason}")
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description="Register a project as an Artel agent")
    ap.add_argument("--dir", default=".", help="target project directory (default: cwd)")
    ap.add_argument("--url", help="Artel server URL (env: ARTEL_URL)")
    ap.add_argument("--key", help="registration key (env: ARTEL_REG_KEY or Artel .env REGISTRATION_KEY)")
    ap.add_argument("--agent", help="agent ID (default: from .env or directory name)")
    args = ap.parse_args()

    target = Path(args.dir).resolve()
    if not target.is_dir():
        print(f"error: {target} is not a directory")
        sys.exit(1)

    proj_env = _read_dotenv(target / ".env")
    artel_env = _read_dotenv(ARTEL_ROOT / ".env")

    url = (
        args.url
        or os.environ.get("ARTEL_URL")
        or proj_env.get("ARTEL_URL")
        or artel_env.get("ARTEL_URL")
        or "http://localhost:8000"
    )
    reg_key = (
        args.key
        or os.environ.get("ARTEL_REG_KEY")
        or proj_env.get("ARTEL_REG_KEY")
        or artel_env.get("REGISTRATION_KEY")
    )
    if not reg_key:
        print("error: no registration key found")
        print("  add REGISTRATION_KEY to Artel's .env, or pass --key")
        sys.exit(1)

    agent_id = args.agent or _agent_id(proj_env, target.name)

    print(f"registering '{agent_id}' with {url} ...")

    data = _post(url, reg_key, agent_id)

    # Replace uvx with local uv run — script knows exactly where Artel lives
    cfg = data["mcp_config"]
    cfg["mcpServers"]["artel"]["command"] = "uv"
    cfg["mcpServers"]["artel"]["args"] = ["run", "python", "-m", "artel.mcp"]
    cfg["mcpServers"]["artel"]["cwd"] = str(ARTEL_ROOT)

    out = target / ".mcp.json"
    out.write_text(json.dumps(cfg, indent=2) + "\n")

    print(f"  agent id  : {data['agent_id']}")
    print(f"  api key   : {data['api_key']}")
    print(f"  .mcp.json : {out}")
    print()
    print("reload plugins in Claude Code (/reload-plugins) to connect")


if __name__ == "__main__":
    main()
