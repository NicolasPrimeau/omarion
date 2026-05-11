#!/usr/bin/env python3
"""
Record a claude session directly via PTY.
Usage: artel-demo-record.py <agent_dir> <output.cast>
"""

import fcntl
import json
import os
import pty
import select
import signal
import struct
import sys
import termios
import time

COLS = 110
ROWS = 18  # half-height; will be combined later

NOVA_PROMPT = (
    "check context and inbox. "
    "you just finished a rate limiting middleware. "
    "write the key design decisions to memory, open a review task for orion, message them to take a look."
)

NOVA2_PROMPT = (
    "check your session context. "
    "you're the same agent on a different machine. pick up exactly where you left off."
)

ORION_PROMPT = (
    "check inbox and context. "
    "nova wants a code review — look over what they wrote and give your verdict."
)

TERMINAL_RESPONSES = [
    (b"\x1b[c", b"\x1b[?1;2c"),
    (b"\x1b[0c", b"\x1b[?1;2c"),
    (b"\x1b[>c", b"\x1b[>0;276;0c"),
    (b"\x1b[>0c", b"\x1b[>0;276;0c"),
]


def set_pty_size(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def send_keys(master, text):
    chunk = 32
    for i in range(0, len(text), chunk):
        os.write(master, text[i : i + chunk].encode())
        time.sleep(0.04)
    os.write(master, b"\r")


def record(agent_dir, prompt, out_cast, startup_wait=12, idle_cutoff=60, max_total=300):
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
        os.chdir(agent_dir)
        env = dict(os.environ)
        env["TERM"] = "xterm-256color"
        env["COLUMNS"] = str(COLS)
        env["LINES"] = str(ROWS)
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        os.execvpe("claude", ["claude", "--dangerously-skip-permissions"], env)
        sys.exit(1)

    os.close(slave)
    events = []
    t0 = time.time()
    prompt_sent = False
    last_data = time.time()
    deadline = time.time() + max_total

    trust_dismissed = False

    while time.time() < deadline:
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
                # Auto-dismiss directory trust dialog (appears when claude starts
                # in an unrecognized directory and waits for Enter to confirm)
                if not trust_dismissed and not prompt_sent:
                    text = data.decode("utf-8", errors="replace")
                    if "trust" in text.lower() or (
                        "enter" in text.lower() and "folder" in text.lower()
                    ):
                        time.sleep(0.3)
                        os.write(master, b"\r")
                        trust_dismissed = True
                last_data = time.time()
            except OSError:
                break
        else:
            elapsed = time.time() - t0
            if not prompt_sent and elapsed >= startup_wait:
                print(f"  sending prompt at t={elapsed:.1f}s", flush=True)
                send_keys(master, prompt)
                prompt_sent = True
                last_data = time.time()
            if prompt_sent and (time.time() - last_data) > idle_cutoff:
                print(f"  {idle_cutoff}s idle — done", flush=True)
                break

    # Drain remaining output
    while True:
        r, _, _ = select.select([master], [], [], 0.3)
        if not r:
            break
        try:
            data = os.read(master, 65536)
            if data:
                events.append(
                    (round(time.time() - t0, 4), "o", data.decode("utf-8", errors="replace"))
                )
        except OSError:
            break

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    try:
        os.waitpid(pid, os.WNOHANG)
    except OSError:
        pass
    try:
        os.close(master)
    except OSError:
        pass

    header = {
        "version": 2,
        "width": COLS,
        "height": ROWS,
        "timestamp": int(time.time()),
        "title": f"Artel demo — {os.path.basename(agent_dir)}",
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
    }
    if events:
        header["duration"] = events[-1][0] + 1.0

    with open(out_cast, "w") as f:
        f.write(json.dumps(header) + "\n")
        for row in events:
            f.write(json.dumps(list(row)) + "\n")

    print(
        f"  → {out_cast}: {len(events)} events, {events[-1][0] if events else 0:.1f}s real",
        flush=True,
    )


if __name__ == "__main__":
    agent = sys.argv[1] if len(sys.argv) > 1 else "nova"
    out = sys.argv[2] if len(sys.argv) > 2 else f"/tmp/artel-{agent}.cast"
    adir = f"/tmp/artel-demo/{agent}"
    if agent == "nova2":
        prompt = NOVA2_PROMPT
    elif agent == "nova":
        prompt = NOVA_PROMPT
    else:
        prompt = ORION_PROMPT
    print(f"Recording {agent}...", flush=True)
    record(adir, prompt, out)
