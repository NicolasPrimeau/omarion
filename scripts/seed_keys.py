#!/usr/bin/env python3
import secrets
import sys
from pathlib import Path


_DERIVED = {
    "archivist": "ARCHIVIST_KEY",
    "nimbus": "MCP_AGENT_KEY",
}


def main():
    agents = sys.argv[1:] or ["nimbus", "archivist", "steward"]
    key_map = {a: secrets.token_urlsafe(32) for a in agents}
    pairs = [f"{a}:{k}" for a, k in key_map.items()]

    env_path = Path(".env")
    drop_prefixes = {"AGENT_KEYS="} | {f"{v}=" for v in _DERIVED.values()}

    if env_path.exists():
        lines = [
            l for l in env_path.read_text().splitlines()
            if not any(l.startswith(p) for p in drop_prefixes)
        ]
        content = "\n".join(lines).rstrip() + "\n"
    else:
        content = ""

    content += f"AGENT_KEYS={','.join(pairs)}\n"
    for agent, env_var in _DERIVED.items():
        if agent in key_map:
            content += f"{env_var}={key_map[agent]}\n"

    env_path.write_text(content)

    print("Keys written to .env:")
    for agent, key in key_map.items():
        print(f"  {agent}: {key}")


if __name__ == "__main__":
    main()
