#!/usr/bin/env python3
"""
Record the Artel onboarding flow (curl onboard | sh) as an asciinema cast.
Usage: python3 scripts/record_onboard.py [out.cast]
       ARTEL_URL=http://artel.local:8000 python3 scripts/record_onboard.py
"""

import fcntl
import json
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time

COLS = 96
ROWS = 24

ARTEL_URL = os.environ.get("ARTEL_URL", "http://localhost:8000")
ARTEL_REG_KEY = os.environ.get("ARTEL_REG_KEY", "")
ARTEL_PROJECT = os.environ.get("ARTEL_PROJECT", "myproject")


def set_pty_size(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def send_keys(master, text, delay=0.05):
    for ch in text:
        os.write(master, ch.encode())
        time.sleep(delay)
    os.write(master, b"\r")


def record(out_cast, startup_wait=2.5, idle_cutoff=12, max_total=60):
    tmpdir = tempfile.mkdtemp(prefix="artel-onboard-")
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
        env["PS1"] = r"\[\e[32m\]\u@\h\[\e[0m\]:\[\e[34m\]\W\[\e[0m\]$ "
        if ARTEL_REG_KEY:
            env["ARTEL_REG_KEY"] = ARTEL_REG_KEY
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        os.execvpe("bash", ["bash", "--norc", "--noprofile"], env)
        sys.exit(1)

    os.close(slave)

    events = []
    t0 = time.time()
    prompt_sent = False
    last_data = time.time()

    TERMINAL_RESPONSES = [
        (b"\x1b[c", b"\x1b[?1;2c"),
        (b"\x1b[0c", b"\x1b[?1;2c"),
        (b"\x1b[>c", b"\x1b[>0;276;0c"),
    ]

    while time.time() - t0 < max_total:
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
                last_data = time.time()
            except OSError:
                break
        else:
            elapsed = time.time() - t0
            if not prompt_sent and elapsed >= startup_wait:
                print(f"  typing curl command at t={elapsed:.1f}s", flush=True)
                time.sleep(0.3)
                cmd = f"curl -s '{ARTEL_URL}/onboard?project={ARTEL_PROJECT}' | sh"
                send_keys(master, cmd, delay=0.04)
                prompt_sent = True
                last_data = time.time()
            if prompt_sent and (time.time() - last_data) > idle_cutoff:
                print(f"  {idle_cutoff}s idle — wrapping up", flush=True)
                break

    # Drain remaining output
    while True:
        r, _, _ = select.select([master], [], [], 0.5)
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

    import shutil

    shutil.rmtree(tmpdir, ignore_errors=True)

    if not events:
        print("no events recorded — aborting", flush=True)
        sys.exit(1)

    header = {
        "version": 2,
        "width": COLS,
        "height": ROWS,
        "timestamp": int(time.time()),
        "title": "Artel — onboard in one command",
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
        "duration": events[-1][0] + 1.0,
    }

    with open(out_cast, "w") as f:
        f.write(json.dumps(header) + "\n")
        for row in events:
            f.write(json.dumps(list(row)) + "\n")

    print(f"  → {out_cast}: {len(events)} events, {events[-1][0]:.1f}s real", flush=True)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/artel-onboard.cast"
    print(f"Recording onboard flow → {out}", flush=True)
    record(out)
    print("done", flush=True)
