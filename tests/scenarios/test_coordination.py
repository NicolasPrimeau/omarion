"""
Multi-agent coordination scenarios.

Each test describes a realistic workflow involving 2+ agents. The goal is to
verify that the primitives compose correctly across realistic usage patterns —
not to re-test individual endpoints (those live in tests/test_*.py).
"""

import asyncio

# ── Task workflows ────────────────────────────────────────────────────────────


async def test_task_delegation(scenario):
    """Planner creates a task; worker finds, claims, and completes it; planner verifies."""
    planner = await scenario.agent("planner")
    worker = await scenario.agent("worker")

    task = await planner.create_task(
        "Analyse error logs from last night",
        description="Focus on 5xx spikes after 02:00 UTC",
        priority="high",
    )
    assert task["status"] == "open"

    open_tasks = await worker.list_tasks(status="open")
    assert any(t["id"] == task["id"] for t in open_tasks)

    claimed = await worker.claim_task(task["id"])
    assert claimed["status"] == "claimed"
    assert claimed["assigned_to"] == "worker"

    await worker.update_task(
        task["id"], description="Found 3 timeout spikes, tracing root cause", append=True
    )

    done = await worker.complete_task(task["id"])
    assert done["status"] == "completed"

    final = await planner.get_task(task["id"])
    assert final["status"] == "completed"
    assert "timeout spikes" in final["description"]


async def test_task_failure_and_retry(scenario):
    """Worker fails a task; a second worker picks up a retry and succeeds."""
    dispatcher = await scenario.agent("dispatcher")
    worker_a = await scenario.agent("worker-a")
    worker_b = await scenario.agent("worker-b")

    task = await dispatcher.create_task("Deploy model to staging")
    await worker_a.claim_task(task["id"])
    await worker_a.fail_task(task["id"])

    assert (await dispatcher.get_task(task["id"]))["status"] == "failed"

    retry = await dispatcher.create_task(
        "Deploy model to staging (retry)",
        description=f"Retry of {task['id']} which failed",
    )
    await worker_b.claim_task(retry["id"])
    done = await worker_b.complete_task(retry["id"])
    assert done["status"] == "completed"


async def test_concurrent_claim_race(scenario):
    """Only one agent can claim a task — the second gets 409."""
    creator = await scenario.agent("creator")
    racer_a = await scenario.agent("racer-a")
    racer_b = await scenario.agent("racer-b")

    task = await creator.create_task("Race me")
    await racer_a.claim_task(task["id"])

    r = await racer_b._http.post(f"/tasks/{task['id']}/claim")
    assert r.status_code == 409


async def test_task_priority_ordering(scenario):
    """Tasks appear in the list with the correct priorities."""
    agent = await scenario.agent("prioritizer")

    low = await agent.create_task("Low priority job", priority="low")
    high = await agent.create_task("High priority job", priority="high")
    normal = await agent.create_task("Normal priority job", priority="normal")

    tasks = await agent.list_tasks()
    ids = [t["id"] for t in tasks]
    assert low["id"] in ids
    assert high["id"] in ids
    assert normal["id"] in ids

    t = await agent.get_task(high["id"])
    assert t["priority"] == "high"


async def test_task_progress_log(scenario):
    """Multiple append updates build a running log on the task description."""
    lead = await scenario.agent("lead")
    worker = await scenario.agent("worker")

    task = await lead.create_task("Long running migration")
    await worker.claim_task(task["id"])

    await worker.update_task(task["id"], description="Step 1: schema backup complete", append=True)
    await worker.update_task(task["id"], description="Step 2: migrating 2M rows", append=True)
    await worker.update_task(task["id"], description="Step 3: validation passed", append=True)
    await worker.complete_task(task["id"])

    final = await lead.get_task(task["id"])
    assert "Step 1" in final["description"]
    assert "Step 2" in final["description"]
    assert "Step 3" in final["description"]


async def test_many_tasks_parallel_workers(scenario):
    """Multiple workers drain a queue of tasks concurrently."""
    dispatcher = await scenario.agent("dispatcher")
    workers = [await scenario.agent(f"worker-{i}") for i in range(4)]

    task_ids = []
    for i in range(8):
        t = await dispatcher.create_task(f"Job {i}")
        task_ids.append(t["id"])

    # each worker claims and completes two tasks
    for i, worker in enumerate(workers):
        await worker.claim_task(task_ids[i * 2])
        await worker.claim_task(task_ids[i * 2 + 1])

    for i, worker in enumerate(workers):
        await worker.complete_task(task_ids[i * 2])
        await worker.complete_task(task_ids[i * 2 + 1])

    completed = await dispatcher.list_tasks(status="completed")
    assert len(completed) == 8


# ── Memory workflows ──────────────────────────────────────────────────────────


async def test_memory_coordination(scenario):
    """Researcher writes findings; engineer searches and finds them."""
    researcher = await scenario.agent("researcher")
    engineer = await scenario.agent("engineer")

    mem = await researcher.write_memory(
        "The rate limiter uses a token bucket with a 60-second refill window. "
        "Burst capacity is 10x the base rate.",
        tags=["rate-limiter", "architecture"],
    )

    results = await engineer.search_memory("how does rate limiting work")
    assert any(r["id"] == mem["id"] for r in results)


async def test_memory_ownership_and_update(scenario):
    """Only the author can update or delete their memory."""
    author = await scenario.agent("author")
    reader = await scenario.agent("reader")

    mem = await author.write_memory("Initial finding about the cache layer")

    found = await reader.get_memory(mem["id"])
    assert found["content"] == mem["content"]

    updated = await author.update_memory(mem["id"], content="Updated: cache uses LRU with 5min TTL")
    assert updated["version"] == 2

    r = await reader._http.patch(f"/memory/{mem['id']}", json={"content": "hacked"})
    assert r.status_code == 403

    await author.delete_memory(mem["id"])
    assert (await reader._http.get(f"/memory/{mem['id']}")).status_code == 404


async def test_memory_confidence_decay_simulation(scenario):
    """Agent writes memory at low confidence; another raises it after verification."""
    guesser = await scenario.agent("guesser")
    verifier = await scenario.agent("verifier")

    mem = await guesser.write_memory(
        "The deploy pipeline probably runs every 6 hours",
        confidence=0.4,
        tags=["deploy", "schedule"],
    )
    assert mem["confidence"] == 0.4

    # verifier confirms and updates
    confirmed = await guesser.update_memory(mem["id"], confidence=1.0)
    assert confirmed["confidence"] == 1.0

    # verifier can read the updated entry
    fetched = await verifier.get_memory(mem["id"])
    assert fetched["confidence"] == 1.0


async def test_memory_tag_filtering(scenario):
    """memory_list with tag filter returns only matching entries."""
    agent = await scenario.agent("tagger")

    infra = await agent.write_memory("EC2 instance type is m5.xlarge", tags=["infra", "aws"])
    code = await agent.write_memory("Auth uses JWT HS256", tags=["auth", "security"])
    await agent.write_memory("Deploy happens at 02:00 UTC", tags=["deploy"])

    infra_results = await agent.list_memory(tag="infra")
    ids = [m["id"] for m in infra_results]
    assert infra["id"] in ids
    assert code["id"] not in ids


async def test_memory_agent_filter(scenario):
    """memory_list with agent filter returns only that agent's entries."""
    alice = await scenario.agent("alice")
    bob = await scenario.agent("bob")

    alice_mem = await alice.write_memory("Alice's finding")
    bob_mem = await bob.write_memory("Bob's finding")

    alice_list = await alice.list_memory(agent="alice")
    alice_ids = [m["id"] for m in alice_list]
    assert alice_mem["id"] in alice_ids
    assert bob_mem["id"] not in alice_ids


async def test_memory_confidence_filter(scenario):
    """memory_list with confidence_min excludes low-confidence entries."""
    agent = await scenario.agent("agent")

    high = await agent.write_memory("Confirmed fact", confidence=0.9)
    low = await agent.write_memory("Guessed fact", confidence=0.3)

    results = await agent.list_memory(confidence_min=0.7)
    ids = [m["id"] for m in results]
    assert high["id"] in ids
    assert low["id"] not in ids


async def test_memory_scope_agent_private(scenario):
    """scope=agent memory is invisible to other agents."""
    owner = await scenario.agent("owner")
    spy = await scenario.agent("spy")

    private = await owner.write_memory("My private API key notes", scope="agent")

    r = await spy._http.get(f"/memory/{private['id']}")
    assert r.status_code == 403

    spy_list = await spy.list_memory()
    assert not any(m["id"] == private["id"] for m in spy_list)

    spy_search = await spy.search_memory("private API key")
    assert not any(m["id"] == private["id"] for m in spy_search)


async def test_memory_version_increments(scenario):
    """Each patch increments the version counter."""
    agent = await scenario.agent("versioner")

    mem = await agent.write_memory("Version 1 content")
    assert mem["version"] == 1

    v2 = await agent.update_memory(mem["id"], content="Version 2 content")
    assert v2["version"] == 2

    v3 = await agent.update_memory(mem["id"], content="Version 3 content")
    assert v3["version"] == 3


async def test_memory_delta(scenario):
    """memory_delta returns entries written after a given timestamp."""
    agent_a = await scenario.agent("delta-a")
    agent_b = await scenario.agent("delta-b")

    old = await agent_a.write_memory("Written before the cutoff")
    await asyncio.sleep(0.01)
    cutoff = old["updated_at"]
    await asyncio.sleep(0.01)

    new = await agent_b.write_memory("Written after the cutoff")

    r = await agent_a._http.get("/memory/delta", params={"since": cutoff})
    delta_ids = [e["id"] for e in r.json()]
    assert new["id"] in delta_ids
    assert old["id"] not in delta_ids


# ── Messaging workflows ───────────────────────────────────────────────────────


async def test_messaging_workflow(scenario):
    """Agent A delegates via message; B reads inbox and acts."""
    orchestrator = await scenario.agent("orchestrator")
    analyst = await scenario.agent("analyst")

    await orchestrator.send_message(
        to="analyst",
        subject="new assignment",
        body="Please analyse Q1 revenue data and write findings to shared memory.",
    )

    messages = await analyst.inbox()
    assert len(messages) == 1
    assert messages[0]["from_agent"] == "orchestrator"
    assert messages[0]["subject"] == "new assignment"

    mem = await analyst.write_memory("Q1 revenue: $2.4M, up 18% YoY.", tags=["revenue", "q1"])

    await analyst.send_message(
        to="orchestrator",
        subject="re: new assignment",
        body=f"Done. Written to memory [{mem['id']}].",
    )

    reply = await orchestrator.inbox()
    assert len(reply) == 1
    assert mem["id"] in reply[0]["body"]


async def test_broadcast_coordination(scenario):
    """One agent broadcasts; all others receive it."""
    coordinator = await scenario.agent("coordinator")
    agent_a = await scenario.agent("agent-a")
    agent_b = await scenario.agent("agent-b")

    await coordinator.send_message(
        to="broadcast", body="Switching to maintenance mode at 03:00 UTC"
    )

    assert any("maintenance mode" in m["body"] for m in await agent_a.inbox())
    assert any("maintenance mode" in m["body"] for m in await agent_b.inbox())


async def test_inbox_cleared_after_read(scenario):
    """Inbox is empty after mark-all-read."""
    sender = await scenario.agent("sender")
    receiver = await scenario.agent("receiver")

    await sender.send_message(to="receiver", body="msg one")
    await sender.send_message(to="receiver", body="msg two")
    assert len(await receiver.inbox()) == 2

    await receiver.mark_inbox_read()
    assert len(await receiver.inbox()) == 0


async def test_high_volume_messaging(scenario):
    """Many messages between agents — all delivered, inbox clears cleanly."""
    sender = await scenario.agent("sender")
    receiver = await scenario.agent("receiver")

    for i in range(20):
        await sender.send_message(to="receiver", body=f"message {i}")

    inbox = await receiver.inbox()
    assert len(inbox) == 20

    await receiver.mark_inbox_read()
    assert len(await receiver.inbox()) == 0


# ── Session continuity ────────────────────────────────────────────────────────


async def test_session_continuity(scenario):
    """
    Agent saves a handoff at session end. A colleague writes memory while it's
    gone. Session 2 loads context and sees what changed since the handoff.
    """
    agent = await scenario.agent("persistent-agent")
    colleague = await scenario.agent("colleague")

    await agent.save_handoff(
        summary="Investigated auth token expiry bug. Found mismatch between docs and code.",
        next_steps=["Fix the docs", "Add a test for token TTL"],
    )
    await asyncio.sleep(0.01)

    new_mem = await colleague.write_memory("Auth TTL is controlled by JWT_TTL env var, default 30d")

    ctx = await agent.load_handoff()

    assert (
        ctx["last_handoff"]["summary"]
        == "Investigated auth token expiry bug. Found mismatch between docs and code."
    )
    assert "Fix the docs" in ctx["last_handoff"]["next_steps"]
    assert any(e["id"] == new_mem["id"] for e in ctx["memory_delta"])


async def test_session_no_prior_handoff(scenario):
    """First session load returns no handoff and empty delta."""
    fresh = await scenario.agent("fresh-agent")
    ctx = await fresh.load_handoff()

    assert ctx["last_handoff"] is None
    assert ctx["memory_delta"] == []


async def test_session_handoff_overwrites(scenario):
    """Each handoff replaces the last — load_handoff returns the most recent."""
    agent = await scenario.agent("overwriter")

    await agent.save_handoff(summary="First session")
    await asyncio.sleep(0.01)
    await agent.save_handoff(summary="Second session", next_steps=["do the thing"])

    ctx = await agent.load_handoff()
    assert ctx["last_handoff"]["summary"] == "Second session"
    assert "do the thing" in ctx["last_handoff"]["next_steps"]


# ── Project scoping ───────────────────────────────────────────────────────────


async def test_project_scoping(scenario):
    """Project-scoped memory is visible to members but not outsiders."""
    alpha = await scenario.agent("alpha")
    beta = await scenario.agent("beta")
    outsider = await scenario.agent("outsider")

    await alpha.join_project("project-x")
    await beta.join_project("project-x")

    secret = await alpha.write_memory(
        "Project X uses a proprietary compression algorithm",
        scope="project",
        project="project-x",
    )

    results = await beta.search_memory("compression algorithm")
    assert any(r["id"] == secret["id"] for r in results)

    outsider_results = await outsider.search_memory("compression algorithm")
    assert not any(r["id"] == secret["id"] for r in outsider_results)


async def test_project_memory_list_filter(scenario):
    """memory_list with project filter returns only that project's entries."""
    agent_a = await scenario.agent("agent-a")
    agent_b = await scenario.agent("agent-b")

    await agent_a.join_project("team-red")
    await agent_b.join_project("team-blue")

    red_mem = await agent_a.write_memory("Red team discovery", project="team-red")
    blue_mem = await agent_b.write_memory("Blue team discovery", project="team-blue")

    red_list = await agent_a.list_memory(project="team-red")
    ids = [m["id"] for m in red_list]
    assert red_mem["id"] in ids
    assert blue_mem["id"] not in ids


async def test_project_task_scoping(scenario):
    """Tasks scoped to a project only appear in that project's list."""
    member = await scenario.agent("member")
    outsider = await scenario.agent("outsider")

    await member.join_project("skunkworks")

    task = await member.create_task("Secret prototype", project="skunkworks")

    member_tasks = await member.list_tasks(project="skunkworks")
    assert any(t["id"] == task["id"] for t in member_tasks)

    outsider_tasks = await outsider.list_tasks(project="skunkworks")
    assert not any(t["id"] == task["id"] for t in outsider_tasks)


async def test_project_join_leave(scenario):
    """Joining then leaving a project restores the outsider view."""
    agent = await scenario.agent("joiner")
    writer = await scenario.agent("writer")

    await agent.join_project("transient")
    await writer.join_project("transient")

    scoped = await writer.write_memory("Project secret", project="transient")

    results_in = await agent.search_memory("Project secret")
    assert any(r["id"] == scoped["id"] for r in results_in)

    await agent.leave_project("transient")

    results_out = await agent.search_memory("Project secret")
    assert not any(r["id"] == scoped["id"] for r in results_out)


async def test_multiple_projects(scenario):
    """Agent can be a member of multiple projects simultaneously."""
    agent = await scenario.agent("multimember")
    w1 = await scenario.agent("writer1")
    w2 = await scenario.agent("writer2")

    await agent.join_project("alpha")
    await agent.join_project("beta")
    await w1.join_project("alpha")
    await w2.join_project("beta")

    mem_alpha = await w1.write_memory("Alpha secret", project="alpha")
    mem_beta = await w2.write_memory("Beta secret", project="beta")

    all_mem = await agent.list_memory()
    ids = [m["id"] for m in all_mem]
    assert mem_alpha["id"] in ids
    assert mem_beta["id"] in ids


# ── Events ────────────────────────────────────────────────────────────────────


async def test_event_emission_and_polling(scenario):
    """Agent emits a custom event; another agent polls and sees it."""
    emitter = await scenario.agent("emitter")
    watcher = await scenario.agent("watcher")

    since = "1970-01-01T00:00:00.000Z"
    await emitter.emit_event("pipeline.complete", {"stage": "build", "status": "ok"})

    events = await watcher.poll_events(since, type="pipeline.complete")
    assert len(events) == 1
    assert events[0]["payload"]["stage"] == "build"
    assert events[0]["agent_id"] == "emitter"


