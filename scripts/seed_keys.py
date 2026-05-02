#!/usr/bin/env python3
import secrets
import sys
from pathlib import Path


def main():
    agents = sys.argv[1:] or ["nimbus", "archivist", "steward"]
    pairs = [f"{a}:{secrets.token_urlsafe(32)}" for a in agents]

    env_path = Path(".env")
    if env_path.exists():
        lines = [l for l in env_path.read_text().splitlines() if not l.startswith("AGENT_KEYS=")]
        content = "\n".join(lines).rstrip() + "\n"
    else:
        content = ""

    content += f"AGENT_KEYS={','.join(pairs)}\n"
    env_path.write_text(content)

    print("Keys written to .env:")
    for pair in pairs:
        agent, key = pair.split(":", 1)
        print(f"  {agent}: {key}")


if __name__ == "__main__":
    main()
