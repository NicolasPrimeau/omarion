#!/usr/bin/env python3
"""
Record the full Artel onboarding demo:
  1. bash: curl artel.local:8000/onboard?project=artel | sh  (registers agent, writes .mcp.json)
  2. bash: claude --dangerously-skip-permissions  (Claude Code starts with artel MCP loaded)
  3. Claude joins project, writes memory, sends message, renames itself

Usage:
  ARTEL_REG_KEY=devkey python3 scripts/record_full_onboard.py [out.cast]
  Then: agg out.cast docs/onboard-3.gif --font-size 16 --theme monokai --speed 3 --last-frame-duration 5
"""

import fcntl
import json
import os
import pty
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time

COLS = 100
ROWS = 30

ARTEL_URL = "http://artel.local:8000"
ARTEL_REG_KEY = os.environ.get("ARTEL_REG_KEY", "")
ARTEL_PROJECT = "artel"

CLAUDE_PROMPT = (
    "Use ONLY the artel MCP tools — no other tools, no browser, no bash. "
    "Run these 4 tool calls in order without preamble: "
    "1) project_join 'artel' "
    "2) memory_write content='Joined Artel — shared memory for AI agent fleets' tags=['onboarding'] "
    "3) message_send to='poseidon-artel' body='Hi from scout, just joined!' "
    "4) agent_rename to 'scout' "
    "Call each tool, show its result, then say Done."
)


def set_pty_size(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def send_keys(master, text, delay=0.045, pre_enter_pause=0.0):
    for ch in text:
        os.write(master, ch.encode())
        time.sleep(delay)
    if pre_enter_pause > 0:
        time.sleep(pre_enter_pause)
    os.write(master, b"\r")


TERMINAL_RESPONSES = [
    (b"\x1b[c", b"\x1b[?1;2c"),
    (b"\x1b[0c", b"\x1b[?1;2c"),
    (b"\x1b[>c", b"\x1b[>0;276;0c"),
    (b"\x1b[>0c", b"\x1b[>0;276;0c"),
]


def _kill_group(pid):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(pid), sig)
        except OSError:
            pass
        time.sleep(0.3)
        try:
            os.waitpid(pid, os.WNOHANG)
        except OSError:
            pass


def _dismiss_trust(data, trust_dismissed):
    if trust_dismissed:
        return trust_dismissed
    text = data.decode("utf-8", errors="replace")
    if "trust" in text.lower() or ("enter" in text.lower() and "folder" in text.lower()):
        time.sleep(0.3)
        return True
    return trust_dismissed