async def test_event_type_filtering(scenario):
    """Polling with a type filter returns only matching events."""
    agent = await scenario.agent("multi-emitter")
    since = "1970-01-01T00:00:00.000Z"

    await agent.emit_event("deploy.started", {"env": "staging"})
    await agent.emit_event("deploy.complete", {"env": "staging"})
    await agent.emit_event("test.failed", {"suite": "integration"})

    deploy_events = await agent.poll_events(since, type="deploy.complete")
    assert all(e["type"] == "deploy.complete" for e in deploy_events)
    assert len(deploy_events) == 1


async def test_memory_written_event_emitted(scenario):
    """Writing a memory emits a memory.written event visible to all agents."""
    writer = await scenario.agent("writer")
    watcher = await scenario.agent("watcher")

    since = "1970-01-01T00:00:00.000Z"
    mem = await writer.write_memory("Something important")

    events = await watcher.poll_events(since, type="memory.written")
    payloads = [e["payload"] for e in events]
    assert any(p.get("memory_id") == mem["id"] for p in payloads)


async def test_task_events_emitted(scenario):
    """Task lifecycle transitions emit corresponding events."""
    creator = await scenario.agent("creator")
    worker = await scenario.agent("worker")
    since = "1970-01-01T00:00:00.000Z"

    task = await creator.create_task("Event-tracked job")
    await worker.claim_task(task["id"])
    await worker.complete_task(task["id"])

    events = await creator.poll_events(since)
    types = [e["type"] for e in events]
    assert "task.created" in types
    assert "task.claimed" in types
    assert "task.completed" in types


# ── Scaling / fleet patterns ──────────────────────────────────────────────────


async def test_large_fleet_participants(scenario):
    """Many agents register and are all visible in the participants list."""
    agents = [await scenario.agent(f"bot-{i}") for i in range(10)]
    reporter = await scenario.agent("reporter")

    participants = await reporter.participants()
    registered_ids = {p["agent_id"] for p in participants}

    for agent in agents:
        assert agent.id in registered_ids


async def test_memory_at_scale(scenario):
    """Many agents writing many memories — search still returns results."""
    agents = [await scenario.agent(f"scribe-{i}") for i in range(5)]

    for i, agent in enumerate(agents):
        for j in range(5):
            await agent.write_memory(
                f"Finding {j} from agent {i}: database connection pool size should be 10",
                tags=[f"agent-{i}", "database"],
            )

    reader = await scenario.agent("reader")
    results = await reader.search_memory("database connection pool")
    assert len(results) > 0


async def test_task_queue_drain(scenario):
    """10 tasks created by one agent, drained by 5 workers."""
    manager = await scenario.agent("manager")
    workers = [await scenario.agent(f"drone-{i}") for i in range(5)]

    task_ids = []
    for i in range(10):
        t = await manager.create_task(f"Unit of work {i}")
        task_ids.append(t["id"])

    for i, worker in enumerate(workers):
        await worker.claim_task(task_ids[i * 2])
        await worker.claim_task(task_ids[i * 2 + 1])
        await worker.complete_task(task_ids[i * 2])
        await worker.complete_task(task_ids[i * 2 + 1])

    completed = await manager.list_tasks(status="completed")
    assert len(completed) == 10
    open_remaining = await manager.list_tasks(status="open")
    assert len(open_remaining) == 0


# ── Full end-to-end workflows ─────────────────────────────────────────────────


async def test_full_research_workflow(scenario):
    """Planner assigns research; researcher executes, writes findings, notifies planner."""
    planner = await scenario.agent("planner")
    researcher = await scenario.agent("researcher")

    task = await planner.create_task(
        "Research vector DB options for long-term memory",
        description="Compare pgvector, Qdrant, and sqlite-vec on latency and ops burden",
        expected_outcome="A memory entry with a clear recommendation",
        priority="normal",
    )

    open_tasks = await researcher.list_tasks(status="open")
    assert any(t["id"] == task["id"] for t in open_tasks)
    await researcher.claim_task(task["id"])

    finding = await researcher.write_memory(
        "Recommendation: sqlite-vec for self-hosted setups under 10M vectors. "
        "Zero ops burden, WAL mode handles concurrent reads.",
        tags=["vector-db", "recommendation"],
    )

    await researcher.update_task(
        task["id"], description=f"Recommendation at [{finding['id']}]", append=True
    )
    await researcher.complete_task(task["id"])

    await researcher.send_message(
        to="planner",
        subject=f"re: {task['title']}",
        body=f"Done. See memory [{finding['id']}].",
    )

    await researcher.save_handoff(
        summary="Completed vector DB research. Recommended sqlite-vec.",
        next_steps=["Present findings at standup"],
    )

    inbox = await planner.inbox()
    assert finding["id"] in inbox[0]["body"]
    assert (await planner.get_task(task["id"]))["status"] == "completed"
    assert "sqlite-vec" in (await planner.get_memory(finding["id"]))["content"]


async def test_incident_response_workflow(scenario):
    """
    On-call detects incident, creates task, broadcasts alert, assigns workers,
    each worker logs findings, on-call marks resolved.
    """
    oncall = await scenario.agent("oncall")
    sre_a = await scenario.agent("sre-a")
    sre_b = await scenario.agent("sre-b")

    # incident detected
    incident = await oncall.create_task(
        "P0: API latency spike — p99 > 10s",
        description="Started 14:32 UTC. Affects /search endpoint.",
        priority="high",
    )

    await oncall.send_message(to="broadcast", body=f"P0 incident — join task [{incident['id']}]")
    await oncall.emit_event("incident.declared", {"task_id": incident["id"], "severity": "p0"})

    assert any(incident["id"] in m["body"] for m in await sre_a.inbox())
    assert any(incident["id"] in m["body"] for m in await sre_b.inbox())

    # sre-a investigates DB
    await sre_a.claim_task(incident["id"])
    db_finding = await sre_a.write_memory(
        "DB query plan changed after index was dropped in migration 042. Full table scan on memory table.",
        tags=["incident", "database", "root-cause"],
    )
    await sre_a.update_task(
        incident["id"], description=f"Root cause: {db_finding['id']}", append=True
    )

    # sre-b checks infra
    await sre_b.write_memory(
        "No infra anomaly. CPU and memory normal. Issue is query performance.",
        tags=["incident", "infra"],
    )

    await sre_a.complete_task(incident["id"])
    await oncall.emit_event("incident.resolved", {"task_id": incident["id"]})

    events = await oncall.poll_events("1970-01-01T00:00:00.000Z", type="incident.resolved")
    assert len(events) == 1

    final = await oncall.get_task(incident["id"])
    assert final["status"] == "completed"
    assert db_finding["id"] in final["description"]

    # post-mortem memory is searchable by future agents
    results = await oncall.search_memory("index dropped migration latency")
    assert any(r["id"] == db_finding["id"] for r in results)


async def test_knowledge_handoff_chain(scenario):
    """
    Three agents pass knowledge through session handoffs —
    each picks up where the last left off via memory delta.
    """
    agent_a = await scenario.agent("shift-a")
    agent_b = await scenario.agent("shift-b")
    agent_c = await scenario.agent("shift-c")

    # shift A does work and hands off
    mem_a = await agent_a.write_memory("Shift A: identified the bottleneck in the auth service")
    await agent_a.save_handoff(
        summary="Shift A complete. Found auth bottleneck.",
        next_steps=["Profile auth service", "Check token cache hit rate"],
    )
    await asyncio.sleep(0.01)

    # shift B picks up, does more work
    ctx_b = await agent_b.load_handoff()
    assert ctx_b["last_handoff"] is None  # B has its own fresh context

    # B loads A's context explicitly (real agents would call their own handoff)
    ctx_a = await agent_a.load_handoff()
    assert ctx_a["last_handoff"]["summary"] == "Shift A complete. Found auth bottleneck."

    mem_b = await agent_b.write_memory("Shift B: token cache hit rate is 12%, should be >80%")
    await agent_b.save_handoff(
        summary="Shift B complete. Token cache misconfigured.",
        next_steps=["Fix cache TTL", "Redeploy auth service"],
    )
    await asyncio.sleep(0.01)

    # shift C picks up
    mem_c = await agent_c.write_memory(
        "Shift C: fixed cache TTL from 60s to 3600s. Hit rate now 94%."
    )

    ctx_b_for_c = await agent_b.load_handoff()
    assert "Token cache misconfigured" in ctx_b_for_c["last_handoff"]["summary"]
    assert any(e["id"] == mem_c["id"] for e in ctx_b_for_c["memory_delta"])

    # all findings are searchable
    results = await agent_c.search_memory("auth service cache token")
    finding_ids = [r["id"] for r in results]
    assert mem_a["id"] in finding_ids or mem_b["id"] in finding_ids


# ── Concurrency ───────────────────────────────────────────────────────────────


async def test_concurrent_workers_race(scenario):
    """
    8 workers race to claim the same task simultaneously via asyncio.gather.
    Exactly one wins; the other seven get 409.
    """
    dispatcher = await scenario.agent("dispatcher")
    workers = [await scenario.agent(f"racer-{i}") for i in range(8)]

    task = await dispatcher.create_task("Contested task — only one gets it")

    responses = await asyncio.gather(
        *[w._http.post(f"/tasks/{task['id']}/claim") for w in workers],
        return_exceptions=True,
    )

    statuses = [r.status_code for r in responses if not isinstance(r, Exception)]
    assert statuses.count(200) == 1
    assert statuses.count(409) == 7

    final = await dispatcher.get_task(task["id"])
    assert final["status"] == "claimed"
    assert final["assigned_to"] is not None


async def test_concurrent_memory_flood(scenario):
    """
    20 agents each write 2 memories in parallel via asyncio.gather.
    All 40 entries land; search returns results from the flood.
    """
    agents = [await scenario.agent(f"flood-{i}") for i in range(20)]

    writes = [
        agents[i].write_memory(
            f"Flood entry {j} from agent {i}: connection pool max is 20 for high-traffic services",
            tags=[f"flood-{i}", "perf"],
        )
        for i in range(20)
        for j in range(2)
    ]
    results = await asyncio.gather(*writes)
    assert len(results) == 40

    reader = await scenario.agent("reader")
    hits = await reader.search_memory("connection pool high-traffic")
    assert len(hits) > 0


async def test_concurrent_task_queue_drain(scenario):
    """
    5 workers drain a 20-task queue by claiming tasks as they become available.
    All tasks complete; no double-claims.
    """
    manager = await scenario.agent("manager")
    workers = [await scenario.agent(f"worker-{i}") for i in range(5)]

    task_ids = []
    for i in range(20):
        t = await manager.create_task(f"Work item {i:02d}", priority="normal")
        task_ids.append(t["id"])

    async def drain(worker):
        claimed = []
        for tid in task_ids:
            r = await worker._http.post(f"/tasks/{tid}/claim")
            if r.status_code == 200:
                claimed.append(tid)
        for tid in claimed:
            await worker.complete_task(tid)
        return claimed

    claim_sets = await asyncio.gather(*[drain(w) for w in workers])

    all_claimed = [tid for s in claim_sets for tid in s]
    assert len(all_claimed) == len(set(all_claimed)), "double-claim detected"
    assert len(all_claimed) == 20

    open_remaining = await manager.list_tasks(status="open")
    assert len(open_remaining) == 0


# ── Event-driven patterns ─────────────────────────────────────────────────────


async def test_event_driven_pipeline(scenario):
    """
    Full event-triggered pipeline:
      producer emits data.ready
      → consumer polls, creates processing task
      → executor claims, processes, writes result memory, completes
      → executor emits pipeline.complete
      → observer sees the full event chain
    """
    producer = await scenario.agent("producer")
    consumer = await scenario.agent("consumer")
    executor = await scenario.agent("executor")
    observer = await scenario.agent("observer")

    since = "1970-01-01T00:00:00.000Z"

    await producer.emit_event("data.ready", {"dataset": "q2-revenue", "rows": 45_000})

    events = await consumer.poll_events(since, type="data.ready")
    assert len(events) == 1
    dataset = events[0]["payload"]["dataset"]
    rows = events[0]["payload"]["rows"]

    task = await consumer.create_task(
        f"Process {dataset}",
        description=f"Triggered by data.ready. {rows} rows to process.",
        priority="high",
    )
    await consumer.emit_event("task.queued", {"task_id": task["id"], "dataset": dataset})

    open_tasks = await executor.list_tasks(status="open")
    assert any(t["id"] == task["id"] for t in open_tasks)
    await executor.claim_task(task["id"])

    result_mem = await executor.write_memory(
        f"Processed {dataset}: total $3.1M, 12% above forecast. No anomalies.",
        tags=["q2-revenue", "results"],
    )
    await executor.update_task(
        task["id"], description=f"Result stored at [{result_mem['id']}]", append=True
    )
    await executor.complete_task(task["id"])
    await executor.emit_event(
        "pipeline.complete", {"task_id": task["id"], "result_id": result_mem["id"]}
    )

    all_events = await observer.poll_events(since)
    event_types = {e["type"] for e in all_events}
    assert "data.ready" in event_types
    assert "task.queued" in event_types
    assert "pipeline.complete" in event_types

    final = await producer.get_task(task["id"])
    assert final["status"] == "completed"
    assert result_mem["id"] in final["description"]


async def test_event_since_precision(scenario):
    """
    Events emitted before a cutoff timestamp are excluded from polls using since=.
    """
    agent = await scenario.agent("timer")

    for i in range(5):
        await agent.emit_event("pre.event", {"i": i})

    await asyncio.sleep(0.01)
    cutoff_events = await agent.poll_events("1970-01-01T00:00:00.000Z", type="pre.event")
    assert len(cutoff_events) == 5
    cutoff = cutoff_events[-1]["created_at"]
    await asyncio.sleep(0.01)

    for i in range(5):
        await agent.emit_event("post.event", {"i": i})

    post = await agent.poll_events(cutoff, type="post.event")
    assert len(post) == 5

    pre_check = await agent.poll_events(cutoff, type="pre.event")
    assert len(pre_check) == 0


# ── Advanced messaging ────────────────────────────────────────────────────────


async def test_multi_round_conversation(scenario):
    """
    Two agents exchange 6 messages in a ping-pong thread.
    Each round the recipient's inbox grows; after clearing it empties.
    """
    ping = await scenario.agent("ping")
    pong = await scenario.agent("pong")

    for round_num in range(6):
        sender, receiver = (ping, pong) if round_num % 2 == 0 else (pong, ping)
        await sender.send_message(
            to=receiver.id,
            subject=f"round {round_num}",
            body=f"Message from {sender.id} — round {round_num}",
        )

    # each agent has 3 messages (rounds 0,2,4 → pong; rounds 1,3,5 → ping)
    assert len(await pong.inbox()) == 3
    assert len(await ping.inbox()) == 3

    await ping.mark_inbox_read()
    await pong.mark_inbox_read()
    assert len(await ping.inbox()) == 0
    assert len(await pong.inbox()) == 0


async def test_mark_single_message_read(scenario):
    """
    Marking one message read leaves the rest unread in the inbox.
    """
    sender = await scenario.agent("sender")
    receiver = await scenario.agent("receiver")

    await sender.send_message(to="receiver", body="first")
    mid = (await sender.send_message(to="receiver", body="second"))["id"]
    await sender.send_message(to="receiver", body="third")

    assert len(await receiver.inbox()) == 3
    await receiver.mark_message_read(mid)
    assert len(await receiver.inbox()) == 2


async def test_broadcast_then_targeted_reply(scenario):
    """
    Coordinator broadcasts to 5 agents; each replies directly.
    Coordinator ends with 5 direct replies in inbox.
    """
    coordinator = await scenario.agent("coordinator")
    team = [await scenario.agent(f"member-{i}") for i in range(5)]

    await coordinator.send_message(to="broadcast", body="Status update needed from all members")

    for member in team:
        inbox = await member.inbox()
        assert any("Status update needed" in m["body"] for m in inbox)
        await member.mark_inbox_read()
        await member.send_message(
            to="coordinator",
            subject="re: status",
            body=f"{member.id} reporting: all systems nominal",
        )

    replies = await coordinator.inbox()
    # filter own broadcast — coordinator also receives it
    direct_replies = [m for m in replies if m["from_agent"] != "coordinator"]
    assert len(direct_replies) == 5
    assert all("nominal" in m["body"] for m in direct_replies)


# ── Advanced memory patterns ──────────────────────────────────────────────────


async def test_memory_type_progression(scenario):
    """
    An entry can be promoted from memory to doc via a PATCH.
    """
    agent = await scenario.agent("promoter")

    entry = await agent.write_memory(
        "Definitive architecture overview: monolith with plugin API surface.",
        type="memory",
    )
    assert entry["type"] == "memory"

    promoted = await agent.update_memory(entry["id"], type="doc")
    assert promoted["type"] == "doc"

    fetched = await agent.get_memory(entry["id"])
    assert fetched["type"] == "doc"


async def test_deleted_memory_invisible(scenario):
    """
    A deleted memory entry is absent from get, list, and search.
    """
    writer = await scenario.agent("writer")
    reader = await scenario.agent("reader")

    entry = await writer.write_memory("Temporary credential rotation schedule")

    r = await reader.get_memory(entry["id"])
    assert r["id"] == entry["id"]

    await writer.delete_memory(entry["id"])

    assert (await reader._http.get(f"/memory/{entry['id']}")).status_code == 404

    listed = await reader.list_memory()
    assert not any(m["id"] == entry["id"] for m in listed)

    searched = await reader.search_memory("credential rotation schedule")
    assert not any(m["id"] == entry["id"] for m in searched)


