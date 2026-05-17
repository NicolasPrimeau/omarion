#!/usr/bin/env python3
"""
Generate a synthetic asciinema cast showing the Artel mesh network demo.
Two instances discover each other on LAN, link, and memory replicates.

Usage:
    python3 scripts/gen_mesh_cast.py [out.cast]
    agg out.cast docs/mesh_network.gif --font-size 15 --theme monokai --speed 1.2 --last-frame-duration 4
"""

import json
import sys
import time

COLS = 96
ROWS = 30

R = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
CYAN = "\x1b[36m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
MAGENTA = "\x1b[35m"
BLUE = "\x1b[34m"
WHITE = "\x1b[97m"
GRAY = "\x1b[90m"

A_COLOR = CYAN
B_COLOR = MAGENTA

PROMPT_A = f"{GREEN}alpha@machine-1{R}:{BLUE}~{R}$ "
PROMPT_B = f"{GREEN}beta@machine-2{R}:{BLUE}~{R}$ "

DIVIDER = f"{GRAY}{'─' * 96}{R}"


def make_cast(out_path):
    events = []
    t = 0.0

    def emit(data, delay=0.0):
        nonlocal t
        events.append((round(t, 4), "o", data))
        t = round(t + delay, 4)

    def pause(s):
        nonlocal t
        t = round(t + s, 4)

    def line(text="", delay=0.12):
        emit(text + "\r\n", delay=delay)

    def type_cmd(prompt, cmd, pre=0.4, post=0.6):
        emit(prompt)
        pause(pre)
        for ch in cmd:
            emit(ch, delay=0.055)
        pause(0.35)
        emit("\r\n", delay=post)

    def label(tag, color, text):
        emit(f"  {color}{BOLD}[{tag}]{R}  {text}\r\n", delay=0.1)

    def section(title):
        line()
        emit(f"{BOLD}{WHITE}{title}{R}\r\n", delay=0.2)
        line(DIVIDER, delay=0.05)

    # ── Intro ─────────────────────────────────────────────────────────────────
    pause(0.3)
    section("Artel mesh network — two instances, one LAN")
    line()
    line(f"  {A_COLOR}{BOLD}alpha{R}  artel on machine-1  (port 8001)", 0.12)
    line(f"  {B_COLOR}{BOLD}beta{R}   artel on machine-2  (port 8002)", 0.12)
    line()
    pause(1.0)

    # ── mDNS discovery ────────────────────────────────────────────────────────
    section("Step 1 — instances advertise themselves via mDNS (_artel._tcp.local.)")
    pause(0.3)
    line(f"  {GRAY}alpha broadcasts:  artel-a1b2c3d4.local. → :8001{R}", 0.15)
    line(f"  {GRAY}beta  broadcasts:  artel-e5f6a7b8.local. → :8002{R}", 0.15)
    pause(0.8)
    line()
    line(f'  {GREEN}✓{R}  beta appears in alpha\'s Mesh tab — {BOLD}"Discovered on LAN"{R}', 0.2)
    line(f'  {GREEN}✓{R}  alpha appears in beta\'s Mesh tab  — {BOLD}"Discovered on LAN"{R}', 0.2)
    pause(1.2)

    # ── One-click link ────────────────────────────────────────────────────────
    section("Step 2 — owner on alpha clicks Link → mutual handshake")
    pause(0.3)
    line(f"  {GRAY}POST http://machine-1:8001/mesh/link-discovered{R}", 0.12)
    line(f"  {GRAY}  instance_id: artel-e5f6a7b8  project: null{R}", 0.12)
    pause(0.9)
    line()
    line(f"  {GRAY}→  alpha generates token  tok_a ··············{R}", 0.15)
    line(f"  {GRAY}→  alpha calls POST http://machine-2:8002/mesh/handshake{R}", 0.15)
    pause(0.7)
    line(f"  {GRAY}←  beta creates peer link ← alpha (using tok_a){R}", 0.15)
    line(f"  {GRAY}←  beta generates token  tok_b ··············{R}", 0.15)
    line(f"  {GRAY}←  returns tok_b{R}", 0.12)
    pause(0.6)
    line(f"  {GRAY}→  alpha creates peer link → beta (using tok_b){R}", 0.15)
    pause(0.8)
    line()
    line(f"  {GREEN}✓{R}  both instances now subscribe to each other's feed", 0.2)
    line(f"  {GREEN}✓{R}  no URL typed, no token copy-pasted", 0.2)
    pause(1.5)

    # ── Write on alpha ────────────────────────────────────────────────────────
    section("Step 3 — alpha writes a memory entry")
    type_cmd(
        PROMPT_A,
        "curl -s -X POST http://machine-1:8001/memory \\",
        pre=0.5,
        post=0.0,
    )
    emit("  -H 'x-agent-id: alpha' -H 'x-api-key: ···' \\\r\n", delay=0.1)
    emit(
        '  -d \'{"content":"Rate limiter deployed — 99th p. latency down 40%","project":"ops"}\'\r\n',
        delay=0.8,
    )
    pause(0.4)
    line()
    line(f"  {GRAY}{{{R}", 0.05)
    line(f'  {GRAY}  "id": "mem-4a2f··",{R}', 0.08)
    line(f'  {GRAY}  "content": "Rate limiter deployed — 99th p. latency down 40%",{R}', 0.08)
    line(f'  {GRAY}  "origin": "artel-a1b2c3d4",{R}', 0.08)
    line(f'  {GRAY}  "project": "ops"{R}', 0.08)
    line(f"  {GRAY}}}{R}", 0.08)
    pause(1.0)

    # ── Feed poll ─────────────────────────────────────────────────────────────
    section("Step 4 — beta's feed poller picks it up (next 30-min interval)")
    pause(0.3)
    line(
        f"  {GRAY}GET http://machine-1:8001/memory/feed.json?mesh_token=tok_b&project=ops{R}", 0.15
    )
    pause(0.9)
    line(f"  {GRAY}← 1 new item  origin=artel-a1b2c3d4{R}", 0.12)
    pause(0.5)
    line(f"  {GRAY}→  beta ingests  mem-4a2f··  (origin preserved){R}", 0.12)
    pause(0.6)
    line()

    # ── Verify on beta ────────────────────────────────────────────────────────
    type_cmd(
        PROMPT_B,
        "curl -s 'http://machine-2:8002/memory/mem-4a2f' -H 'x-agent-id: beta' -H 'x-api-key: ···'",
        pre=0.5,
        post=0.6,
    )
    line(f"  {GRAY}{{{R}", 0.05)
    line(f'  {GRAY}  "id": "mem-4a2f··",{R}', 0.08)
    line(f'  {GRAY}  "content": "Rate limiter deployed — 99th p. latency down 40%",{R}', 0.08)
    line(f'  {GRAY}  "origin": "artel-a1b2c3d4",    <- alpha origin preserved{R}', 0.08)
    line(f'  {GRAY}  "project": "ops"{R}', 0.08)
    line(f"  {GRAY}}}{R}", 0.12)
    pause(0.8)
    line()
    line(f"  {GREEN}✓{R}  memory replicated  {A_COLOR}alpha → beta{R}", 0.2)
    line(f"  {GREEN}✓{R}  origin preserved — beta's archivist won't re-synthesize it", 0.2)
    line(f"  {GREEN}✓{R}  re-polling is idempotent — no duplicates, no loops", 0.2)
    pause(2.0)

    # ── Footer ────────────────────────────────────────────────────────────────
    line()
    line(DIVIDER, 0.05)
    line(
        f"  {BOLD}Artel{R} · self-hosted mesh for AI agent fleets · {CYAN}github.com/NicolasPrimeau/artel{R}",
        0.15,
    )
    line()
    pause(3.0)

    header = {
        "version": 2,
        "width": COLS,
        "height": ROWS,
        "timestamp": int(time.time()),
        "title": "Artel — mesh network demo",
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
        "duration": round(t + 0.5, 4),
    }

    with open(out_path, "w") as f:
        f.write(json.dumps(header) + "\n")
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    print(f"wrote {out_path}  ({len(events)} events, {t:.1f}s)")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "mesh_network.cast"
    make_cast(out)