def record(out_cast, max_total=480):
    tmpdir = tempfile.mkdtemp(prefix="artel-demo-")
    subprocess.run(["git", "init", "-q", tmpdir], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            tmpdir,
            "remote",
            "add",
            "origin",
            f"https://github.com/example/{ARTEL_PROJECT}.git",
        ],
        check=True,
    )

    master, slave = pty.openpty()
    set_pty_size(master, ROWS, COLS)
    set_pty_size(slave, ROWS, COLS)

    pid = os.fork()
    if pid == 0:
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        os.dup2(slave, 0)
        os.dup2(slave, 1)
        os.dup2(slave, 2)
        for fd in range(3, 256):
            try:
                os.close(fd)
            except OSError:
                pass
        os.chdir(tmpdir)
        env = dict(os.environ)
        env["TERM"] = "xterm-256color"
        env["COLUMNS"] = str(COLS)
        env["LINES"] = str(ROWS)
        env["PS1"] = r"\[\e[32m\]user@poseidon\[\e[0m\]:\[\e[34m\]\W\[\e[0m\]\$ "
        env["AGENT_ID"] = "demo-user"
        if ARTEL_REG_KEY:
            env["ARTEL_REG_KEY"] = ARTEL_REG_KEY
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        os.execvpe("bash", ["bash", "--norc", "--noprofile"], env)
        sys.exit(1)

    os.close(slave)

    events = []
    t0 = time.time()
    last_data = time.time()
    accumulated = b""
    trust_dismissed = False

    # States:
    # 0 - wait bash prompt -> curl onboard
    # 1 - wait onboard done -> launch claude
    # 2 - wait claude ready -> send artel demo prompt
    # 3 - wait claude done -> state 6
    # 6 - done
    state = 0
    state_entered = time.time()

    print(f"Recording -> {out_cast}", flush=True)

    try:
        while time.time() - t0 < max_total and state != 6:
            r, _, _ = select.select([master], [], [], 0.05)
            if r:
                try:
                    data = os.read(master, 65536)
                    if not data:
                        break
                    events.append(
                        (round(time.time() - t0, 4), "o", data.decode("utf-8", errors="replace"))
                    )
                    for q, resp in TERMINAL_RESPONSES:
                        if q in data:
                            try:
                                os.write(master, resp)
                            except OSError:
                                pass
                    new_dismissed = _dismiss_trust(data, trust_dismissed)
                    if new_dismissed and not trust_dismissed:
                        os.write(master, b"\r")
                        trust_dismissed = new_dismissed
                    accumulated += data
                    last_data = time.time()
                except OSError:
                    break

            elapsed = time.time() - t0
            idle = time.time() - last_data
            in_state = time.time() - state_entered
            text = accumulated.decode("utf-8", errors="replace")
            tail = text[-600:]

            if state == 0:
                if "$ " in text and in_state > 1.5:
                    print(f"  [t={elapsed:.1f}s] bash ready -- running onboard", flush=True)
                    time.sleep(0.5)
                    send_keys(
                        master,
                        f"curl -fsSL {ARTEL_URL}/onboard | sh",
                        delay=0.10,
                        pre_enter_pause=2.0,
                    )
                    accumulated = b""
                    state = 1
                    state_entered = time.time()

            elif state == 1:
                if "to connect" in text and idle > 1.5:
                    print(f"  [t={elapsed:.1f}s] onboard done -- launching claude", flush=True)
                    time.sleep(1.2)
                    send_keys(master, "claude --dangerously-skip-permissions", delay=0.07)
                    accumulated = b""
                    state = 2
                    state_entered = time.time()
                elif in_state > 30:
                    print(f"  [t={elapsed:.1f}s] timeout waiting for onboard", flush=True)
                    state = 6

            elif state == 2:
                if "❯" in text and idle > 2.0:
                    print(f"  [t={elapsed:.1f}s] claude ready -- sending prompt", flush=True)
                    time.sleep(0.8)
                    send_keys(master, CLAUDE_PROMPT, delay=0.03)
                    accumulated = b""
                    state = 3
                    state_entered = time.time()
                elif in_state > 90:
                    print(f"  [t={elapsed:.1f}s] timeout waiting for claude", flush=True)
                    state = 6

            elif state == 3:
                if "❯" in tail and idle > 4 and in_state > 20:
                    print(f"  [t={elapsed:.1f}s] claude done", flush=True)
                    state = 6
                elif in_state > 150:
                    print(f"  [t={elapsed:.1f}s] hard timeout -- cutting", flush=True)
                    state = 6

    finally:
        print("  killing process group", flush=True)
        _kill_group(pid)

        drain_end = time.time() + 2.0
        while time.time() < drain_end:
            r, _, _ = select.select([master], [], [], 0.2)
            if not r:
                break
            try:
                data = os.read(master, 65536)
                if data:
                    events.append(
                        (round(time.time() - t0, 4), "o", data.decode("utf-8", errors="replace"))
                    )
                else:
                    break
            except OSError:
                break

        try:
            os.close(master)
        except OSError:
            pass

        shutil.rmtree(tmpdir, ignore_errors=True)

    if not events:
        print("no events -- aborting", flush=True)
        sys.exit(1)

    duration = events[-1][0]
    header = {
        "version": 2,
        "width": COLS,
        "height": ROWS,
        "timestamp": int(time.time()),
        "title": "Artel -- onboard and explore",
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
        "duration": round(duration + 2.0, 4),
    }

    with open(out_cast, "w") as f:
        f.write(json.dumps(header) + "\n")
        for ev in events:
            f.write(json.dumps(list(ev)) + "\n")

    print(f"  -> {len(events)} events, {duration:.0f}s -> {out_cast}", flush=True)


if __name__ == "__main__":
    if not ARTEL_REG_KEY:
        print("error: set ARTEL_REG_KEY in env", flush=True)
        sys.exit(1)
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/artel-full-onboard.cast"
    record(out)
    print("done", flush=True)