async def test_stale_fact_correction(scenario):
    """
    Agent writes an incorrect fact; corrects it. Searchers find the updated version.
    """
    agent = await scenario.agent("corrector")
    reader = await scenario.agent("reader")

    wrong = await agent.write_memory(
        "Deploy pipeline runs at 04:00 UTC",
        tags=["deploy", "schedule"],
        confidence=0.5,
    )

    corrected = await agent.update_memory(
        wrong["id"],
        content="Deploy pipeline runs at 02:00 UTC (confirmed from cron config)",
        confidence=1.0,
    )
    assert corrected["version"] == 2
    assert "02:00" in corrected["content"]
    assert corrected["confidence"] == 1.0

    results = await reader.search_memory("deploy pipeline schedule")
    match = next((r for r in results if r["id"] == wrong["id"]), None)
    assert match is not None
    assert "02:00" in match["content"]
    assert match["confidence"] == 1.0


async def test_cross_project_memory_search(scenario):
    """
    An agent in two projects can search across both.
    An agent in neither project sees none of the scoped entries.
    """
    bridge = await scenario.agent("bridge")
    writer_a = await scenario.agent("writer-a")
    writer_b = await scenario.agent("writer-b")
    outsider = await scenario.agent("outsider")

    await bridge.join_project("red")
    await bridge.join_project("blue")
    await writer_a.join_project("red")
    await writer_b.join_project("blue")

    red_mem = await writer_a.write_memory(
        "Red team: vulnerability in session token rotation",
        project="red",
        scope="project",
    )
    blue_mem = await writer_b.write_memory(
        "Blue team: defence playbook updated for token rotation attacks",
        project="blue",
        scope="project",
    )

    bridge_results = await bridge.search_memory("token rotation")
    bridge_ids = {r["id"] for r in bridge_results}
    assert red_mem["id"] in bridge_ids
    assert blue_mem["id"] in bridge_ids

    outsider_results = await outsider.search_memory("token rotation")
    outsider_ids = {r["id"] for r in outsider_results}
    assert red_mem["id"] not in outsider_ids
    assert blue_mem["id"] not in outsider_ids


async def test_partial_project_visibility(scenario):
    """
    Mix of project-scoped and global memory.
    Member sees both; non-member sees only global.
    """
    member = await scenario.agent("member")
    outsider = await scenario.agent("outsider")
    writer = await scenario.agent("writer")

    await member.join_project("gamma")
    await writer.join_project("gamma")

    global_mem = await writer.write_memory("Global fact: the DB is PostgreSQL 16")
    scoped_mem = await writer.write_memory(
        "Gamma secret: we use a homomorphic encryption prototype",
        project="gamma",
        scope="project",
    )

    member_list = await member.list_memory()
    member_ids = {m["id"] for m in member_list}
    assert global_mem["id"] in member_ids
    assert scoped_mem["id"] in member_ids

    outsider_list = await outsider.list_memory()
    outsider_ids = {m["id"] for m in outsider_list}
    assert global_mem["id"] in outsider_ids
    assert scoped_mem["id"] not in outsider_ids


# ── Advanced session patterns ─────────────────────────────────────────────────


async def test_session_delta_includes_project_memory(scenario):
    """
    After saving a handoff, project-scoped memory written by a teammate
    appears in the delta when the agent (a project member) reloads context.
    """
    agent = await scenario.agent("shift-worker")
    teammate = await scenario.agent("teammate")

    await agent.join_project("ops")
    await teammate.join_project("ops")

    await agent.save_handoff(summary="End of shift. DB migration paused at step 3.")
    await asyncio.sleep(0.01)

    team_mem = await teammate.write_memory(
        "Resumed migration at step 3. Completed successfully.",
        project="ops",
        scope="project",
    )

    ctx = await agent.load_handoff()
    delta_ids = {e["id"] for e in ctx["memory_delta"]}
    assert team_mem["id"] in delta_ids


async def test_session_context_excludes_private_memory(scenario):
    """
    scope=agent memory written by other agents does NOT appear in session delta.
    """
    agent = await scenario.agent("watcher")
    private_writer = await scenario.agent("private-writer")

    await agent.save_handoff(summary="Watching for changes.")
    await asyncio.sleep(0.01)

    private_mem = await private_writer.write_memory(
        "My internal reasoning log — not for sharing",
        scope="agent",
    )
    shared_mem = await private_writer.write_memory(
        "Public update: cache flushed successfully",
    )

    ctx = await agent.load_handoff()
    delta_ids = {e["id"] for e in ctx["memory_delta"]}
    assert shared_mem["id"] in delta_ids
    assert private_mem["id"] not in delta_ids


# ── Task state machine ────────────────────────────────────────────────────────


async def test_task_sequential_pipeline(scenario):
    """
    Three tasks form a sequential pipeline: each starts only after the previous
    completes. Workers use memory to pass results between stages.
    """
    orchestrator = await scenario.agent("orchestrator")
    stage1 = await scenario.agent("stage1")
    stage2 = await scenario.agent("stage2")
    stage3 = await scenario.agent("stage3")

    t1 = await orchestrator.create_task("Stage 1: ingest raw data")
    t2 = await orchestrator.create_task("Stage 2: transform and validate")
    t3 = await orchestrator.create_task("Stage 3: load to warehouse")

    await stage1.claim_task(t1["id"])
    result1 = await stage1.write_memory("Ingested 120K rows from S3. Schema: id,ts,amount.")
    await stage1.update_task(t1["id"], description=f"Output: [{result1['id']}]", append=True)
    await stage1.complete_task(t1["id"])
    await stage1.send_message(to="stage2", body=f"Stage 1 done. Input at [{result1['id']}].")

    # stage 2 waits for stage 1 signal
    inbox2 = await stage2.inbox()
    assert result1["id"] in inbox2[0]["body"]
    await stage2.claim_task(t2["id"])
    result2 = await stage2.write_memory(
        "Validated 119.8K rows. Dropped 200 malformed. Schema enforced."
    )
    await stage2.complete_task(t2["id"])
    await stage2.send_message(to="stage3", body=f"Stage 2 done. Input at [{result2['id']}].")

    # stage 3
    inbox3 = await stage3.inbox()
    assert result2["id"] in inbox3[0]["body"]
    await stage3.claim_task(t3["id"])
    result3 = await stage3.write_memory("Loaded 119.8K rows to warehouse. Table: events_2024_q2.")
    await stage3.update_task(t3["id"], description=f"Loaded: [{result3['id']}]", append=True)
    await stage3.complete_task(t3["id"])

    completed = await orchestrator.list_tasks(status="completed")
    assert len(completed) == 3

    # full lineage is searchable
    lineage = await orchestrator.search_memory("warehouse events 2024")
    assert any(r["id"] == result3["id"] for r in lineage)


async def test_task_retry_with_escalation(scenario):
    """
    Worker A fails a task twice; on the third attempt a senior agent
    picks it up, escalates priority, adds diagnosis notes, and completes.
    """
    manager = await scenario.agent("manager")
    junior_a = await scenario.agent("junior-a")
    junior_b = await scenario.agent("junior-b")
    senior = await scenario.agent("senior")

    task = await manager.create_task(
        "Diagnose memory leak in worker process",
        priority="normal",
    )

    await junior_a.claim_task(task["id"])
    await junior_a.fail_task(task["id"])

    retry1 = await manager.create_task(
        "Diagnose memory leak — retry 1",
        description=f"First attempt [{task['id']}] failed.",
        priority="high",
    )
    await junior_b.claim_task(retry1["id"])
    await junior_b.fail_task(retry1["id"])

    retry2 = await manager.create_task(
        "Diagnose memory leak — retry 2 (escalated)",
        description="Two prior failures. Senior required.",
        priority="high",
    )
    await manager.update_task(retry2["id"], priority="high")

    await senior.claim_task(retry2["id"])
    diagnosis = await senior.write_memory(
        "Root cause: unbounded cache in LRU wrapper. Heap grows 50MB/hr. Fix: cap at 10K entries.",
        tags=["memory-leak", "root-cause"],
        confidence=0.95,
    )
    await senior.update_task(
        retry2["id"], description=f"Diagnosis: [{diagnosis['id']}]", append=True
    )
    await senior.complete_task(retry2["id"])

    await manager.send_message(
        to="senior",
        body="Thanks for the escalation fix. Adding to post-mortem.",
    )

    final = await manager.get_task(retry2["id"])
    assert final["status"] == "completed"
    assert diagnosis["id"] in final["description"]

    results = await manager.search_memory("LRU cache memory leak")
    assert any(r["id"] == diagnosis["id"] for r in results)


# ── Full simulation ───────────────────────────────────────────────────────────


async def test_full_sprint_simulation(scenario):
    """
    Full software sprint:
      PM creates 4 tasks in project 'sprint-1', notifies team via broadcast.
      Two devs claim and complete tasks, writing memory findings.
      QA verifies by searching memory and marking tasks reviewed.
      PM gets completion events and sends closing message.
    """
    pm = await scenario.agent("pm")
    dev1 = await scenario.agent("dev1")
    dev2 = await scenario.agent("dev2")
    qa = await scenario.agent("qa")

    for ag in [pm, dev1, dev2, qa]:
        await ag.join_project("sprint-1")

    since = "1970-01-01T00:00:00.000Z"

    tasks = []
    for spec in [
        ("Implement rate limiter", "high"),
        ("Add JWT refresh endpoint", "high"),
        ("Write integration tests for auth", "normal"),
        ("Update API docs", "low"),
    ]:
        t = await pm.create_task(spec[0], project="sprint-1", priority=spec[1])
        tasks.append(t)

    await pm.send_message(
        to="broadcast",
        body=f"Sprint-1 kicked off. {len(tasks)} tasks open. Claim what you can.",
    )
    await pm.emit_event("sprint.started", {"project": "sprint-1", "task_count": len(tasks)})

    assert len(await dev1.inbox()) >= 1
    assert len(await dev2.inbox()) >= 1

    await dev1.claim_task(tasks[0]["id"])
    await dev1.claim_task(tasks[2]["id"])
    await dev2.claim_task(tasks[1]["id"])
    await dev2.claim_task(tasks[3]["id"])

    rl_finding = await dev1.write_memory(
        "Rate limiter implemented using token bucket. Burst: 20 req. Refill: 10 req/s.",
        tags=["rate-limiter", "sprint-1"],
        project="sprint-1",
    )
    await dev1.update_task(
        tasks[0]["id"], description=f"Done. See [{rl_finding['id']}]", append=True
    )
    await dev1.complete_task(tasks[0]["id"])

    await dev1.write_memory(
        "Auth integration tests: 47 cases, 100% pass. Coverage: 94%.",
        tags=["tests", "auth", "sprint-1"],
        project="sprint-1",
    )
    await dev1.complete_task(tasks[2]["id"])

    jwt_finding = await dev2.write_memory(
        "JWT refresh endpoint: POST /auth/refresh. Accepts refresh_token, returns new access+refresh pair.",
        tags=["jwt", "auth", "sprint-1"],
        project="sprint-1",
    )
    await dev2.complete_task(tasks[1]["id"])
    await dev2.complete_task(tasks[3]["id"])

    await dev1.emit_event("feature.shipped", {"task_id": tasks[0]["id"], "feature": "rate-limiter"})
    await dev2.emit_event("feature.shipped", {"task_id": tasks[1]["id"], "feature": "jwt-refresh"})

    qa_results = await qa.search_memory("rate limiter token bucket")
    assert any(r["id"] == rl_finding["id"] for r in qa_results)

    auth_results = await qa.search_memory("JWT refresh token auth")
    assert any(r["id"] == jwt_finding["id"] for r in auth_results)

    completed = await pm.list_tasks(status="completed", project="sprint-1")
    assert len(completed) == 4

    shipped_events = await pm.poll_events(since, type="feature.shipped")
    assert len(shipped_events) == 2

    await pm.send_message(
        to="broadcast",
        body="Sprint-1 complete. All 4 tasks shipped. Nice work team.",
    )
    await pm.emit_event("sprint.completed", {"project": "sprint-1"})

    final_events = await pm.poll_events(since, type="sprint.completed")
    assert len(final_events) == 1


async def test_multi_agent_knowledge_base_build(scenario):
    """
    5 specialist agents each contribute domain knowledge to shared memory over
    multiple rounds. A generalist agent synthesises by searching across domains
    and verifies cross-domain coverage.
    """
    specialists = {
        "infra": "Infrastructure uses Kubernetes 1.29 on bare metal. 3 control-plane nodes.",
        "security": "mTLS enforced between all services. Cert rotation every 90 days via cert-manager.",
        "data": "Primary DB is PostgreSQL 16 with read replicas. WAL archiving to S3.",
        "api": "REST API on FastAPI. Rate limited at 1000 req/min per API key. OpenAPI spec at /docs.",
        "ml": "Model inference runs on GPU nodes. Batch size 32. P99 latency 120ms.",
    }

    agents = {}
    memories = {}
    for domain, content in specialists.items():
        ag = await scenario.agent(domain)
        agents[domain] = ag
        mem = await ag.write_memory(content, tags=[domain, "architecture"])
        memories[domain] = mem

    for domain, ag in agents.items():
        for other_domain, other_content_snippet in [
            ("infra", "Kubernetes"),
            ("security", "mTLS"),
            ("data", "PostgreSQL"),
            ("api", "FastAPI"),
            ("ml", "GPU"),
        ]:
            if other_domain == domain:
                continue
            await ag.write_memory(
                f"{domain.upper()} ↔ {other_domain.upper()}: integration note — {other_content_snippet} integration verified.",
                tags=[domain, other_domain, "integration"],
            )

    generalist = await scenario.agent("generalist")

    for query, expected_domain in [
        ("Kubernetes control plane nodes", "infra"),
        ("mTLS certificate rotation", "security"),
        ("PostgreSQL read replicas WAL", "data"),
        ("FastAPI rate limiting API key", "api"),
        ("GPU inference latency batch", "ml"),
    ]:
        results = await generalist.search_memory(query, limit=30)
        assert len(results) > 0, f"no results for: {query}"
        result_ids = {r["id"] for r in results}
        assert memories[expected_domain]["id"] in result_ids, (
            f"expected {expected_domain} memory in results for '{query}'"
        )


# ── Negative paths / authorization ────────────────────────────────────────────


async def test_wrong_api_key_rejected(scenario):
    """Requests with an invalid API key are rejected with 401."""
    agent = await scenario.agent("legit")
    bad_client = agent._http.__class__(
        transport=agent._http._transport,
        base_url="http://test",
        headers={"x-agent-id": "legit", "x-api-key": "WRONGKEY"},
    )
    r = await bad_client.get("/memory")
    assert r.status_code == 401
    await bad_client.aclose()


async def test_complete_task_wrong_assignee_rejected(scenario):
    """An agent that did not claim a task cannot complete it — 403."""
    creator = await scenario.agent("creator")
    worker = await scenario.agent("worker")
    thief = await scenario.agent("thief")

    task = await creator.create_task("Owned task")
    await worker.claim_task(task["id"])

    r = await thief._http.post(f"/tasks/{task['id']}/complete")
    assert r.status_code == 403

    still_claimed = await creator.get_task(task["id"])
    assert still_claimed["status"] == "claimed"


async def test_fail_task_wrong_assignee_rejected(scenario):
    """An agent that did not claim a task cannot fail it — 403."""
    creator = await scenario.agent("creator")
    worker = await scenario.agent("worker")
    interloper = await scenario.agent("interloper")

    task = await creator.create_task("My task")
    await worker.claim_task(task["id"])

    r = await interloper._http.post(f"/tasks/{task['id']}/fail")
    assert r.status_code == 403


async def test_complete_unclaimed_task_rejected(scenario):
    """Completing a task that nobody claimed returns 409."""
    creator = await scenario.agent("creator")
    agent = await scenario.agent("agent")

    task = await creator.create_task("Unclaimed task")
    r = await agent._http.post(f"/tasks/{task['id']}/complete")
    assert r.status_code == 409


async def test_claim_completed_task_rejected(scenario):
    """Claiming an already-completed task returns 409."""
    creator = await scenario.agent("creator")
    worker = await scenario.agent("worker")
    late = await scenario.agent("late")

    task = await creator.create_task("Quick task")
    await worker.claim_task(task["id"])
    await worker.complete_task(task["id"])

    r = await late._http.post(f"/tasks/{task['id']}/claim")
    assert r.status_code == 409


async def test_memory_update_by_non_owner_rejected(scenario):
    """Only the author can patch their own memory — 403 for everyone else."""
    author = await scenario.agent("author")
    stranger = await scenario.agent("stranger")

    mem = await author.write_memory("Sensitive architectural decision")
    r = await stranger._http.patch(f"/memory/{mem['id']}", json={"content": "overwritten"})
    assert r.status_code == 403

    unchanged = await author.get_memory(mem["id"])
    assert unchanged["content"] == "Sensitive architectural decision"


async def test_memory_delete_by_non_owner_rejected(scenario):
    """Only the author can delete their own memory — 403 for everyone else."""
    author = await scenario.agent("author")
    stranger = await scenario.agent("stranger")

    mem = await author.write_memory("Important context")
    r = await stranger._http.delete(f"/memory/{mem['id']}")
    assert r.status_code == 403

    still_there = await author.get_memory(mem["id"])
    assert still_there["id"] == mem["id"]


async def test_private_memory_get_by_other_agent_rejected(scenario):
    """scope=agent memory returns 403 for any agent that is not the author."""
    owner = await scenario.agent("owner")
    spy = await scenario.agent("spy")

    private = await owner.write_memory("My private scratchpad notes", scope="agent")
    r = await spy._http.get(f"/memory/{private['id']}")
    assert r.status_code == 403


async def test_project_task_list_non_member_empty(scenario):
    """Non-members querying project tasks get an empty list, not an error."""
    member = await scenario.agent("member")
    outsider = await scenario.agent("outsider")

    await member.join_project("secret-proj")
    await member.create_task("Secret task", project="secret-proj")

    result = await outsider.list_tasks(project="secret-proj")
    assert result == []


