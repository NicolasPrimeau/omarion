import artel.server.config as cfg_mod

# ── Group 1: owner role basics ────────────────────────────────────────────────


async def test_regular_agent_has_agent_role(scenario):
    agent = await scenario.agent("regular-one")
    r = await agent._http.get("/agents/me")
    r.raise_for_status()
    assert r.json()["role"] == "agent"


async def test_ui_agent_has_owner_role(scenario):
    owner = await scenario.owner_agent()
    r = await owner._http.get("/agents/me")
    r.raise_for_status()
    assert r.json()["role"] == "owner"


async def test_owner_role_visible_in_participants(scenario):
    await scenario.owner_agent()
    regular = await scenario.agent("participant-regular")

    parts = await regular.participants()
    uid = cfg_mod.settings.ui_agent_id
    owner_part = next((p for p in parts if p["agent_id"] == uid), None)
    regular_part = next((p for p in parts if p["agent_id"] == "participant-regular"), None)

    assert owner_part is not None
    assert owner_part["role"] == "owner"
    assert regular_part is not None
    assert regular_part["role"] == "agent"


# ── Group 2: directive write permissions ──────────────────────────────────────


async def test_regular_agent_cannot_write_directive(scenario):
    agent = await scenario.agent("nodir-agent")
    status, body = await agent.write_memory_raw(
        {"content": "forbidden directive", "type": "directive"}
    )
    assert status == 403


async def test_owner_can_write_directive(scenario):
    owner = await scenario.owner_agent()
    status, body = await owner.write_memory_raw(
        {"content": "system directive: always respond in JSON", "type": "directive"}
    )
    assert status == 201
    assert body["type"] == "directive"


async def test_directive_confidence_forced_to_1(scenario):
    owner = await scenario.owner_agent()
    status, body = await owner.write_memory_raw(
        {"content": "low confidence directive attempt", "type": "directive", "confidence": 0.3}
    )
    assert status == 201
    assert body["confidence"] == 1.0


async def test_directive_appears_in_memory_list(scenario):
    owner = await scenario.owner_agent()
    directive = await owner.write_memory_raw(
        {"content": "directive: log all decisions", "type": "directive"}
    )
    directive_id = directive[1]["id"]

    entries = await owner.list_memory()
    ids = [e["id"] for e in entries]
    assert directive_id in ids


async def test_directive_type_filter(scenario):
    owner = await scenario.owner_agent()
    directive = await owner.write_memory_raw(
        {"content": "directive: prefer structured output", "type": "directive"}
    )
    directive_id = directive[1]["id"]

    regular = await owner.write_memory("a regular memory entry")
    regular_id = regular["id"]

    directives = await owner.list_memory(type="directive")
    directive_ids = [e["id"] for e in directives]
    assert directive_id in directive_ids
    assert regular_id not in directive_ids


async def test_expires_at_stored(scenario):
    owner = await scenario.owner_agent()
    status, body = await owner.write_memory_raw(
        {
            "content": "expiring directive",
            "type": "directive",
            "expires_at": "2030-01-01T00:00:00Z",
        }
    )
    assert status == 201
    assert body["expires_at"] == "2030-01-01T00:00:00Z"


# ── Group 3: directive immutability enforcement ───────────────────────────────


async def test_regular_agent_cannot_patch_others_directive(scenario):
    owner = await scenario.owner_agent()
    _, directive = await owner.write_memory_raw(
        {"content": "immutable directive", "type": "directive"}
    )
    directive_id = directive["id"]

    attacker = await scenario.agent("patch-attacker")
    status, _ = await attacker.update_memory_raw(directive_id, {"content": "hijacked"})
    assert status == 403


async def test_owner_can_patch_any_memory(scenario):
    author = await scenario.agent("mem-author")
    mem = await author.write_memory("author's original content")

    owner = await scenario.owner_agent()
    status, body = await owner.update_memory_raw(mem["id"], {"content": "owner patched this"})
    assert status == 200
    assert body["content"] == "owner patched this"


async def test_owner_can_delete_any_memory(scenario):
    author = await scenario.agent("del-author")
    mem = await author.write_memory("content to be deleted by owner")

    owner = await scenario.owner_agent()
    status = await owner.delete_memory_raw(mem["id"])
    assert status == 204

    r = await author._http.get(f"/memory/{mem['id']}")
    assert r.status_code == 404


# ── Group 4: owner ownership bypass ──────────────────────────────────────────


