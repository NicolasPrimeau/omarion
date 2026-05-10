#!/usr/bin/env python3
import secrets
import sys
from pathlib import Path

_AGENTS = ["archivist", "mcp"]


def main():
    agents = sys.argv[1:] or _AGENTS
    key_map = {a: secrets.token_urlsafe(32) for a in agents}
    pairs = [f"{a}:{k}" for a, k in key_map.items()]

    env_path = Path(".env")
    if env_path.exists():
        lines = [
            line for line in env_path.read_text().splitlines() if not line.startswith("AGENT_KEYS=")
        ]
        content = "\n".join(lines).rstrip() + "\n"
    else:
        content = ""

    content += f"AGENT_KEYS={','.join(pairs)}\n"
    env_path.write_text(content)

    print("Keys written to .env:")
    for agent, key in key_map.items():
        print(f"  {agent}: {key}")


if __name__ == "__main__":
    main()