async def test_task_not_found(scenario):
    """Fetching or claiming a non-existent task returns 404."""
    agent = await scenario.agent("agent")
    r = await agent._http.get("/tasks/does-not-exist")
    assert r.status_code == 404

    r2 = await agent._http.post("/tasks/does-not-exist/claim")
    assert r2.status_code == 404


async def test_memory_not_found(scenario):
    """Fetching a non-existent memory entry returns 404."""
    agent = await scenario.agent("agent")
    r = await agent._http.get("/memory/does-not-exist")
    assert r.status_code == 404


# ── Presence / participants ────────────────────────────────────────────────────


async def test_presence_updates_on_activity(scenario):
    """last_seen advances each time an agent makes a request."""
    agent = await scenario.agent("active")
    watcher = await scenario.agent("watcher")

    await asyncio.sleep(0.01)
    before = next(p["last_seen"] for p in await watcher.participants() if p["agent_id"] == "active")

    await asyncio.sleep(0.01)
    await agent.write_memory("touch to update last_seen")
    await asyncio.sleep(0.01)

    after = next(p["last_seen"] for p in await watcher.participants() if p["agent_id"] == "active")
    assert after > before


async def test_participants_includes_all_registered(scenario):
    """Every agent that has interacted appears in participants with agent_id and last_seen."""
    agents = [await scenario.agent(f"p-agent-{i}") for i in range(6)]
    for ag in agents:
        await ag.write_memory(f"I am {ag.id}")

    reporter = await scenario.agent("reporter")
    participants = {p["agent_id"]: p for p in await reporter.participants()}

    for ag in agents:
        assert ag.id in participants
        assert "last_seen" in participants[ag.id]
        assert participants[ag.id]["last_seen"] is not None


# ── Edge cases ────────────────────────────────────────────────────────────────


async def test_self_message(scenario):
    """An agent can send a message to itself and receive it in inbox."""
    agent = await scenario.agent("solo")
    await agent.send_message(to="solo", body="Reminder to self: check the deploy logs at 09:00")
    inbox = await agent.inbox()
    assert any("check the deploy logs" in m["body"] for m in inbox)


async def test_task_with_all_fields(scenario):
    """Tasks support all optional fields: priority, due_at, expected_outcome."""
    planner = await scenario.agent("planner")
    worker = await scenario.agent("worker")

    task = await planner.create_task(
        "Ship v2.0",
        description="Full release including auth rewrite and new API surface",
        expected_outcome="Zero-downtime deploy, all monitors green",
        priority="high",
        due_at="2026-06-01T00:00:00Z",
    )

    assert task["priority"] == "high"
    assert task["due_at"] == "2026-06-01T00:00:00Z"
    assert task["expected_outcome"] == "Zero-downtime deploy, all monitors green"

    fetched = await worker.get_task(task["id"])
    assert fetched["priority"] == "high"
    assert fetched["expected_outcome"] == task["expected_outcome"]


async def test_combined_task_filters(scenario):
    """List tasks filtered by both status and agent simultaneously."""
    manager = await scenario.agent("manager")
    worker_a = await scenario.agent("worker-a")
    worker_b = await scenario.agent("worker-b")

    t1 = await manager.create_task("Task for A")
    t2 = await manager.create_task("Task for B")
    t3 = await manager.create_task("Another task for A")

    await worker_a.claim_task(t1["id"])
    await worker_b.claim_task(t2["id"])
    await worker_a.claim_task(t3["id"])
    await worker_a.complete_task(t3["id"])

    claimed_by_a = await manager.list_tasks(status="claimed", agent="worker-a")
    ids = {t["id"] for t in claimed_by_a}
    assert t1["id"] in ids
    assert t2["id"] not in ids
    assert t3["id"] not in ids

    completed_by_a = await manager.list_tasks(status="completed", agent="worker-a")
    assert any(t["id"] == t3["id"] for t in completed_by_a)


async def test_memory_delta_with_type_filter(scenario):
    """memory_delta supports filtering by entry type."""
    agent = await scenario.agent("writer")

    old = await agent.write_memory("Old content", type="memory")
    await asyncio.sleep(0.01)
    cutoff = old["updated_at"]
    await asyncio.sleep(0.01)

    new_mem = await agent.write_memory("New memory entry", type="memory")
    new_doc = await agent.write_memory("New doc entry", type="doc")

    r = await agent._http.get("/memory/delta", params={"since": cutoff, "type": "doc"})
    delta = r.json()
    ids = {e["id"] for e in delta}
    assert new_doc["id"] in ids
    assert new_mem["id"] not in ids
    assert old["id"] not in ids


async def test_empty_task_queue_workers(scenario):
    """Workers polling an empty task queue get an empty list, not an error."""
    workers = [await scenario.agent(f"idle-{i}") for i in range(5)]
    results = await asyncio.gather(*[w.list_tasks(status="open") for w in workers])
    assert all(r == [] for r in results)


async def test_broadcast_independent_read_tracking(scenario):
    """
    Each agent tracks broadcast read status independently.
    Agent A marking a broadcast read does not hide it from Agent B.
    """
    sender = await scenario.agent("sender")
    reader_a = await scenario.agent("reader-a")
    reader_b = await scenario.agent("reader-b")

    await sender.send_message(to="broadcast", body="System-wide announcement")

    assert any("announcement" in m["body"] for m in await reader_a.inbox())
    assert any("announcement" in m["body"] for m in await reader_b.inbox())

    await reader_a.mark_inbox_read()

    assert not any("announcement" in m["body"] for m in await reader_a.inbox())
    assert any("announcement" in m["body"] for m in await reader_b.inbox())


async def test_multiple_handoffs_only_last_returned(scenario):
    """Saving 5 handoffs returns only the most recent one on load."""
    agent = await scenario.agent("serial-worker")

    for i in range(5):
        await agent.save_handoff(summary=f"Session {i} complete")
        await asyncio.sleep(0.01)

    ctx = await agent.load_handoff()
    assert ctx["last_handoff"]["summary"] == "Session 4 complete"


async def test_memory_search_limit_param(scenario):
    """search_memory respects an explicit limit parameter."""
    agent = await scenario.agent("writer")

    for i in range(15):
        await agent.write_memory(f"database connection pool finding number {i}")

    default_results = await agent.search_memory("database connection pool")
    assert len(default_results) <= 10

    large_results = await agent.search_memory("database connection pool", limit=15)
    assert len(large_results) >= len(default_results)


async def test_event_with_empty_payload(scenario):
    """Events can be emitted with an empty payload — no error."""
    agent = await scenario.agent("emitter")
    event = await agent.emit_event("heartbeat")
    assert event["type"] == "heartbeat"
    assert event["payload"] == {}

    since = "1970-01-01T00:00:00.000Z"
    events = await agent.poll_events(since, type="heartbeat")
    assert len(events) == 1


async def test_update_claimed_task_title(scenario):
    """A claimed task's title and priority can still be updated."""
    creator = await scenario.agent("creator")
    worker = await scenario.agent("worker")

    task = await creator.create_task("Initial title", priority="low")
    await worker.claim_task(task["id"])

    updated = await creator.update_task(task["id"], title="Revised title", priority="high")
    assert updated["title"] == "Revised title"
    assert updated["priority"] == "high"
    assert updated["status"] == "claimed"


async def test_cross_agent_task_visibility(scenario):
    """Any agent can see tasks created by another agent in the global list."""
    creator = await scenario.agent("creator")
    observer = await scenario.agent("observer")

    task = await creator.create_task("Globally visible task")

    all_tasks = await observer.list_tasks()
    assert any(t["id"] == task["id"] for t in all_tasks)


async def test_project_membership_required_for_project_memory_list(scenario):
    """
    Querying list_memory with project= returns empty for non-members
    and the scoped entries for members.
    """
    member = await scenario.agent("member")
    outsider = await scenario.agent("outsider")
    writer = await scenario.agent("writer")

    await member.join_project("vault")
    await writer.join_project("vault")

    secret = await writer.write_memory("Vault credential rotation procedure", project="vault")

    member_list = await member.list_memory(project="vault")
    assert any(m["id"] == secret["id"] for m in member_list)

    outsider_list = await outsider.list_memory(project="vault")
    assert not any(m["id"] == secret["id"] for m in outsider_list)


# ── Handoff richness ──────────────────────────────────────────────────────────


async def test_handoff_memory_refs(scenario):
    """
    Agent saves a handoff with specific memory_refs pointing to key findings.
    Next session loads the handoff and fetches each referenced memory directly.
    """
    agent = await scenario.agent("analyst")
    colleague = await scenario.agent("colleague")

    finding_a = await agent.write_memory(
        "Auth service is leaking tokens on logout. Reproduce: call POST /logout without refresh_token.",
        tags=["auth", "bug"],
    )
    finding_b = await agent.write_memory(
        "Token TTL is hardcoded to 30d in jwt_utils.py:42. Should be configurable via JWT_TTL env.",
        tags=["auth", "config"],
    )

    await agent.save_handoff(
        summary="Investigated auth token issues. Two findings.",
        memory_refs=[finding_a["id"], finding_b["id"]],
        next_steps=["Fix TTL config", "Add logout integration test"],
    )

    ctx = await agent.load_handoff()
    assert ctx["last_handoff"]["summary"] == "Investigated auth token issues. Two findings."
    assert finding_a["id"] in ctx["last_handoff"]["memory_refs"]
    assert finding_b["id"] in ctx["last_handoff"]["memory_refs"]

    # colleague uses refs to get exact context without searching
    for ref_id in ctx["last_handoff"]["memory_refs"]:
        mem = await colleague.get_memory(ref_id)
        assert "auth" in mem["content"].lower() or "token" in mem["content"].lower()


async def test_handoff_in_progress_field(scenario):
    """
    Agent saves a handoff with in_progress items describing suspended work.
    The next session restores the work-in-progress list.
    """
    agent = await scenario.agent("worker")

    await agent.save_handoff(
        summary="Mid-sprint, pausing for sleep.",
        in_progress=[
            "Refactoring auth middleware — 60% done",
            "Writing tests for /refresh endpoint — not started",
        ],
        next_steps=["Finish auth middleware", "Run full test suite"],
    )

    ctx = await agent.load_handoff()
    wip = ctx["last_handoff"]["in_progress"]
    assert len(wip) == 2
    assert any("auth middleware" in item for item in wip)
    assert any("refresh" in item for item in wip)


async def test_warm_handoff_pattern(scenario):
    """
    Full warm handoff:
      Agent A does a session's work, writes memory, saves handoff with refs.
      Agent B loads A's handoff, follows the refs, then continues and saves its own.
      Agent C loads B's handoff and sees full chain.
    """
    agent_a = await scenario.agent("agent-a")
    agent_b = await scenario.agent("agent-b")
    agent_c = await scenario.agent("agent-c")

    # Agent A works and hands off
    mem1 = await agent_a.write_memory("Discovered: DB migration 045 is missing a rollback clause")
    mem2 = await agent_a.write_memory(
        "Temporary fix: added manual rollback script at scripts/rollback_045.sh"
    )
    await agent_a.save_handoff(
        summary="Found migration issue. Applied temp fix.",
        memory_refs=[mem1["id"], mem2["id"]],
        next_steps=["Proper fix: add rollback to 045", "Notify DBA team"],
        in_progress=["Investigating whether 046 has same issue"],
    )
    await asyncio.sleep(0.01)

    # Agent B picks up from A's handoff
    ctx_a = await agent_a.load_handoff()
    handoff = ctx_a["last_handoff"]
    assert len(handoff["memory_refs"]) == 2

    for ref_id in handoff["memory_refs"]:
        fetched = await agent_b.get_memory(ref_id)
        assert fetched["id"] == ref_id

    assert "Investigating whether 046" in handoff["in_progress"][0]

    mem3 = await agent_b.write_memory(
        "Confirmed: migration 046 is clean. Only 045 affected.",
        parents=[mem1["id"], mem2["id"]],
    )
    await agent_b.save_handoff(
        summary="Confirmed scope: only 045 affected. Proper fix needed.",
        memory_refs=[mem1["id"], mem2["id"], mem3["id"]],
        next_steps=["Add rollback to 045", "Deploy"],
        in_progress=[],
    )
    await asyncio.sleep(0.01)

    mem4 = await agent_c.write_memory(
        "Fixed: migration 045 now has proper rollback. Tested in staging.",
        parents=[mem3["id"]],
    )

    ctx_b = await agent_b.load_handoff()
    assert (
        ctx_b["last_handoff"]["summary"] == "Confirmed scope: only 045 affected. Proper fix needed."
    )
    assert mem4["id"] in {e["id"] for e in ctx_b["memory_delta"]}


# ── Memory provenance ─────────────────────────────────────────────────────────


async def test_memory_parents_chain(scenario):
    """
    Each refinement of a finding sets parents to the prior version's ID,
    creating a traceable lineage.
    """
    agent_a = await scenario.agent("agent-a")
    agent_b = await scenario.agent("agent-b")
    agent_c = await scenario.agent("agent-c")

    root = await agent_a.write_memory("Hypothesis: cache miss rate is high due to TTL mismatch")

    child = await agent_b.write_memory(
        "Validated: TTL in code is 60s but Redis config sets 30s — 50% cache miss rate",
        parents=[root["id"]],
    )

    leaf = await agent_c.write_memory(
        "Fixed: aligned TTL to 60s in Redis. Cache miss rate dropped to 8%.",
        parents=[child["id"]],
    )

    assert root["parents"] == []
    assert child["parents"] == [root["id"]]
    assert leaf["parents"] == [child["id"]]

    fetched_leaf = await agent_a.get_memory(leaf["id"])
    assert fetched_leaf["parents"] == [child["id"]]


async def test_memory_multi_parent_synthesis(scenario):
    """
    An agent synthesises from multiple sources and records all parents.
    """
    src_a = await scenario.agent("source-a")
    src_b = await scenario.agent("source-b")
    synthesiser = await scenario.agent("synthesiser")

    mem_a = await src_a.write_memory("Metric A: p99 API latency is 340ms on /search")
    mem_b = await src_b.write_memory("Metric B: DB query for /search takes 280ms on average")

    synthesis = await synthesiser.write_memory(
        "Root cause: /search latency (340ms p99) is dominated by DB query (280ms). "
        "Index optimisation should cut latency by 60-70%.",
        parents=[mem_a["id"], mem_b["id"]],
        confidence=0.9,
        tags=["latency", "root-cause", "synthesis"],
    )

    assert set(synthesis["parents"]) == {mem_a["id"], mem_b["id"]}

    fetched = await src_a.get_memory(synthesis["id"])
    assert fetched["confidence"] == 0.9
    assert set(fetched["parents"]) == {mem_a["id"], mem_b["id"]}


# ── Advanced event patterns ───────────────────────────────────────────────────


async def test_event_agent_filter(scenario):
    """poll_events with agent= returns only events from that specific agent."""
    alpha = await scenario.agent("alpha")
    beta = await scenario.agent("beta")
    since = "1970-01-01T00:00:00.000Z"

    await alpha.emit_event("alpha.ping", {"n": 1})
    await alpha.emit_event("alpha.ping", {"n": 2})
    await beta.emit_event("beta.ping", {"n": 1})

    alpha_events = await alpha.poll_events(since, agent="alpha")
    assert all(e["agent_id"] == "alpha" for e in alpha_events)
    # only the 2 alpha.pings (plus any task/memory events alpha emitted)
    ping_events = [e for e in alpha_events if e["type"] == "alpha.ping"]
    assert len(ping_events) == 2

    beta_events = await alpha.poll_events(since, agent="beta")
    assert all(e["agent_id"] == "beta" for e in beta_events)
    beta_pings = [e for e in beta_events if e["type"] == "beta.ping"]
    assert len(beta_pings) == 1


async def test_supervisor_monitors_completion(scenario):
    """
    Supervisor creates N tasks, workers complete them concurrently.
    Supervisor polls the event stream until all task.completed events arrive,
    then sends a summary message to the team.
    """
    supervisor = await scenario.agent("supervisor")
    workers = [await scenario.agent(f"w-{i}") for i in range(4)]
    since = "1970-01-01T00:00:00.000Z"

    tasks = [await supervisor.create_task(f"Job {i}") for i in range(4)]
    task_ids = {t["id"] for t in tasks}

    for worker, task in zip(workers, tasks):
        await worker.claim_task(task["id"])

    for worker, task in zip(workers, tasks):
        result = await worker.write_memory(
            f"Completed job {task['title']}. Output: OK.",
            tags=["output"],
        )
        await worker.update_task(task["id"], description=f"Result: [{result['id']}]", append=True)
        await worker.complete_task(task["id"])

    all_events = await supervisor.poll_events(since, type="task.completed")
    completed_task_ids = {e["payload"]["task_id"] for e in all_events}
    assert task_ids.issubset(completed_task_ids)

    completed = await supervisor.list_tasks(status="completed")
    assert len(completed) == 4

    await supervisor.send_message(
        to="broadcast",
        body=f"All {len(tasks)} jobs complete. Check memory for outputs.",
    )