async def test_regular_agent_cannot_patch_others_memory(scenario):
    agent_a = await scenario.agent("patch-owner-a")
    agent_b = await scenario.agent("patch-intruder-b")

    mem = await agent_a.write_memory("agent A's private finding")
    status, _ = await agent_b.update_memory_raw(mem["id"], {"content": "stolen"})
    assert status == 403


async def test_regular_agent_cannot_delete_others_memory(scenario):
    agent_a = await scenario.agent("del-owner-a")
    agent_b = await scenario.agent("del-intruder-b")

    mem = await agent_a.write_memory("agent A's memory")
    status = await agent_b.delete_memory_raw(mem["id"])
    assert status == 403


async def test_owner_patches_another_agents_memory(scenario):
    agent_a = await scenario.agent("owned-mem-a")
    mem = await agent_a.write_memory("original content by A")

    owner = await scenario.owner_agent()
    status, body = await owner.update_memory_raw(mem["id"], {"content": "updated by owner"})
    assert status == 200
    assert body["content"] == "updated by owner"


async def test_owner_deletes_another_agents_memory(scenario):
    agent_a = await scenario.agent("owned-del-a")
    mem = await agent_a.write_memory("memory to be owner-deleted")

    owner = await scenario.owner_agent()
    status = await owner.delete_memory_raw(mem["id"])
    assert status == 204


async def test_owner_completes_another_agents_task(scenario):
    agent_a = await scenario.agent("task-creator-a")
    task = await agent_a.create_task("task to be owner-completed")
    await agent_a.claim_task(task["id"])

    owner = await scenario.owner_agent()
    status, body = await owner.complete_task_raw(task["id"])
    assert status == 200
    assert body["status"] == "completed"


async def test_regular_agent_cannot_complete_others_task(scenario):
    agent_a = await scenario.agent("task-creator-x")
    agent_b = await scenario.agent("task-intruder-y")

    task = await agent_a.create_task("task claimed by A")
    await agent_a.claim_task(task["id"])

    status, _ = await agent_b.complete_task_raw(task["id"])
    assert status == 403


# ── Group 5: directive lifecycle ──────────────────────────────────────────────


async def test_directive_survives_type_filter(scenario):
    owner = await scenario.owner_agent()
    agent = await scenario.agent("lifecycle-agent")

    mem = await agent.write_memory("a plain memory")
    _, doc_body = await owner.write_memory_raw({"content": "a doc entry", "type": "doc"})
    _, dir_body = await owner.write_memory_raw(
        {"content": "a directive entry", "type": "directive"}
    )

    all_entries = await agent.list_memory()
    all_ids = [e["id"] for e in all_entries]
    assert mem["id"] in all_ids
    assert doc_body["id"] in all_ids
    assert dir_body["id"] in all_ids

    memories = await agent.list_memory(type="memory")
    memory_ids = [e["id"] for e in memories]
    assert mem["id"] in memory_ids
    assert doc_body["id"] not in memory_ids
    assert dir_body["id"] not in memory_ids

    docs = await agent.list_memory(type="doc")
    doc_ids = [e["id"] for e in docs]
    assert doc_body["id"] in doc_ids
    assert mem["id"] not in doc_ids
    assert dir_body["id"] not in doc_ids

    directives = await agent.list_memory(type="directive")
    directive_ids = [e["id"] for e in directives]
    assert dir_body["id"] in directive_ids
    assert mem["id"] not in directive_ids
    assert doc_body["id"] not in directive_ids


async def test_directive_search_includes_directives(scenario):
    owner = await scenario.owner_agent()
    _, directive = await owner.write_memory_raw(
        {
            "content": "xyzquux orchestration constraint: always verify checksums",
            "type": "directive",
        }
    )
    directive_id = directive["id"]

    results = await owner.search_memory("xyzquux orchestration constraint")
    result_ids = [r["id"] for r in results]
    assert directive_id in result_ids


async def test_multiple_directives_project_scoped(scenario):
    owner = await scenario.owner_agent()
    await owner.join_project("proj-directive-test")

    _, d1 = await owner.write_memory_raw(
        {
            "content": "directive one for project",
            "type": "directive",
            "project": "proj-directive-test",
        }
    )
    _, d2 = await owner.write_memory_raw(
        {
            "content": "directive two for project",
            "type": "directive",
            "project": "proj-directive-test",
        }
    )

    directives = await owner.list_memory(type="directive", project="proj-directive-test")
    directive_ids = [e["id"] for e in directives]
    assert d1["id"] in directive_ids
    assert d2["id"] in directive_ids
    assert len(directives) >= 2
