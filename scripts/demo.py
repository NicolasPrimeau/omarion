#!/usr/bin/env python3
"""
Artel demo script — stage for asciinema recording.

Usage:
    python3 scripts/demo.py

Records well with:
    asciinema rec artel-demo.cast --cols 88 --rows 32
    agg artel-demo.cast artel-demo.gif --speed 1.5 --font-size 15
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager

ARTEL_URL = "http://192.168.183.2:8000"
REG_KEY = "devkey"

# ── ANSI palette ──────────────────────────────────────────────────────────────
R = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
RED = "\033[31m"
BLUE = "\033[34m"
WHITE = "\033[97m"
BG_DARK = "\033[48;5;235m"

ALPHA_COLOR = CYAN
BETA_COLOR = MAGENTA


# ── Helpers ───────────────────────────────────────────────────────────────────


def _req(method, path, *, agent_id, api_key, data=None, params=None):
    url = ARTEL_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body = json.dumps(data).encode() if data is not None else None
    headers = {
        "x-agent-id": agent_id,
        "x-api-key": api_key,
        "content-type": "application/json",
    }
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def _register(agent_id):
    req = urllib.request.Request(
        ARTEL_URL + "/agents/self-register",
        data=json.dumps({"agent_id": agent_id}).encode(),
        headers={
            "content-type": "application/json",
            "x-registration-key": REG_KEY,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        if "already" in str(body.get("detail", "")):
            raise ValueError(f"agent_id '{agent_id}' already taken — pick another")
        raise


def _deregister(agent_id, api_key):
    try:
        _req("DELETE", "/agents/me", agent_id=agent_id, api_key=api_key)
    except Exception:
        pass


# ── Display ───────────────────────────────────────────────────────────────────


def _flush(text="", end="\n"):
    sys.stdout.write(text + end)
    sys.stdout.flush()


def _type(text, color="", delay=0.03):
    for ch in text:
        sys.stdout.write(color + ch + R)
        sys.stdout.flush()
        time.sleep(delay)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _pause(s=0.6):
    time.sleep(s)


def _header(text):
    _flush()
    width = 70
    bar = "─" * width
    _flush(f"{BOLD}{WHITE}{bar}{R}")
    _flush(f"{BOLD}{WHITE}  {text}{R}")
    _flush(f"{BOLD}{WHITE}{bar}{R}")
    _pause(0.4)


def _act(n, title):
    _flush()
    _flush(f"{BOLD}{YELLOW}  ◆ ACT {n}  {title}{R}")
    _flush(f"{DIM}  {'─' * 50}{R}")
    _pause(0.5)


def _agent(name, color, msg, *, delay=0.025):
    prefix = f"  {BOLD}{color}[{name}]{R} "
    _flush(prefix, end="")
    for ch in msg:
        sys.stdout.write(color + ch + R)
        sys.stdout.flush()
        time.sleep(delay)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _result(label, value, color=DIM):
    _flush(f"  {DIM}    └─ {label}: {color}{value}{R}")


def _ok(msg):
    _flush(f"  {GREEN}    ✓ {msg}{R}")


def _divider():
    _flush(f"\n{DIM}  {'·' * 60}{R}\n")


@contextmanager
def _spinner(msg):
    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    sys.stdout.write(f"  {DIM}{frames[0]} {msg}…{R}")
    sys.stdout.flush()
    yield
    sys.stdout.write(f"\r  {GREEN}✓ {msg}{R}          \n")
    sys.stdout.flush()


# ── Demo ──────────────────────────────────────────────────────────────────────


def run():
    _flush("\033[2J\033[H")  # clear screen

    _flush()
    _flush(f"{BOLD}{WHITE}  ╔══════════════════════════════════════════════╗{R}")
    _flush(f"{BOLD}{WHITE}  ║  {CYAN}ARTEL{WHITE}  ─  multi-agent coordination demo   ║{R}")
    _flush(f"{BOLD}{WHITE}  ╚══════════════════════════════════════════════╝{R}")
    _pause(1.0)

    _flush()
    _type("  Two agents. One shared brain.", WHITE, delay=0.04)
    _type("  No framework coupling. No repeated work.", DIM, delay=0.03)
    _pause(1.2)

    # ── Setup: register two demo agents ──────────────────────────────────────
    _header("SETUP — registering demo agents")

    alpha_name = "nova"
    beta_name = "orion"

    _agent(alpha_name, ALPHA_COLOR, "registering…")
    _pause(0.3)
    alpha = _register(alpha_name)
    alpha_id, alpha_key = alpha["agent_id"], alpha["api_key"]
    _ok(f"{alpha_name}  ·  key: {alpha_key[:12]}…")

    _pause(0.2)
    _agent(beta_name, BETA_COLOR, "registering…")
    _pause(0.3)
    beta = _register(beta_name)
    beta_id, beta_key = beta["agent_id"], beta["api_key"]
    _ok(f"{beta_name}  ·  key: {beta_key[:12]}…")

    _pause(0.8)

    # ── ACT I — Discovery ─────────────────────────────────────────────────────
    _act(1, "DISCOVERY")

    _agent(alpha_name, ALPHA_COLOR, "checking shared memory before I start work…")
    _pause(0.4)
    results = _req(
        "GET",
        "/memory/search",
        agent_id=alpha_id,
        api_key=alpha_key,
        params={"q": "database latency orders service", "limit": "3"},
    )
    if results:
        _result("found", f"{len(results)} related entries", GREEN)
    else:
        _result("found", "nothing — this is new territory", DIM)
    _pause(0.6)

    _agent(alpha_name, ALPHA_COLOR, "writing observation to shared memory…")
    _pause(0.3)
    mem = _req(
        "POST",
        "/memory",
        agent_id=alpha_id,
        api_key=alpha_key,
        data={
            "content": (
                "Production alert: p99 latency on orders-service spiked to 4.2s at 03:14 UTC. "
                "Affects checkout flow. No deploy in the last 6 hours. DB CPU normal."
            ),
            "type": "memory",
            "scope": "project",
            "tags": ["incident", "orders", "latency", "production"],
            "confidence": 1.0,
            "parents": [],
        },
    )
    _ok(f"memory written  [{mem['id'][:8]}…]  tags: incident, orders, latency")
    _pause(0.4)

    _agent(alpha_name, ALPHA_COLOR, "creating task for the fleet…")
    _pause(0.3)
    task = _req(
        "POST",
        "/tasks",
        agent_id=alpha_id,
        api_key=alpha_key,
        data={
            "title": "Investigate p99 latency spike on orders-service",
            "description": "Started 03:14 UTC. No recent deploy. p99=4.2s, p50 normal. DB CPU normal.",
            "expected_outcome": "Root cause identified and documented in shared memory.",
            "priority": "high",
        },
    )
    _ok(f"task created  [{task['id'][:8]}…]  priority: {task['priority']}")
    _pause(0.5)

    _agent(alpha_name, ALPHA_COLOR, "messaging orion — this needs eyes now")
    _pause(0.3)
    msg = _req(
        "POST",
        "/messages",
        agent_id=alpha_id,
        api_key=alpha_key,
        data={
            "to": beta_name,
            "subject": "⚠ orders latency spike",
            "body": (
                f"p99 latency hit 4.2s at 03:14 UTC. I've filed task [{task['id'][:8]}]. "
                "Memory has the details. Can you dig into the DB query patterns?"
            ),
        },
    )
    _ok(f"message sent → {beta_name}  [{msg['id'][:8]}…]")

    _divider()

    # ── ACT II — Coordination ─────────────────────────────────────────────────
    _act(2, "COORDINATION")

    _agent(beta_name, BETA_COLOR, "checking inbox…")
    _pause(0.5)
    inbox = _req("GET", "/messages/inbox", agent_id=beta_id, api_key=beta_key)
    if inbox:
        m = inbox[0]
        _ok(f"message from {m['from_agent']}  ·  {m['subject']!r}")
        _result("body", m["body"][:80] + "…", DIM)
    _req("POST", "/messages/inbox/read-all", agent_id=beta_id, api_key=beta_key)
    _pause(0.5)

    _agent(beta_name, BETA_COLOR, "searching memory — what do I know about this?")
    _pause(0.4)
    findings = _req(
        "GET",
        "/memory/search",
        agent_id=beta_id,
        api_key=beta_key,
        params={"q": "orders service latency incident", "limit": "3"},
    )
    for f in findings[:2]:
        snippet = f["content"][:75].replace("\n", " ")
        _result("match", f"[{f['id'][:8]}] {snippet}…", DIM)
    _pause(0.5)

    _agent(beta_name, BETA_COLOR, f"claiming task [{task['id'][:8]}…]")
    _pause(0.3)
    _req("POST", f"/tasks/{task['id']}/claim", agent_id=beta_id, api_key=beta_key)
    _ok("task claimed — on it")
    _pause(0.7)

    _agent(beta_name, BETA_COLOR, "found it. writing root cause to shared memory…")
    _pause(0.4)
    fix_mem = _req(
        "POST",
        "/memory",
        agent_id=beta_id,
        api_key=beta_key,
        data={
            "content": (
                "Root cause (orders latency spike, 2026-05-10 03:14 UTC): "
                "Missing composite index on orders(customer_id, created_at). "
                "A recent data backfill added 4M rows — full table scans on the orders listing query. "
                "Fix: CREATE INDEX CONCURRENTLY idx_orders_cust_created ON orders(customer_id, created_at). "
                "Deploying now. ETA 8 min."
            ),
            "type": "memory",
            "scope": "project",
            "tags": ["incident", "orders", "root-cause", "db-index", "resolved"],
            "confidence": 1.0,
            "parents": [mem["id"]],
        },
    )
    _ok(f"root cause written  [{fix_mem['id'][:8]}…]  parents: [{mem['id'][:8]}]")
    _pause(0.4)

    _agent(beta_name, BETA_COLOR, "notifying nova — fix is deploying")
    _pause(0.3)
    _req(
        "POST",
        "/messages",
        agent_id=beta_id,
        api_key=beta_key,
        data={
            "to": alpha_name,
            "subject": "✓ root cause found + fix deploying",
            "body": (
                f"Missing index on orders(customer_id, created_at) — backfill added 4M rows. "
                f"CREATE INDEX CONCURRENTLY running now, ETA 8 min. "
                f"Memory [{fix_mem['id'][:8]}] has the full write-up."
            ),
        },
    )
    _ok(f"message sent → {alpha_name}")

    _divider()

    # ── ACT III — Resolution ──────────────────────────────────────────────────
    _act(3, "RESOLUTION")

    _agent(beta_name, BETA_COLOR, "index built — completing task")
    _pause(0.4)
    _req("POST", f"/tasks/{task['id']}/complete", agent_id=beta_id, api_key=beta_key)
    _ok(f"task [{task['id'][:8]}…]  status: completed")
    _pause(0.5)

    _agent(alpha_name, ALPHA_COLOR, "reading inbox — checking for updates…")
    _pause(0.4)
    inbox2 = _req("GET", "/messages/inbox", agent_id=alpha_id, api_key=alpha_key)
    if inbox2:
        m2 = inbox2[0]
        _ok(f"message from {m2['from_agent']}  ·  {m2['subject']!r}")
        _result("body", m2["body"][:80] + "…", DIM)
    _req("POST", "/messages/inbox/read-all", agent_id=alpha_id, api_key=alpha_key)
    _pause(0.4)

    _agent(alpha_name, ALPHA_COLOR, "verifying via semantic search — is the fix documented?")
    _pause(0.4)
    verify = _req(
        "GET",
        "/memory/search",
        agent_id=alpha_id,
        api_key=alpha_key,
        params={"q": "orders index fix deployment", "limit": "2"},
    )
    for v in verify:
        snippet = v["content"][:80].replace("\n", " ")
        _result(f"[{v['id'][:8]}]", snippet + "…", GREEN)
    _pause(0.6)

    _divider()

    # ── ACT IV — Handoff ──────────────────────────────────────────────────────
    _act(4, "SESSION HANDOFF")

    _agent(beta_name, BETA_COLOR, "saving session handoff before going idle…")
    _pause(0.3)
    handoff = _req(
        "POST",
        "/sessions/handoff",
        agent_id=beta_id,
        api_key=beta_key,
        data={
            "summary": (
                "Resolved orders-service p99 latency spike (03:14 UTC). "
                "Root cause: missing composite index after 4M-row backfill. "
                "Index deployed, latency back to p99 <200ms."
            ),
            "next_steps": [
                "Monitor orders p99 over next 24h",
                "Add index coverage check to pre-backfill runbook",
            ],
            "in_progress": [],
        },
    )
    _ok(f"handoff saved  [{handoff['id'][:8]}…]")
    _pause(0.5)

    _agent(beta_name, BETA_COLOR, "new session — loading my own context to verify…")
    _pause(0.4)
    ctx = _req("GET", f"/sessions/handoff/{beta_name}", agent_id=beta_id, api_key=beta_key)
    h = ctx.get("last_handoff")
    if h:
        _ok(f"context loaded  [{h['id'][:8]}…]")
        _result("summary", h["summary"][:80] + "…", DIM)
        for step in h.get("next_steps") or []:
            _result("next", step, DIM)
    _pause(0.6)

    _agent(alpha_name, ALPHA_COLOR, "fleet-wide search — what do we know about this incident?")
    _pause(0.4)
    final = _req(
        "GET",
        "/memory/search",
        agent_id=alpha_id,
        api_key=alpha_key,
        params={"q": "orders latency incident root cause resolved", "limit": "3"},
    )
    for v in final:
        snippet = v["content"][:72].replace("\n", " ")
        _result(f"[{v['id'][:8]}]", snippet + "…", GREEN)
    _pause(0.8)

    # ── Finale ────────────────────────────────────────────────────────────────
    _flush()
    _flush(f"{BOLD}{WHITE}  ╔══════════════════════════════════════════════╗{R}")
    _flush(f"{BOLD}{WHITE}  ║  {GREEN}incident resolved in < 4 minutes{WHITE}            ║{R}")
    _flush(
        f"{BOLD}{WHITE}  ║  {DIM}two agents · zero repeated work{WHITE}             {R}{BOLD}{WHITE}║{R}"
    )
    _flush(
        f"{BOLD}{WHITE}  ║  {DIM}memory, tasks, messages, handoffs — live{WHITE}    {R}{BOLD}{WHITE}║{R}"
    )
    _flush(f"{BOLD}{WHITE}  ╚══════════════════════════════════════════════╝{R}")
    _pause(1.0)

    # ── Cleanup ───────────────────────────────────────────────────────────────
    _flush()
    _flush(f"{DIM}  cleaning up demo agents…{R}")
    _deregister(alpha_id, alpha_key)
    _deregister(beta_id, beta_key)
    _flush(f"{DIM}  done.{R}\n")


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        _flush(f"\n{DIM}  interrupted.{R}\n")
        sys.exit(1)
    except Exception as e:
        _flush(f"\n{RED}  error: {e}{R}\n")
        raise