async def test_event_driven_fanout_and_collect(scenario):
    """
    Orchestrator fans out by emitting N work items as events.
    N workers each pick up one event, execute a task, write memory.
    Orchestrator polls for all completion events and verifies full coverage.
    """
    orchestrator = await scenario.agent("orchestrator")
    workers = [await scenario.agent(f"fan-worker-{i}") for i in range(5)]
    since = "1970-01-01T00:00:00.000Z"

    datasets = [f"dataset-{i}" for i in range(5)]
    for ds in datasets:
        await orchestrator.emit_event("work.assigned", {"dataset": ds})

    work_events = await workers[0].poll_events(since, type="work.assigned")
    assert len(work_events) == 5

    result_ids = []
    for worker, evt in zip(workers, work_events):
        ds = evt["payload"]["dataset"]
        mem = await worker.write_memory(
            f"Processed {ds}: 10K rows, no anomalies.",
            tags=["output", ds],
        )
        result_ids.append(mem["id"])
        await worker.emit_event("work.done", {"dataset": ds, "result_id": mem["id"]})

    done_events = await orchestrator.poll_events(since, type="work.done")
    assert len(done_events) == 5
    collected_result_ids = {e["payload"]["result_id"] for e in done_events}
    assert set(result_ids) == collected_result_ids


# ── Memory list time filters ──────────────────────────────────────────────────


async def test_memory_list_updated_before_filter(scenario):
    """list_memory with updated_before excludes entries modified after the cutoff."""
    agent = await scenario.agent("writer")

    early = await agent.write_memory("Written early")
    await asyncio.sleep(0.01)
    cutoff = early["updated_at"]
    await asyncio.sleep(0.01)

    late = await agent.write_memory("Written late")

    results = await agent.list_memory(updated_before=cutoff)
    ids = {m["id"] for m in results}
    assert early["id"] not in ids
    assert late["id"] not in ids

    all_results = await agent.list_memory()
    all_ids = {m["id"] for m in all_results}
    assert early["id"] in all_ids
    assert late["id"] in all_ids


async def test_memory_list_created_before_filter(scenario):
    """list_memory with created_before returns only entries created before that timestamp."""
    agent = await scenario.agent("writer")

    before_entries = []
    for i in range(3):
        m = await agent.write_memory(f"Entry before cutoff {i}")
        before_entries.append(m)

    await asyncio.sleep(0.01)
    cutoff = before_entries[-1]["created_at"]
    await asyncio.sleep(0.01)

    after_entries = []
    for i in range(3):
        m = await agent.write_memory(f"Entry after cutoff {i}")
        after_entries.append(m)

    results = await agent.list_memory(created_before=cutoff)
    result_ids = {m["id"] for m in results}
    for m in after_entries:
        assert m["id"] not in result_ids


async def test_memory_delta_agent_filter(scenario):
    """memory_delta with agent= returns only entries from that specific agent."""
    agent_x = await scenario.agent("agent-x")
    agent_y = await scenario.agent("agent-y")

    seed = await agent_x.write_memory("Seed entry to establish a timestamp")
    await asyncio.sleep(0.01)
    cutoff = seed["updated_at"]
    await asyncio.sleep(0.01)

    x_mem = await agent_x.write_memory("Agent X's finding after cutoff")
    await agent_y.write_memory("Agent Y's finding after cutoff")

    r = await agent_x._http.get("/memory/delta", params={"since": cutoff, "agent": "agent-x"})
    delta = r.json()
    ids = {e["id"] for e in delta}
    assert x_mem["id"] in ids
    assert all(e["agent_id"] == "agent-x" for e in delta)


# ── Authorization completeness ────────────────────────────────────────────────


async def test_non_member_cannot_write_project_memory(scenario):
    """Writing memory scoped to a project requires membership — 403 for outsiders."""
    outsider = await scenario.agent("outsider")
    member = await scenario.agent("member")

    await member.join_project("restricted")

    r = await outsider._http.post(
        "/memory",
        json={"content": "injected knowledge", "project": "restricted", "scope": "project"},
    )
    assert r.status_code == 403

    member_list = await member.list_memory(project="restricted")
    assert len(member_list) == 0


async def test_claim_then_fail_then_reroute(scenario):
    """
    Dead-letter pattern: task fails twice; a triage agent re-creates it
    with higher priority and assigns a senior. Senior completes it.
    Senior writes a post-mortem and notifies ops.
    """
    ops = await scenario.agent("ops")
    junior_a = await scenario.agent("junior-a")
    junior_b = await scenario.agent("junior-b")
    senior = await scenario.agent("senior")
    triage = await scenario.agent("triage")

    original = await ops.create_task("Deploy new ML model to prod", priority="normal")

    await junior_a.claim_task(original["id"])
    await junior_a.fail_task(original["id"])

    retry = await triage.create_task(
        "Deploy new ML model to prod — retry",
        description=f"Retry of [{original['id']}]. Junior A failed.",
        priority="high",
    )
    await junior_b.claim_task(retry["id"])
    await junior_b.fail_task(retry["id"])

    dead_letter = await triage.create_task(
        "Deploy ML model — ESCALATED",
        description=f"Two failures. Refs: [{original['id']}], [{retry['id']}]. Assign senior.",
        priority="high",
    )
    await senior.claim_task(dead_letter["id"])
    post_mortem = await senior.write_memory(
        "ML model deploy failed twice due to missing GPU driver on node-07. "
        "Fixed: pinned deployment to nodes with gpu=true label.",
        tags=["deploy", "ml", "post-mortem"],
        confidence=1.0,
    )
    await senior.update_task(
        dead_letter["id"], description=f"Fixed. Post-mortem: [{post_mortem['id']}]", append=True
    )
    await senior.complete_task(dead_letter["id"])
    await senior.send_message(to="ops", body=f"Deployed. Root cause in [{post_mortem['id']}].")

    ops_inbox = await ops.inbox()
    assert any(post_mortem["id"] in m["body"] for m in ops_inbox)
    assert (await ops.get_task(dead_letter["id"]))["status"] == "completed"

    results = await ops.search_memory("GPU driver deploy ML")
    assert any(r["id"] == post_mortem["id"] for r in results)


async def test_project_leave_and_rejoin(scenario):
    """
    Access to project memory is fully restored after leave + rejoin.
    """
    member = await scenario.agent("member")
    writer = await scenario.agent("writer")

    await member.join_project("cyclical")
    await writer.join_project("cyclical")

    scoped = await writer.write_memory(
        "Cyclical project secret", project="cyclical", scope="project"
    )

    assert any(m["id"] == scoped["id"] for m in await member.list_memory(project="cyclical"))

    await member.leave_project("cyclical")
    assert not any(m["id"] == scoped["id"] for m in await member.list_memory(project="cyclical"))

    await member.join_project("cyclical")
    assert any(m["id"] == scoped["id"] for m in await member.list_memory(project="cyclical"))


async def test_partial_inbox_clear_via_individual_reads(scenario):
    """
    With 10 messages, marking 4 individually leaves exactly 6 unread.
    """
    sender = await scenario.agent("sender")
    receiver = await scenario.agent("receiver")

    msgs = []
    for i in range(10):
        m = await sender.send_message(to="receiver", body=f"message {i}")
        msgs.append(m)

    for m in msgs[:4]:
        await receiver.mark_message_read(m["id"])

    unread = await receiver.inbox()
    assert len(unread) == 6
    read_ids = {m["id"] for m in msgs[:4]}
    assert not any(m["id"] in read_ids for m in unread)


async def test_message_forwarding_chain(scenario):
    """
    Agent A sends to B; B forwards to C with added context; C acts on it.
    Final memory entry is reachable by A via search.
    """
    agent_a = await scenario.agent("agent-a")
    agent_b = await scenario.agent("agent-b")
    agent_c = await scenario.agent("agent-c")

    await agent_a.send_message(
        to="agent-b",
        subject="FWD: data anomaly",
        body="Spike in error rate at 14:32 UTC. Can you investigate?",
    )

    original = (await agent_b.inbox())[0]
    await agent_b.send_message(
        to="agent-c",
        subject=f"FWD: {original['subject']}",
        body=f"Original from {original['from_agent']}: {original['body']}\n\nC, you own this service — please check.",
    )

    forwarded = (await agent_c.inbox())[0]
    assert "14:32 UTC" in forwarded["body"]
    assert "agent-a" in forwarded["body"]

    finding = await agent_c.write_memory(
        "Error spike at 14:32 UTC caused by bad deploy rolled back at 14:45 UTC. "
        "No data loss. Monitor for recurrence.",
        tags=["incident", "error-spike"],
    )
    await agent_c.send_message(
        to="agent-b",
        body=f"Found root cause. See memory [{finding['id']}].",
    )
    b_reply = (await agent_b.inbox())[0]
    await agent_b.send_message(
        to="agent-a",
        body=f"Resolved. Details: {b_reply['body']}",
    )

    a_final = (await agent_a.inbox())[0]
    assert finding["id"] in a_final["body"]

    results = await agent_a.search_memory("error spike rollback deploy")
    assert any(r["id"] == finding["id"] for r in results)


async def test_search_memory_tag_and_project_combined(scenario):
    """
    search_memory with both tag= and project= returns only entries matching both.
    """
    member = await scenario.agent("member")
    writer = await scenario.agent("writer")

    await member.join_project("proj")
    await writer.join_project("proj")

    match = await writer.write_memory(
        "Match: tagged and in project",
        project="proj",
        scope="project",
        tags=["target-tag"],
    )
    wrong_tag = await writer.write_memory(
        "Wrong tag: in project but different tag",
        project="proj",
        scope="project",
        tags=["other-tag"],
    )
    no_project = await writer.write_memory(
        "No project: has target-tag but not in project",
        tags=["target-tag"],
    )

    results = await member.search_memory("tagged project entry", project="proj", tag="target-tag")
    ids = {r["id"] for r in results}
    assert match["id"] in ids
    assert wrong_tag["id"] not in ids
    assert no_project["id"] not in ids


async def test_high_confidence_memory_discoverable(scenario):
    """
    Write one high-confidence authoritative entry alongside many lower-confidence
    entries on the same topic. The authoritative entry appears in search results.
    """
    authority = await scenario.agent("authority")
    contributors = [await scenario.agent(f"contrib-{i}") for i in range(8)]

    for i, c in enumerate(contributors):
        await c.write_memory(
            f"Contributor {i} estimate: deploy window is approximately {i + 1} hours",
            confidence=0.3 + i * 0.05,
            tags=["deploy-window"],
        )

    canonical = await authority.write_memory(
        "Canonical: deploy window is exactly 2 hours. Defined in runbook v3, section 4.2.",
        confidence=1.0,
        tags=["deploy-window", "canonical"],
    )

    reader = await scenario.agent("reader")
    results = await reader.search_memory("deploy window duration", limit=20)
    ids = {r["id"] for r in results}
    assert canonical["id"] in ids


# ── Agent rename (cascade correctness) ───────────────────────────────────────


async def test_agent_rename_cascades_to_all_records(scenario):
    """
    Renaming an agent updates every table that references agent_id:
    memory, tasks, messages, events, session_handoffs, and project_members.
    After the rename the agent can still operate under its new identity.
    """
    agent = await scenario.agent("old-name")
    peer = await scenario.agent("peer")

    await agent.join_project("renaming-proj")

    mem = await agent.write_memory("Written before rename", tags=["pre-rename"])
    task = await agent.create_task("Task before rename", project="renaming-proj")
    await agent.send_message(to="peer", body="Message before rename")
    await agent.emit_event("pre.rename.event", {"note": "emitted before rename"})
    await agent.save_handoff(summary="Session before rename")

    renamed = await agent.rename("new-name")
    assert renamed["agent_id"] == "new-name"

    fetched_mem = await peer.get_memory(mem["id"])
    assert fetched_mem["agent_id"] == "new-name"

    fetched_task = await agent.get_task(task["id"])
    assert fetched_task["created_by"] == "new-name"

    peer_inbox = await peer.inbox()
    assert any(m["from_agent"] == "new-name" for m in peer_inbox)

    since = "1970-01-01T00:00:00.000Z"
    all_events = await peer.poll_events(since, agent="new-name")
    assert any(e["agent_id"] == "new-name" for e in all_events)

    participants = {p["agent_id"] for p in await peer.participants()}
    assert "new-name" in participants
    assert "old-name" not in participants

    proj_members = await agent._http.get("/projects/renaming-proj/members")
    member_ids = {m["agent_id"] for m in proj_members.json()}
    assert "new-name" in member_ids
    assert "old-name" not in member_ids

    new_mem = await agent.write_memory("Written after rename")
    assert new_mem["agent_id"] == "new-name"


async def test_agent_rename_preserves_project_membership(scenario):
    """
    After rename, the agent retains access to all projects it was in.
    Project-scoped memory written before the rename is still accessible.
    """
    agent = await scenario.agent("before-rename")
    writer = await scenario.agent("writer")

    await agent.join_project("secure-proj")
    await writer.join_project("secure-proj")

    scoped = await writer.write_memory(
        "Project secret written before rename",
        project="secure-proj",
        scope="project",
    )

    await agent.rename("after-rename")

    member_list = await agent.list_memory(project="secure-proj")
    assert any(m["id"] == scoped["id"] for m in member_list)

    results = await agent.search_memory("project secret before rename")
    assert any(r["id"] == scoped["id"] for r in results)


async def test_agent_rename_id_conflict_rejected(scenario):
    """Renaming to an already-taken agent ID returns 409."""
    agent_a = await scenario.agent("agent-a-rename")
    await scenario.agent("agent-b-rename")

    r = await agent_a._http.patch("/agents/me", json={"new_id": "agent-b-rename"})
    assert r.status_code == 409


# ── Task state machine completeness ──────────────────────────────────────────


async def test_fail_completed_task_rejected(scenario):
    """A completed task cannot be failed — 409."""
    creator = await scenario.agent("creator")
    worker = await scenario.agent("worker")

    task = await creator.create_task("Already done")
    await worker.claim_task(task["id"])
    await worker.complete_task(task["id"])

    r = await worker._http.post(f"/tasks/{task['id']}/fail")
    assert r.status_code == 409


async def test_complete_failed_task_rejected(scenario):
    """A failed task cannot be completed — 409."""
    creator = await scenario.agent("creator")
    worker = await scenario.agent("worker")

    task = await creator.create_task("Doomed task")
    await worker.claim_task(task["id"])
    await worker.fail_task(task["id"])

    r = await worker._http.post(f"/tasks/{task['id']}/complete")
    assert r.status_code == 409


async def test_claim_failed_task_rejected(scenario):
    """A failed task cannot be claimed — 409."""
    creator = await scenario.agent("creator")
    worker_a = await scenario.agent("worker-a")
    worker_b = await scenario.agent("worker-b")

    task = await creator.create_task("Task that will fail")
    await worker_a.claim_task(task["id"])
    await worker_a.fail_task(task["id"])

    r = await worker_b._http.post(f"/tasks/{task['id']}/claim")
    assert r.status_code == 409


async def test_complete_open_task_rejected(scenario):
    """An unclaimed (open) task cannot be completed — 409."""
    creator = await scenario.agent("creator")
    task = await creator.create_task("Nobody claimed me")
    r = await creator._http.post(f"/tasks/{task['id']}/complete")
    assert r.status_code == 409


# ── Authorization completeness (new attack surfaces) ─────────────────────────


async def test_non_member_cannot_search_project_memory(scenario):
    """search_memory with project= returns empty for non-members, not project contents."""
    member = await scenario.agent("member")
    outsider = await scenario.agent("outsider")
    writer = await scenario.agent("writer")

    await member.join_project("private-search")
    await writer.join_project("private-search")

    secret = await writer.write_memory(
        "Classified: zero-day in the rate limiter bypass",
        project="private-search",
        scope="project",
    )

    member_results = await member.search_memory("zero-day rate limiter", project="private-search")
    assert any(r["id"] == secret["id"] for r in member_results)

    outsider_results = await outsider.search_memory(
        "zero-day rate limiter", project="private-search"
    )
    assert not any(r["id"] == secret["id"] for r in outsider_results)
    assert outsider_results == []


async def test_non_member_cannot_delta_project_memory(scenario):
    """memory_delta with project= returns empty for non-members."""
    member = await scenario.agent("member")
    outsider = await scenario.agent("outsider")
    writer = await scenario.agent("writer")

    await member.join_project("delta-guard")
    await writer.join_project("delta-guard")

    since = "1970-01-01T00:00:00.000Z"
    await writer.write_memory("Delta-guarded secret", project="delta-guard", scope="project")

    member_delta = await member._http.get(
        "/memory/delta", params={"since": since, "project": "delta-guard"}
    )
    assert any(m["project"] == "delta-guard" for m in member_delta.json())

    outsider_delta = await outsider._http.get(
        "/memory/delta", params={"since": since, "project": "delta-guard"}
    )
    assert outsider_delta.json() == []


async def test_non_member_cannot_create_project_task(scenario):
    """Creating a task scoped to a project requires membership — 403 for outsiders."""
    outsider = await scenario.agent("outsider")
    member = await scenario.agent("member")

    await member.join_project("guarded-tasks")

    r = await outsider._http.post(
        "/tasks", json={"title": "Injected task", "project": "guarded-tasks"}
    )
    assert r.status_code == 403

    member_tasks = await member.list_tasks(project="guarded-tasks")
    assert len(member_tasks) == 0


# ── Memory filters (remaining params) ────────────────────────────────────────


async def test_memory_list_min_version_filter(scenario):
    """list_memory with min_version= returns only entries with that many edits."""
    agent = await scenario.agent("editor")

    once = await agent.write_memory("Written once — version 1")
    twice = await agent.write_memory("Will be edited")
    await agent.update_memory(twice["id"], content="Edited once — now version 2")
    thrice = await agent.write_memory("Will be edited twice")
    await agent.update_memory(thrice["id"], content="First edit")
    await agent.update_memory(thrice["id"], content="Second edit — version 3")

    v2_plus = await agent.list_memory(min_version=2)
    ids = {m["id"] for m in v2_plus}
    assert twice["id"] in ids
    assert thrice["id"] in ids
    assert once["id"] not in ids

    v3_plus = await agent.list_memory(min_version=3)
    ids = {m["id"] for m in v3_plus}
    assert thrice["id"] in ids
    assert twice["id"] not in ids


