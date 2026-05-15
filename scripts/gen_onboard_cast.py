#!/usr/bin/env python3
"""
Generate a clean synthetic asciinema cast showing the Artel onboarding flow.
Produces exactly what a first-time registration looks like — no terminal artifacts.
Usage: python3 scripts/gen_onboard_cast.py [out.cast]
"""

import json
import sys
import time

COLS = 92
ROWS = 12

GREEN = "\x1b[32m"
BLUE = "\x1b[34m"
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
CYAN = "\x1b[36m"

PROMPT = f"{GREEN}user@poseidon{RESET}:{BLUE}~/myproject{RESET}$ "


def make_cast(out_path):
    events = []
    t = 0.0

    def emit(data, delay=0.0):
        nonlocal t
        events.append((round(t, 4), "o", data))
        t = round(t + delay, 4)

    # Initial prompt
    emit(PROMPT, delay=0.6)

    # Type the command character by character
    cmd = "curl -s 'http://artel.local:8000/onboard?project=myproject' | sh"
    char_delay = 0.075
    for ch in cmd:
        emit(ch, delay=char_delay)

    # Brief pause before hitting Enter (user hovering over key)
    t = round(t + 0.4, 4)

    # Enter keypress
    emit("\r\n", delay=0.05)

    # Brief pause simulating network + registration
    t = round(t + 0.7, 4)

    # Registration output — emit line by line with natural pacing
    lines = [
        (f"  {BOLD}agent{RESET}    : poseidon-myproject", 0.18),
        (f"  {BOLD}project{RESET}  : myproject", 0.12),
        (f"  {BOLD}creds{RESET}    : ~/.config/artel/poseidon-myproject", 0.12),
        (f"  {BOLD}.mcp.json{RESET} written", 0.25),
        ("", 0.15),
        (f"{DIM}start a new Claude Code session to connect{RESET}", 0.6),
        ("", 0.15),
    ]
    for text, delay in lines:
        emit(text + "\r\n", delay=delay)

    # Final prompt
    emit(PROMPT, delay=1.2)

    header = {
        "version": 2,
        "width": COLS,
        "height": ROWS,
        "timestamp": int(time.time()),
        "title": "Artel — onboard in one command",
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
        "duration": round(t + 0.5, 4),
    }

    with open(out_path, "w") as f:
        f.write(json.dumps(header) + "\n")
        for ev in events:
            f.write(json.dumps(list(ev)) + "\n")

    print(f"→ {out_path}: {len(events)} events, {t:.1f}s", flush=True)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/artel-onboard.cast"
    make_cast(out)