async def test_memory_list_scope_change(scenario):
    """An agent can escalate a private memory to project scope."""
    agent = await scenario.agent("agent")
    reader = await scenario.agent("reader")

    await agent.join_project("shared-proj")
    await reader.join_project("shared-proj")

    private = await agent.write_memory("Initially private", scope="agent")

    r = await reader._http.get(f"/memory/{private['id']}")
    assert r.status_code == 403

    await agent.update_memory(private["id"], scope="project", project="shared-proj")

    now_visible = await reader.get_memory(private["id"])
    assert now_visible["scope"] == "project"


# ── Inbox agent= filter ───────────────────────────────────────────────────────


async def test_inbox_agent_filter(scenario):
    """
    GET /messages/inbox?agent= lets an agent read another agent's inbox
    (admin-style monitoring pattern).
    """
    sender = await scenario.agent("sender")
    await scenario.agent("target")
    monitor = await scenario.agent("monitor")

    await sender.send_message(to="target", body="Secret message for target")

    target_inbox = await monitor._http.get("/messages/inbox", params={"agent": "target"})
    assert target_inbox.status_code == 200
    msgs = target_inbox.json()
    assert any("Secret message for target" in m["body"] for m in msgs)


# ── Edge cases uncovered ──────────────────────────────────────────────────────


async def test_update_nonexistent_task(scenario):
    """Patching a task that does not exist returns 404."""
    agent = await scenario.agent("agent")
    r = await agent._http.patch("/tasks/ghost-id", json={"title": "nope"})
    assert r.status_code == 404


async def test_update_nonexistent_memory(scenario):
    """Patching a memory entry that does not exist returns 404."""
    agent = await scenario.agent("agent")
    r = await agent._http.patch("/memory/ghost-id", json={"content": "nope"})
    assert r.status_code == 404


async def test_write_multiple_then_delete_self(scenario):
    """
    An agent writes memory and sends messages, then deletes itself.
    Its memory entries remain; it can no longer authenticate.
    """
    mortal = await scenario.agent("mortal")
    peer = await scenario.agent("peer")

    mem = await mortal.write_memory("Last words before deletion")
    await mortal.send_message(to="peer", body="Goodbye, I am deleting myself")

    await mortal.delete_self()

    still_there = await peer.get_memory(mem["id"])
    assert still_there["id"] == mem["id"]

    inbox = await peer.inbox()
    assert any("Goodbye" in m["body"] for m in inbox)

    r = await mortal._http.get("/memory")
    assert r.status_code == 401

    participants = {p["agent_id"] for p in await peer.participants()}
    assert "mortal" not in participants


async def test_memory_search_with_max_distance(scenario):
    """
    max_distance filters by ANN distance. With the zero-vector mock all distances
    are exactly 0.0, so max_distance=0.0 passes (0 <= 0) and max_distance=-0.001
    excludes everything (0 > -0.001 is true — nothing passes).
    """
    agent = await scenario.agent("agent")
    await agent.write_memory("target entry for max distance test")

    results_open = await agent.search_memory("target entry max distance", max_distance=1.0)
    assert len(results_open) > 0

    results_closed = await agent.search_memory("target entry max distance", max_distance=-0.001)
    assert len(results_closed) == 0


async def test_task_update_does_not_change_status(scenario):
    """PATCH /tasks/:id cannot change task status — only title, description, priority."""
    creator = await scenario.agent("creator")
    task = await creator.create_task("Status-stable task")

    await creator._http.patch(f"/tasks/{task['id']}", json={"status": "completed"})
    fetched = await creator.get_task(task["id"])
    assert fetched["status"] == "open"


async def test_large_memory_content(scenario):
    """Memory entries support large content blobs without truncation."""
    agent = await scenario.agent("agent")
    large = "A" * 50_000 + " the critical keyword is here " + "B" * 50_000

    mem = await agent.write_memory(large)
    fetched = await agent.get_memory(mem["id"])
    assert len(fetched["content"]) == len(large)
    assert "critical keyword" in fetched["content"]


async def test_concurrent_project_joins(scenario):
    """Many agents join the same project simultaneously without conflicts."""
    agents = [await scenario.agent(f"joiner-{i}") for i in range(15)]

    await asyncio.gather(*[a.join_project("popular-proj") for a in agents])

    member = agents[0]
    proj_members = await member._http.get("/projects/popular-proj/members")
    member_ids = {m["agent_id"] for m in proj_members.json()}
    for ag in agents:
        assert ag.id in member_ids


async def test_events_from_deleted_agent_still_visible(scenario):
    """
    Events emitted by an agent before it deletes itself remain in the event log.
    """
    mortal = await scenario.agent("mortal-emitter")
    watcher = await scenario.agent("watcher")

    since = "1970-01-01T00:00:00.000Z"
    await mortal.emit_event("farewell.event", {"msg": "going away"})
    await mortal.delete_self()

    events = await watcher.poll_events(since, type="farewell.event")
    assert len(events) == 1
    assert events[0]["agent_id"] == "mortal-emitter"


# ── Memory PATCH security ─────────────────────────────────────────────────────


async def test_memory_patch_project_change_requires_membership(scenario):
    """
    An agent cannot move their memory into a project they are not a member of.
    """
    author = await scenario.agent("author")
    await scenario.agent("member").then if False else None  # register member
    member = await scenario.agent("member")

    await member.join_project("exclusive")
    mem = await author.write_memory("Public memory, not yet scoped")

    r = await author._http.patch(
        f"/memory/{mem['id']}",
        json={"project": "exclusive", "scope": "project"},
    )
    assert r.status_code == 403

    still_global = await member.get_memory(mem["id"])
    assert still_global["project"] is None


async def test_memory_patch_scope_project_without_project_field_rejected(scenario):
    """
    Setting scope='project' on a memory that has no project is rejected — 422.
    """
    agent = await scenario.agent("agent")
    mem = await agent.write_memory("Unscoped memory")
    assert mem["project"] is None

    r = await agent._http.patch(f"/memory/{mem['id']}", json={"scope": "project"})
    assert r.status_code == 422


async def test_memory_patch_project_change_valid_member(scenario):
    """
    An agent CAN move their memory into a project they are a member of.
    """
    author = await scenario.agent("author")
    reader = await scenario.agent("reader")

    await author.join_project("shared")
    await reader.join_project("shared")

    mem = await author.write_memory("About to become project-scoped")
    assert mem["project"] is None

    updated = await author.update_memory(mem["id"], project="shared", scope="project")
    assert updated["project"] == "shared"
    assert updated["scope"] == "project"

    visible = await reader.get_memory(mem["id"])
    assert visible["project"] == "shared"


# ── Admin operations ──────────────────────────────────────────────────────────


async def test_admin_delete_cleans_up_membership(scenario):
    """
    Admin deleting an agent removes them from project_members.
    Their memory and events survive; other members retain project access.
    """
    doomed = await scenario.agent("doomed")
    survivor = await scenario.agent("survivor")
    writer = await scenario.agent("writer")

    await doomed.join_project("cleanup-test")
    await survivor.join_project("cleanup-test")
    await writer.join_project("cleanup-test")

    mem = await doomed.write_memory(
        "Written by doomed agent",
        project="cleanup-test",
        scope="project",
    )
    await doomed.emit_event("agent.working", {"status": "active"})

    await scenario.admin_delete("doomed")

    # memory and events survive
    still_there = await survivor.get_memory(mem["id"])
    assert still_there["id"] == mem["id"]

    since = "1970-01-01T00:00:00.000Z"
    events = await survivor.poll_events(since, agent="doomed")
    assert any(e["type"] == "agent.working" for e in events)

    # doomed agent is no longer in project members
    proj_members = await survivor._http.get("/projects/cleanup-test/members")
    member_ids = {m["agent_id"] for m in proj_members.json()}
    assert "doomed" not in member_ids
    assert "survivor" in member_ids

    # doomed can no longer authenticate
    r = await doomed._http.get("/memory")
    assert r.status_code == 401


async def test_admin_list_agents(scenario):
    """Admin endpoint lists all registered agents."""
    agents = [await scenario.agent(f"listed-{i}") for i in range(4)]

    all_agents = await scenario.admin_list_agents()
    agent_ids = {a["agent_id"] for a in all_agents}
    for ag in agents:
        assert ag.id in agent_ids


async def test_admin_delete_nonexistent_agent(scenario):
    """Admin deleting a non-existent agent returns 404."""
    r = await scenario._admin.delete("/agents/ghost-agent")
    assert r.status_code == 404


# ── Task pre-assignment ───────────────────────────────────────────────────────


async def test_task_pre_assigned_at_creation(scenario):
    """
    A task can be created with assigned_to pre-set.
    The pre-assigned agent sees it in their task list.
    """
    planner = await scenario.agent("planner")
    specialist = await scenario.agent("specialist")

    task = await planner.create_task(
        "Design the new auth flow",
        description="Pre-assigned to our auth specialist",
        assigned_to="specialist",
    )
    assert task["assigned_to"] == "specialist"
    assert task["status"] == "open"

    my_tasks = await specialist.list_tasks(agent="specialist")
    assert any(t["id"] == task["id"] for t in my_tasks)


async def test_pre_assigned_task_can_still_be_claimed_by_anyone(scenario):
    """
    Pre-assignment is advisory: any agent can claim an open task,
    which overwrites the assigned_to field.
    """
    planner = await scenario.agent("planner")
    await scenario.agent("intended")
    usurper = await scenario.agent("usurper")

    task = await planner.create_task("Do the thing", assigned_to="intended")
    assert task["assigned_to"] == "intended"
    assert task["status"] == "open"

    claimed = await usurper.claim_task(task["id"])
    assert claimed["assigned_to"] == "usurper"
    assert claimed["status"] == "claimed"

    final = await planner.get_task(task["id"])
    assert final["assigned_to"] == "usurper"


# ── Session handoff completeness ──────────────────────────────────────────────


async def test_handoff_all_fields_round_trip(scenario):
    """All handoff fields persist and are returned verbatim on load."""
    agent = await scenario.agent("full-handoff-agent")
    mem = await agent.write_memory("Referenced finding")

    await agent.save_handoff(
        summary="End of shift — complete context.",
        in_progress=["Debugging auth middleware — 70% done", "Reviewing PR #42"],
        next_steps=["Finish auth middleware", "Merge PR #42", "Update runbook"],
        memory_refs=[mem["id"]],
    )

    ctx = await agent.load_handoff()
    h = ctx["last_handoff"]
    assert h["summary"] == "End of shift — complete context."
    assert len(h["in_progress"]) == 2
    assert "Debugging auth middleware" in h["in_progress"][0]
    assert len(h["next_steps"]) == 3
    assert mem["id"] in h["memory_refs"]


async def test_handoff_cross_agent_load_forbidden(scenario):
    """Agent A cannot load agent B's handoff — 403."""
    agent_a = await scenario.agent("agent-a")
    agent_b = await scenario.agent("agent-b")

    await agent_a.save_handoff(summary="A's private session state")

    r = await agent_b._http.get("/sessions/handoff/agent-a")
    assert r.status_code == 403


# ── Self-register duplicate ───────────────────────────────────────────────────


async def test_register_duplicate_agent_id_rejected(scenario):
    """Registering an agent with an already-taken ID returns a conflict error."""
    await scenario.agent("existing")

    r = await scenario._admin.post("/agents/register", json={"agent_id": "existing"})
    assert r.status_code in (409, 422)


# ── Event payload richness ────────────────────────────────────────────────────


async def test_event_complex_nested_payload(scenario):
    """Events store and return arbitrarily nested JSON payloads."""
    agent = await scenario.agent("agent")
    since = "1970-01-01T00:00:00.000Z"

    payload = {
        "run_id": "abc-123",
        "metrics": {"accuracy": 0.94, "loss": 0.12, "epochs": 50},
        "tags": ["experiment", "baseline"],
        "metadata": {"git_sha": "deadbeef", "dataset": "imagenet-1k"},
    }
    await agent.emit_event("ml.run.completed", payload)

    events = await agent.poll_events(since, type="ml.run.completed")
    assert len(events) == 1
    returned = events[0]["payload"]
    assert returned["run_id"] == "abc-123"
    assert returned["metrics"]["accuracy"] == 0.94
    assert "experiment" in returned["tags"]
    assert returned["metadata"]["git_sha"] == "deadbeef"


async def test_event_type_with_dots_and_underscores(scenario):
    """Event types with dots and underscores are stored and filtered correctly."""
    agent = await scenario.agent("agent")
    since = "1970-01-01T00:00:00.000Z"

    await agent.emit_event("system.health_check.passed", {"host": "node-07"})
    await agent.emit_event("system.health_check.failed", {"host": "node-08"})

    passed = await agent.poll_events(since, type="system.health_check.passed")
    assert len(passed) == 1
    assert passed[0]["payload"]["host"] == "node-07"

    failed = await agent.poll_events(since, type="system.health_check.failed")
    assert len(failed) == 1


# ── Memory with rich tags ─────────────────────────────────────────────────────


async def test_memory_multiple_tags_each_filterable(scenario):
    """
    An entry with many tags is retrievable by any one of them.
    """
    agent = await scenario.agent("agent")

    entry = await agent.write_memory(
        "Multi-tagged knowledge entry",
        tags=["alpha", "beta", "gamma", "delta", "epsilon"],
    )

    for tag in ["alpha", "beta", "gamma", "delta", "epsilon"]:
        results = await agent.list_memory(tag=tag)
        assert any(m["id"] == entry["id"] for m in results), f"tag={tag} didn't return the entry"


async def test_memory_tag_filter_does_not_leak_other_tags(scenario):
    """
    list_memory(tag=X) returns entries tagged with X but not entries tagged only with Y.
    """
    agent = await scenario.agent("agent")

    alpha = await agent.write_memory("Only alpha", tags=["alpha"])
    beta = await agent.write_memory("Only beta", tags=["beta"])
    both = await agent.write_memory("Both alpha and beta", tags=["alpha", "beta"])

    alpha_results = await agent.list_memory(tag="alpha")
    alpha_ids = {m["id"] for m in alpha_results}
    assert alpha["id"] in alpha_ids
    assert both["id"] in alpha_ids
    assert beta["id"] not in alpha_ids


# ── Ordering guarantees ───────────────────────────────────────────────────────


async def test_task_list_ordered_by_created_at_desc(scenario):
    """Tasks are returned newest-first by default."""
    agent = await scenario.agent("agent")

    ids = []
    for i in range(5):
        t = await agent.create_task(f"Task {i}")
        ids.append(t["id"])
        await asyncio.sleep(0.01)  # ensure distinct timestamps

    tasks = await agent.list_tasks()
    returned_ids = [t["id"] for t in tasks][:5]
    assert returned_ids == list(reversed(ids))


async def test_memory_delta_ordered_by_updated_at_asc(scenario):
    """memory_delta returns entries in ascending update order."""
    agent = await scenario.agent("agent")
    seed = await agent.write_memory("Seed")
    await asyncio.sleep(0.01)
    cutoff = seed["updated_at"]
    await asyncio.sleep(0.01)

    mems = []
    for i in range(4):
        m = await agent.write_memory(f"Delta entry {i}")
        mems.append(m)

    r = await agent._http.get("/memory/delta", params={"since": cutoff})
    delta = r.json()
    timestamps = [e["updated_at"] for e in delta]
    assert timestamps == sorted(timestamps)


# ── Multi-primitive stress ────────────────────────────────────────────────────


async def test_all_primitives_under_rapid_fire(scenario):
    """
    One orchestrator and four agents rapidly exercise every primitive
    simultaneously: memory, tasks, messages, events. Verifies nothing
    deadlocks or corrupts state under concurrent mixed load.
    """
    orch = await scenario.agent("orch")
    workers = [await scenario.agent(f"rapid-{i}") for i in range(4)]
    since = "1970-01-01T00:00:00.000Z"

    async def worker_loop(w):
        mem = await w.write_memory(f"{w.id}: rapid observation")
        task = await w.create_task(f"{w.id}: rapid task")
        await w.claim_task(task["id"])
        await w.complete_task(task["id"])
        await w.send_message(to="orch", body=f"{w.id} done. mem={mem['id']}")
        await w.emit_event("rapid.done", {"worker": w.id, "mem_id": mem["id"]})
        return mem["id"]

    mem_ids = await asyncio.gather(*[worker_loop(w) for w in workers])

    completed = await orch.list_tasks(status="completed")
    assert len(completed) == 4

    inbox = await orch.inbox()
    assert len(inbox) == 4

    done_events = await orch.poll_events(since, type="rapid.done")
    assert len(done_events) == 4

    for mid in mem_ids:
        m = await orch.get_memory(mid)
        assert m is not None


async def test_project_full_lifecycle(scenario):
    """
    Full project lifecycle: create, populate, work, leave, disband.
    Verifies state is consistent at every transition.
    """
    pm = await scenario.agent("pm")
    dev = await scenario.agent("dev")
    qa = await scenario.agent("qa")

    for ag in [pm, dev, qa]:
        await ag.join_project("lifecycle-proj")

    task = await pm.create_task("Build feature X", project="lifecycle-proj")
    mem = await dev.write_memory(
        "Feature X design: REST endpoint POST /features/x",
        project="lifecycle-proj",
        scope="project",
    )
    await dev.claim_task(task["id"])
    await dev.complete_task(task["id"])

    qa_results = await qa.search_memory("feature X REST endpoint", project="lifecycle-proj")
    assert any(r["id"] == mem["id"] for r in qa_results)

    await dev.leave_project("lifecycle-proj")
    dev_list = await dev.list_memory(project="lifecycle-proj")
    assert len(dev_list) == 0

    pm_list = await pm.list_memory(project="lifecycle-proj")
    assert any(m["id"] == mem["id"] for m in pm_list)

    task_before_disband = await pm.get_task(task["id"])
    assert task_before_disband["status"] == "completed"

    await qa.leave_project("lifecycle-proj")
    await pm.leave_project("lifecycle-proj")

    pm_final = await pm.list_memory(project="lifecycle-proj")
    assert len(pm_final) == 0


# ── Project membership edge cases ─────────────────────────────────────────────


async def test_join_project_idempotent(scenario):
    """Joining the same project twice is safe — agent appears once in member list."""
    agent = await scenario.agent("agent")
    await agent.join_project("idempotent-proj")
    await agent.join_project("idempotent-proj")  # second join must not raise or duplicate

    r = await agent._http.get("/projects/idempotent-proj/members")
    assert r.status_code == 200
    members = r.json()
    agent_entries = [m for m in members if m["agent_id"] == "agent"]
    assert len(agent_entries) == 1


async def test_leave_project_when_not_member_is_silent(scenario):
    """Leaving a project the agent never joined returns 204 silently."""
    agent = await scenario.agent("agent")
    r = await agent._http.delete("/projects/ghost-proj/leave")
    assert r.status_code == 204


async def test_my_projects_list(scenario):
    """GET /projects/me lists only the projects the calling agent has joined."""
    agent = await scenario.agent("agent")
    bystander = await scenario.agent("bystander")

    await agent.join_project("proj-one")
    await agent.join_project("proj-two")

    r = await agent._http.get("/projects/mine")
    assert r.status_code == 200
    my_projects = {p["project_id"] for p in r.json()}
    assert "proj-one" in my_projects
    assert "proj-two" in my_projects

    r2 = await bystander._http.get("/projects/mine")
    bystander_projects = {p["project_id"] for p in r2.json()}
    assert "proj-one" not in bystander_projects


async def test_global_project_list_shows_counts(scenario):
    """GET /projects returns all projects with correct memory_count and task_count."""
    member = await scenario.agent("member")
    await member.join_project("counted-proj")

    await member.write_memory("Memory A", project="counted-proj")
    await member.write_memory("Memory B", project="counted-proj")
    await member.create_task("Task A", project="counted-proj")

    r = await member._http.get("/projects")
    assert r.status_code == 200
    projects = {p["name"]: p for p in r.json()}
    assert "counted-proj" in projects
    p = projects["counted-proj"]
    assert p["memory_count"] == 2
    assert p["task_count"] == 1


# ── Message validation ────────────────────────────────────────────────────────


async def test_send_message_to_nonexistent_agent_rejected(scenario):
    """Sending a message to an agent that doesn't exist returns 404."""
    sender = await scenario.agent("sender")
    r = await sender._http.post(
        "/messages",
        json={"to": "does-not-exist", "body": "Hello?"},
    )
    assert r.status_code == 404


async def test_send_message_subject_defaults_to_empty(scenario):
    """Messages sent without a subject field default to empty string."""
    sender = await scenario.agent("sender")
    receiver = await scenario.agent("receiver")

    await sender._http.post("/messages", json={"to": "receiver", "body": "no subject"})
    inbox = await receiver.inbox()
    assert inbox[0]["subject"] == ""


async def test_send_message_to_self(scenario):
    """An agent can send a message to itself and receive it in inbox."""
    agent = await scenario.agent("agent")
    await agent.send_message(to="agent", body="Note to self: check the deploy logs at 09:00")
    inbox = await agent.inbox()
    assert any("deploy logs" in m["body"] for m in inbox)
    assert all(m["from_agent"] == "agent" for m in inbox if "deploy logs" in m["body"])


# ── Task PATCH guards ─────────────────────────────────────────────────────────


async def test_patch_completed_task_rejected(scenario):
    """Patching a completed task returns 409 — terminal tasks are immutable."""
    creator = await scenario.agent("creator")
    worker = await scenario.agent("worker")

    task = await creator.create_task("Immutable after completion", description="Original")
    await worker.claim_task(task["id"])
    await worker.complete_task(task["id"])

    r = await creator._http.patch(f"/tasks/{task['id']}", json={"description": "Attempted rewrite"})
    assert r.status_code == 409

    unchanged = await creator.get_task(task["id"])
    assert unchanged["description"] == "Original"


async def test_patch_failed_task_rejected(scenario):
    """Patching a failed task returns 409."""
    creator = await scenario.agent("creator")
    worker = await scenario.agent("worker")

    task = await creator.create_task("Will fail and stay failed")
    await worker.claim_task(task["id"])
    await worker.fail_task(task["id"])

    r = await creator._http.patch(f"/tasks/{task['id']}", json={"title": "New title"})
    assert r.status_code == 409


async def test_patch_task_empty_body_is_noop(scenario):
    """PATCH with no recognized fields leaves the task completely unchanged."""
    agent = await scenario.agent("agent")
    task = await agent.create_task("Stable task", description="Original desc")
    original_updated_at = task["updated_at"]

    r = await agent._http.patch(f"/tasks/{task['id']}", json={})
    assert r.status_code == 200
    result = r.json()
    assert result["title"] == "Stable task"
    assert result["description"] == "Original desc"
    assert result["updated_at"] == original_updated_at


async def test_task_update_expected_outcome(scenario):
    """expected_outcome can be set and later updated via PATCH."""
    planner = await scenario.agent("planner")
    worker = await scenario.agent("worker")

    task = await planner.create_task(
        "Optimise query",
        expected_outcome="p99 latency < 50ms",
    )
    assert task["expected_outcome"] == "p99 latency < 50ms"

    await worker.claim_task(task["id"])
    updated = await worker.update_task(
        task["id"],
        expected_outcome="p99 latency < 30ms — revised after profiling",
    )
    assert updated["expected_outcome"] == "p99 latency < 30ms — revised after profiling"


# ── Memory soft-delete idempotency ────────────────────────────────────────────


async def test_double_delete_memory_second_is_404(scenario):
    """Deleting an already-deleted memory entry returns 404."""
    agent = await scenario.agent("agent")
    mem = await agent.write_memory("Ephemeral entry")

    await agent.delete_memory(mem["id"])

    r = await agent._http.delete(f"/memory/{mem['id']}")
    assert r.status_code == 404


async def test_delete_memory_then_write_same_content(scenario):
    """After deleting a memory, writing the same content creates a fresh entry with a new ID."""
    agent = await scenario.agent("agent")
    original = await agent.write_memory("Re-writable content")
    await agent.delete_memory(original["id"])

    fresh = await agent.write_memory("Re-writable content")
    assert fresh["id"] != original["id"]
    assert fresh["version"] == 1

    r = await agent._http.get(f"/memory/{original['id']}")
    assert r.status_code == 404


# ── Memory confidence validation ──────────────────────────────────────────────


async def test_memory_confidence_out_of_range_rejected(scenario):
    """Writing memory with confidence > 1.0 or < 0.0 is rejected with 422."""
    agent = await scenario.agent("agent")

    r_high = await agent._http.post("/memory", json={"content": "bad", "confidence": 1.5})
    assert r_high.status_code == 422

    r_neg = await agent._http.post("/memory", json={"content": "bad", "confidence": -0.1})
    assert r_neg.status_code == 422

    r_valid = await agent._http.post("/memory", json={"content": "valid", "confidence": 0.0})
    assert r_valid.status_code == 201

    r_max = await agent._http.post("/memory", json={"content": "max", "confidence": 1.0})
    assert r_max.status_code == 201


async def test_memory_update_confidence_out_of_range_rejected(scenario):
    """PATCH /memory/:id with confidence out of range is rejected with 422."""
    agent = await scenario.agent("agent")
    mem = await agent.write_memory("Normal entry", confidence=0.5)

    r = await agent._http.patch(f"/memory/{mem['id']}", json={"confidence": 2.0})
    assert r.status_code == 422

    unchanged = await agent.get_memory(mem["id"])
    assert unchanged["confidence"] == 0.5


# ── Session handoff host field ────────────────────────────────────────────────


async def test_handoff_host_field_stored_and_returned(scenario):
    """The host field on a handoff is stored and returned verbatim on load."""
    agent = await scenario.agent("agent")

    await agent.save_handoff(
        summary="Session on prod cluster",
        host="prod-node-07.internal",
    )

    ctx = await agent.load_handoff()
    assert ctx["last_handoff"]["host"] == "prod-node-07.internal"


# ── Agent own info ────────────────────────────────────────────────────────────


async def test_get_own_agent_info(scenario):
    """GET /agents/me returns the calling agent's own record."""
    agent = await scenario.agent("self-aware")
    r = await agent._http.get("/agents/me")
    assert r.status_code == 200
    info = r.json()
    assert info["agent_id"] == "self-aware"


# ── Memory parents — limitation documentation ─────────────────────────────────


async def test_memory_parents_nonexistent_ids_accepted(scenario):
    """
    Memory parents pointing to non-existent IDs are stored without validation.
    This is a known limitation — no referential integrity on parents.
    """
    agent = await scenario.agent("agent")
    ghost_id = "00000000-0000-0000-0000-000000000000"

    mem = await agent.write_memory("Entry with orphaned parent", parents=[ghost_id])
    assert mem["parents"] == [ghost_id]

    fetched = await agent.get_memory(mem["id"])
    assert fetched["parents"] == [ghost_id]


# ── Memory delta edge cases ───────────────────────────────────────────────────


async def test_memory_delta_future_since_returns_empty(scenario):
    """memory_delta with a since timestamp in the far future returns nothing."""
    agent = await scenario.agent("agent")
    await agent.write_memory("Past entry")

    r = await agent._http.get("/memory/delta", params={"since": "2099-01-01T00:00:00.000Z"})
    assert r.status_code == 200
    assert r.json() == []


async def test_memory_delta_returns_own_writes_by_others(scenario):
    """
    memory_delta since a timestamp reflects writes from ALL agents,
    not just the calling agent's own entries.
    """
    watcher = await scenario.agent("watcher")
    writer_a = await scenario.agent("writer-a")
    writer_b = await scenario.agent("writer-b")

    seed = await watcher.write_memory("Seed")
    await asyncio.sleep(0.01)
    cutoff = seed["updated_at"]
    await asyncio.sleep(0.01)

    mem_a = await writer_a.write_memory("From writer A")
    mem_b = await writer_b.write_memory("From writer B")

    r = await watcher._http.get("/memory/delta", params={"since": cutoff})
    delta_ids = {e["id"] for e in r.json()}
    assert mem_a["id"] in delta_ids
    assert mem_b["id"] in delta_ids


# ── Task agent filter completeness ────────────────────────────────────────────


async def test_task_list_agent_filter_matches_creator_and_assignee(scenario):
    """
    list_tasks(agent=X) returns tasks where X is creator OR assignee.
    """
    planner = await scenario.agent("planner")
    worker = await scenario.agent("worker")
    unrelated = await scenario.agent("unrelated")

    created = await planner.create_task("Created by planner")
    pre_assigned = await planner.create_task("Pre-assigned to worker", assigned_to="worker")
    claimed = await planner.create_task("Will be claimed by worker")
    await worker.claim_task(claimed["id"])
    unrelated_task = await unrelated.create_task("Nothing to do with planner or worker")

    worker_tasks = await planner.list_tasks(agent="worker")
    worker_ids = {t["id"] for t in worker_tasks}
    assert pre_assigned["id"] in worker_ids
    assert claimed["id"] in worker_ids
    assert created["id"] not in worker_ids
    assert unrelated_task["id"] not in worker_ids


async def test_task_list_combined_agent_and_status(scenario):
    """list_tasks(agent=X, status=Y) intersects both filters correctly."""
    creator = await scenario.agent("creator")
    worker = await scenario.agent("worker")

    t_open = await creator.create_task("Open task for worker", assigned_to="worker")
    t_claimed = await creator.create_task("Will be claimed")
    await worker.claim_task(t_claimed["id"])
    t_completed = await creator.create_task("Will be completed")
    await worker.claim_task(t_completed["id"])
    await worker.complete_task(t_completed["id"])

    claimed_by_worker = await creator.list_tasks(agent="worker", status="claimed")
    ids = {t["id"] for t in claimed_by_worker}
    assert t_claimed["id"] in ids
    assert t_open["id"] not in ids
    assert t_completed["id"] not in ids


# ── Event combined filters ────────────────────────────────────────────────────


async def test_poll_events_type_and_agent_combined(scenario):
    """poll_events with both type= and agent= returns only matching events."""
    alpha = await scenario.agent("alpha")
    beta = await scenario.agent("beta")
    since = "1970-01-01T00:00:00.000Z"

    await alpha.emit_event("shared.event", {"from": "alpha"})
    await beta.emit_event("shared.event", {"from": "beta"})
    await alpha.emit_event("other.event", {"from": "alpha"})

    results = await alpha.poll_events(since, type="shared.event", agent="alpha")
    assert all(e["type"] == "shared.event" for e in results)
    assert all(e["agent_id"] == "alpha" for e in results)
    assert len([e for e in results if e["type"] == "shared.event"]) == 1


# ── Memory tag update ─────────────────────────────────────────────────────────


async def test_memory_tags_cleared_to_empty_list(scenario):
    """Updating tags to [] removes all tags from the entry."""
    agent = await scenario.agent("agent")
    mem = await agent.write_memory("Tagged entry", tags=["a", "b", "c"])

    results_before = await agent.list_memory(tag="a")
    assert any(m["id"] == mem["id"] for m in results_before)

    cleared = await agent.update_memory(mem["id"], tags=[])
    assert cleared["tags"] == []

    results_after = await agent.list_memory(tag="a")
    assert not any(m["id"] == mem["id"] for m in results_after)


# ── Long-running agent simulation ────────────────────────────────────────────


async def test_agent_accumulates_context_over_many_sessions(scenario):
    """
    An agent accumulates knowledge across 5 simulated sessions.
    Each session loads the prior handoff, writes memory, and saves a new handoff.
    Final state reflects the full history via search.
    """
    agent = await scenario.agent("long-runner")
    topics = [
        ("auth", "Session 1: Mapped the auth service. JWT TTL is 30d."),
        ("cache", "Session 2: Identified cache miss issue. Redis TTL mismatch."),
        ("deploy", "Session 3: Fixed deploy pipeline. Added rollback step."),
        ("db", "Session 4: Optimised slow queries. Added index on memory.updated_at."),
        ("docs", "Session 5: Updated runbook. Added cache and deploy sections."),
    ]

    saved_mem_ids = []
    for i, (tag, content) in enumerate(topics):
        ctx = await agent.load_handoff()
        if i > 0:
            assert ctx["last_handoff"] is not None
            assert f"Session {i}" in ctx["last_handoff"]["summary"]

        mem = await agent.write_memory(content, tags=[tag, "session-log"])
        saved_mem_ids.append(mem["id"])
        await asyncio.sleep(0.01)

        await agent.save_handoff(
            summary=f"Session {i + 1}: covered {tag}.",
            memory_refs=[mem["id"]],
            next_steps=[f"Follow up on {topics[i + 1][0]}" if i < 4 else "All done."],
        )
        await asyncio.sleep(0.01)

    final_ctx = await agent.load_handoff()
    assert "Session 5" in final_ctx["last_handoff"]["summary"]
    assert "All done." in final_ctx["last_handoff"]["next_steps"]

    for query, expected_id in [
        ("JWT TTL auth service", saved_mem_ids[0]),
        ("Redis cache miss TTL", saved_mem_ids[1]),
        ("deploy pipeline rollback", saved_mem_ids[2]),
        ("slow query index optimisation", saved_mem_ids[3]),
        ("runbook cache deploy sections", saved_mem_ids[4]),
    ]:
        results = await agent.search_memory(query)
        assert any(r["id"] == expected_id for r in results), f"missed: {query}"


async def test_task_ordering_with_same_timestamp_stable(scenario):
    """
    Tasks created within the same millisecond are still returned deterministically
    (SQLite ORDER BY created_at DESC — insertion order acts as tiebreaker via rowid).
    """
    agent = await scenario.agent("agent")
    ids = []
    for i in range(5):
        t = await agent.create_task(f"Rapid task {i}")
        ids.append(t["id"])

    tasks = await agent.list_tasks()
    returned = [t["id"] for t in tasks if t["id"] in set(ids)]
    assert set(returned) == set(ids)


# ── Project resource access by ID ─────────────────────────────────────────────


async def test_non_member_cannot_get_project_memory_by_id(scenario):
    """GET /memory/{id} must enforce project membership for project-scoped entries."""
    owner = await scenario.agent("owner")
    outsider = await scenario.agent("outsider")

    await owner.join_project("secret-proj")
    entry = await owner.write_memory("Classified info", project="secret-proj", scope="project")

    r = await outsider._http.get(f"/memory/{entry['id']}")
    assert r.status_code == 403, f"expected 403 but got {r.status_code}: {r.text}"


async def test_non_member_cannot_get_project_task_by_id(scenario):
    """GET /tasks/{id} must enforce project membership for project tasks."""
    owner = await scenario.agent("owner")
    outsider = await scenario.agent("outsider")

    await owner.join_project("priv-proj")
    task = await owner.create_task("Secret task", project="priv-proj")

    r = await outsider._http.get(f"/tasks/{task['id']}")
    assert r.status_code == 403, f"expected 403 but got {r.status_code}: {r.text}"


async def test_member_can_get_project_task_by_id(scenario):
    """GET /tasks/{id} is accessible to project members."""
    owner = await scenario.agent("owner")
    peer = await scenario.agent("peer")

    await owner.join_project("shared-proj")
    await peer.join_project("shared-proj")
    task = await owner.create_task("Shared task", project="shared-proj")

    fetched = await peer.get_task(task["id"])
    assert fetched["id"] == task["id"]


async def test_member_can_get_project_memory_by_id(scenario):
    """GET /memory/{id} is accessible to project members for project-scoped entries."""
    owner = await scenario.agent("owner")
    peer = await scenario.agent("peer")

    await owner.join_project("collab-proj")
    await peer.join_project("collab-proj")
    entry = await owner.write_memory("Shared note", project="collab-proj", scope="project")

    fetched = await peer.get_memory(entry["id"])
    assert fetched["id"] == entry["id"]


async def test_global_memory_accessible_to_all(scenario):
    """Memory with no project is readable by any agent regardless of scope='project'."""
    writer = await scenario.agent("writer")
    reader = await scenario.agent("reader")

    entry = await writer.write_memory("Public note")
    assert entry["project"] is None
    fetched = await reader.get_memory(entry["id"])
    assert fetched["id"] == entry["id"]


# ── Project members listing ────────────────────────────────────────────────────


async def test_project_members_list_visible_to_members(scenario):
    """Members of a project can list who else belongs."""
    alpha = await scenario.agent("alpha")
    beta = await scenario.agent("beta")
    gamma = await scenario.agent("gamma")

    for a in (alpha, beta, gamma):
        await a.join_project("team-proj")

    r = await alpha._http.get("/projects/team-proj/members")
    assert r.status_code == 200
    ids = {m["agent_id"] for m in r.json()}
    assert ids == {"alpha", "beta", "gamma"}


async def test_project_members_list_forbidden_to_non_members(scenario):
    """Non-members cannot list project members."""
    insider = await scenario.agent("insider")
    outsider = await scenario.agent("outsider")

    await insider.join_project("closed-proj")

    r = await outsider._http.get("/projects/closed-proj/members")
    assert r.status_code == 403


async def test_project_members_list_empty_project(scenario):
    """An empty project (no members yet) returns empty list once member joins."""
    solo = await scenario.agent("solo")
    await solo.join_project("solo-proj")

    r = await solo._http.get("/projects/solo-proj/members")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["agent_id"] == "solo"


# ── Session handoff accumulation ──────────────────────────────────────────────


async def test_multiple_handoffs_latest_is_returned(scenario):
    """Saving multiple handoffs keeps them all; GET returns only the latest."""
    agent = await scenario.agent("agent")

    await agent.save_handoff("First handoff", next_steps=["step A"])
    await asyncio.sleep(0.01)
    await agent.save_handoff("Second handoff", next_steps=["step B"])
    await asyncio.sleep(0.01)
    await agent.save_handoff("Third handoff", next_steps=["step C"])

    ctx = await agent.load_handoff()
    assert ctx["last_handoff"]["summary"] == "Third handoff"
    assert "step C" in ctx["last_handoff"]["next_steps"]


async def test_handoff_memory_delta_since_last_handoff(scenario):
    """Memory written after the last handoff appears in the delta; older memory does not."""
    agent = await scenario.agent("agent")

    old_entry = await agent.write_memory("Old memory before first handoff")
    await asyncio.sleep(0.01)
    await agent.save_handoff("First handoff")
    await asyncio.sleep(0.01)
    new_entry = await agent.write_memory("New memory after handoff")

    ctx = await agent.load_handoff()
    delta_ids = {e["id"] for e in ctx["memory_delta"]}
    assert new_entry["id"] in delta_ids
    assert old_entry["id"] not in delta_ids


# ── Memory list filters ────────────────────────────────────────────────────────


async def test_memory_type_filter(scenario):
    """List with type= returns only matching entries."""
    agent = await scenario.agent("agent")

    await agent.write_memory("A document", type="doc")
    await agent.write_memory("A memory note", type="memory")
    await agent.write_memory("Another document", type="doc")

    docs = await agent.list_memory(type="doc")
    assert len(docs) == 2
    assert all(e["type"] == "doc" for e in docs)

    memories = await agent.list_memory(type="memory")
    assert len(memories) == 1


async def test_memory_confidence_min_filter(scenario):
    """List with confidence_min= only returns entries at or above threshold."""
    agent = await scenario.agent("agent")

    await agent.write_memory("Low confidence", confidence=0.3)
    await agent.write_memory("High confidence", confidence=0.8)
    await agent.write_memory("Exact threshold", confidence=0.5)

    results = await agent.list_memory(confidence_min=0.5)
    confidences = [e["confidence"] for e in results]
    assert all(c >= 0.5 for c in confidences)
    assert 0.3 not in confidences


async def test_memory_list_limit_respected(scenario):
    """List returns at most `limit` entries."""
    agent = await scenario.agent("agent")
    for i in range(10):
        await agent.write_memory(f"Entry {i}")

    results = await agent.list_memory(limit=3)
    assert len(results) == 3


async def test_memory_version_increments_on_patch(scenario):
    """Patching a memory entry increments its version number."""
    agent = await scenario.agent("agent")
    entry = await agent.write_memory("Initial content")
    assert entry["version"] == 1

    v2 = await agent.update_memory(entry["id"], content="Updated content")
    assert v2["version"] == 2

    v3 = await agent.update_memory(entry["id"], tags=["new-tag"])
    assert v3["version"] == 3


# ── Task field coverage ────────────────────────────────────────────────────────


async def test_task_due_at_field(scenario):
    """Task created with due_at has it stored and returned."""
    agent = await scenario.agent("agent")
    task = await agent.create_task("Deadline task", due_at="2026-12-31T23:59:00.000Z")
    assert task["due_at"] == "2026-12-31T23:59:00.000Z"

    fetched = await agent.get_task(task["id"])
    assert fetched["due_at"] == "2026-12-31T23:59:00.000Z"


async def test_task_priority_high(scenario):
    """Tasks with priority=high are stored and retrievable."""
    agent = await scenario.agent("agent")
    task = await agent.create_task("Urgent task", priority="high")
    assert task["priority"] == "high"

    high_tasks = await agent.list_tasks()
    assert any(t["id"] == task["id"] and t["priority"] == "high" for t in high_tasks)


async def test_task_get_by_id_round_trip(scenario):
    """GET /tasks/{id} returns all fields set at creation."""
    agent = await scenario.agent("agent")
    task = await agent.create_task(
        "Full task",
        description="Detailed description",
        expected_outcome="Clear outcome",
        priority="normal",
    )
    fetched = await agent.get_task(task["id"])
    assert fetched["title"] == "Full task"
    assert fetched["description"] == "Detailed description"
    assert fetched["expected_outcome"] == "Clear outcome"
    assert fetched["priority"] == "normal"
    assert fetched["status"] == "open"
    assert fetched["created_by"] == "agent"


# ── Participants endpoint ──────────────────────────────────────────────────────


async def test_participants_lists_all_agents(scenario):
    """GET /participants returns all registered agents."""
    alpha = await scenario.agent("alpha")
    await scenario.agent("beta")

    participants = await alpha.participants()
    ids = {p["agent_id"] for p in participants}
    assert "alpha" in ids
    assert "beta" in ids


async def test_participants_shows_active_task(scenario):
    """Participant with a claimed task has active_task_id set."""
    worker = await scenario.agent("worker")
    watcher = await scenario.agent("watcher")

    task = await worker.create_task("Work item")
    await worker.claim_task(task["id"])

    participants = await watcher.participants()
    worker_p = next(p for p in participants if p["agent_id"] == "worker")
    assert worker_p["active_task_id"] == task["id"]


async def test_participants_no_active_task_when_complete(scenario):
    """After task completion, active_task_id is cleared."""
    worker = await scenario.agent("worker")

    task = await worker.create_task("Completed work")
    await worker.claim_task(task["id"])
    await worker.complete_task(task["id"])

    participants = await worker.participants()
    worker_p = next(p for p in participants if p["agent_id"] == "worker")
    assert worker_p["active_task_id"] is None


# ── Broadcast sender receives own broadcast ───────────────────────────────────


async def test_broadcast_sender_sees_own_message_in_inbox(scenario):
    """Agent that sends a broadcast also sees it in their own inbox."""
    sender = await scenario.agent("sender")

    await sender.send_message("broadcast", "Attention all agents!")
    inbox = await sender.inbox()
    bodies = [m["body"] for m in inbox]
    assert "Attention all agents!" in bodies


async def test_broadcast_visible_to_late_joining_agent(scenario):
    """Broadcast sent before an agent is registered is NOT visible after the fact."""
    sender = await scenario.agent("sender")

    await sender.send_message("broadcast", "Pre-registration broadcast")

    late = await scenario.agent("late")
    inbox = await late.inbox()
    bodies = [m["body"] for m in inbox]
    assert "Pre-registration broadcast" in bodies


# ── Events boundary conditions ────────────────────────────────────────────────


async def test_events_since_is_exclusive(scenario):
    """Events with created_at == since are NOT returned (strict >)."""
    agent = await scenario.agent("agent")

    e = await agent.emit_event("marker.event")
    created_at = e["created_at"]

    events = await agent.poll_events(since=created_at)
    ids = [ev["id"] for ev in events]
    assert e["id"] not in ids, "Event at exactly 'since' should be excluded"


async def test_events_ordering_chronological(scenario):
    """poll_events returns events in ascending created_at order."""
    agent = await scenario.agent("agent")

    e1 = await agent.emit_event("order.first")
    await asyncio.sleep(0.01)
    e2 = await agent.emit_event("order.second")
    await asyncio.sleep(0.01)
    e3 = await agent.emit_event("order.third")

    events = await agent.poll_events(since="1970-01-01T00:00:00.000Z", type="order.first")
    assert events[0]["id"] == e1["id"]

    all_events = await agent.poll_events(since="1970-01-01T00:00:00.000Z")
    filtered = [ev for ev in all_events if ev["id"] in {e1["id"], e2["id"], e3["id"]}]
    assert [ev["id"] for ev in filtered] == [e1["id"], e2["id"], e3["id"]]


# ── Agent project field in registration ───────────────────────────────────────


async def test_agent_registration_project_field_is_metadata_only(scenario):
    """The project field on agent registration is metadata only — does NOT grant membership.
    Agent must explicitly join a project to access project-scoped resources."""
    r = await scenario._admin.post(
        "/agents/register", json={"agent_id": "proj-agent", "project": "auto-proj"}
    )
    r.raise_for_status()
    data = r.json()
    assert data["project"] == "auto-proj"

    from httpx import AsyncClient

    proj_agent_http = AsyncClient(
        transport=scenario._transport,
        base_url="http://test",
        headers={"x-agent-id": "proj-agent", "x-api-key": data["api_key"]},
    )
    try:
        task_r = await proj_agent_http.post(
            "/tasks", json={"title": "Auto-proj task", "project": "auto-proj"}
        )
        assert task_r.status_code == 403, "project field alone does not grant access"

        await proj_agent_http.post("/projects/auto-proj/join")
        task_r2 = await proj_agent_http.post(
            "/tasks", json={"title": "Auto-proj task", "project": "auto-proj"}
        )
        assert task_r2.status_code == 201
    finally:
        await proj_agent_http.aclose()


# ── Memory scope='agent' isolation ────────────────────────────────────────────


async def test_agent_scoped_memory_not_in_others_list(scenario):
    """scope='agent' entries do not appear in other agents' memory list."""
    owner = await scenario.agent("owner")
    spy = await scenario.agent("spy")

    private = await owner.write_memory("Private note", scope="agent")

    spy_memory = await spy.list_memory()
    ids = [e["id"] for e in spy_memory]
    assert private["id"] not in ids


async def test_agent_scoped_memory_not_in_search(scenario):
    """scope='agent' entries do not appear in other agents' search results."""
    owner = await scenario.agent("owner")
    spy = await scenario.agent("spy")

    await owner.write_memory("My secret note only I should see", scope="agent")
    results = await spy.search_memory("secret note only I should see")
    assert all(e["agent_id"] != "owner" or e["scope"] != "agent" for e in results)


async def test_owner_sees_own_agent_scoped_memory(scenario):
    """scope='agent' entries appear in the owner's list and GET."""
    owner = await scenario.agent("owner")

    private = await owner.write_memory("My secret", scope="agent")
    own_list = await owner.list_memory()
    assert any(e["id"] == private["id"] for e in own_list)

    fetched = await owner.get_memory(private["id"])
    assert fetched["id"] == private["id"]


# ── Task completion/fail who can act ──────────────────────────────────────────


async def test_non_assignee_cannot_complete_task(scenario):
    """Only the assignee can complete a claimed task."""
    creator = await scenario.agent("creator")
    worker = await scenario.agent("worker")
    bystander = await scenario.agent("bystander")

    task = await creator.create_task("Work item")
    await worker.claim_task(task["id"])

    r = await bystander._http.post(f"/tasks/{task['id']}/complete")
    assert r.status_code == 403


async def test_non_assignee_cannot_fail_task(scenario):
    """Only the assignee can fail a claimed task."""
    creator = await scenario.agent("creator")
    worker = await scenario.agent("worker")
    bystander = await scenario.agent("bystander")

    task = await creator.create_task("Work item")
    await worker.claim_task(task["id"])

    r = await bystander._http.post(f"/tasks/{task['id']}/fail")
    assert r.status_code == 403


# ── Memory PATCH owner-only ────────────────────────────────────────────────────


async def test_non_owner_cannot_patch_memory(scenario):
    """Only the author can patch a memory entry."""
    author = await scenario.agent("author")
    thief = await scenario.agent("thief")

    entry = await author.write_memory("Authoritative note")
    r = await thief._http.patch(f"/memory/{entry['id']}", json={"content": "Tampered"})
    assert r.status_code == 403


async def test_non_owner_cannot_delete_memory(scenario):
    """Only the author can delete a memory entry."""
    author = await scenario.agent("author")
    thief = await scenario.agent("thief")

    entry = await author.write_memory("Protected note")
    r = await thief._http.delete(f"/memory/{entry['id']}")
    assert r.status_code == 403


# ── No-auth requests ──────────────────────────────────────────────────────────


async def test_unauthenticated_memory_list_rejected(scenario):
    """Requests without auth headers are rejected."""
    from httpx import AsyncClient

    anon = AsyncClient(transport=scenario._transport, base_url="http://test")
    try:
        r = await anon.get("/memory")
        assert r.status_code in (401, 403)
    finally:
        await anon.aclose()


# ── Task claim transfers ownership correctly ──────────────────────────────────


async def test_claim_sets_assigned_to(scenario):
    """Claiming a task sets assigned_to to the claiming agent."""
    creator = await scenario.agent("creator")
    worker = await scenario.agent("worker")

    task = await creator.create_task("Claimable task")
    assert task["assigned_to"] is None

    claimed = await worker.claim_task(task["id"])
    assert claimed["assigned_to"] == "worker"
    assert claimed["status"] == "claimed"
    assert claimed["created_by"] == "creator"


# ── Memory scope=project requires project field ────────────────────────────────


async def test_memory_no_project_is_universally_readable(scenario):
    """Memory written without a project is accessible to everyone (no membership gate)."""
    agent = await scenario.agent("agent")
    entry = await agent.write_memory("Universal knowledge")
    assert entry["project"] is None
    assert entry["scope"] == "project"


async def test_memory_scope_agent_needs_no_project(scenario):
    """Writing memory with scope='agent' and no project field succeeds."""
    agent = await scenario.agent("agent")
    entry = await agent.write_memory("Private note", scope="agent")
    assert entry["scope"] == "agent"
    assert entry["project"] is None


# ── Project list response shape ───────────────────────────────────────────────


async def test_global_project_list_includes_agents_list(scenario):
    """GET /projects returns projects with an agents list containing all members."""
    alpha = await scenario.agent("alpha")
    beta = await scenario.agent("beta")

    await alpha.join_project("listed-proj")
    await beta.join_project("listed-proj")

    r = await alpha._http.get("/projects")
    assert r.status_code == 200
    projects = {p["name"]: p for p in r.json()}
    assert "listed-proj" in projects
    agents = set(projects["listed-proj"]["agents"])
    assert "alpha" in agents
    assert "beta" in agents


# ── Message read state ────────────────────────────────────────────────────────


async def test_mark_inbox_read_clears_all_direct_messages(scenario):
    """mark_inbox_read clears all unread direct messages at once."""
    sender = await scenario.agent("sender")
    recipient = await scenario.agent("recipient")

    for i in range(3):
        await sender.send_message("recipient", f"Message {i}")

    assert len(await recipient.inbox()) == 3
    await recipient.mark_inbox_read()
    assert len(await recipient.inbox()) == 0


async def test_direct_message_read_field_updated(scenario):
    """After marking a direct message as read, the read field is True."""
    sender = await scenario.agent("sender")
    recipient = await scenario.agent("recipient")

    msg = await sender.send_message("recipient", "Test message")
    assert not msg["read"]

    marked = await recipient.mark_message_read(msg["id"])
    assert marked["read"]


async def test_broadcast_read_per_recipient(scenario):
    """Marking a broadcast as read by agent A does not remove it from agent B's inbox."""
    broadcaster = await scenario.agent("broadcaster")
    reader_a = await scenario.agent("reader-a")
    reader_b = await scenario.agent("reader-b")

    msg = await broadcaster.send_message("broadcast", "Broadcast for all")

    await reader_a.mark_message_read(msg["id"])

    inbox_b = await reader_b.inbox()
    assert any(m["id"] == msg["id"] for m in inbox_b)
    inbox_a = await reader_a.inbox()
    assert not any(m["id"] == msg["id"] for m in inbox_a)
