from unittest.mock import AsyncMock, patch

import pytest_asyncio
from httpx import AsyncClient

import artel.store.db as db_mod
from artel.archivist.synthesis import on_task_completed, run_synthesis, run_task_triage

ARCHIVIST_ID = "test-archivist"


class _ScenarioArchivistClient:
    def __init__(self, http: AsyncClient):
        self._http = http

    async def get_directives(self, project=None):
        params = {"type": "directive", "scope": "project", "limit": 200}
        if project:
            params["project"] = project
        r = await self._http.get("/memory", params=params)
        r.raise_for_status()
        results = list(r.json())
        r2 = await self._http.get(
            "/memory", params={"type": "directive", "scope": "agent", "limit": 200}
        )
        r2.raise_for_status()
        results.extend(r2.json())
        return results

    async def get_delta(self, since: str) -> list[dict]:
        r = await self._http.get("/memory/delta", params={"since": since})
        r.raise_for_status()
        return r.json()

    async def list_tasks(self, status=None, limit=50) -> list[dict]:
        params = {"limit": limit}
        if status:
            params["status"] = status
        r = await self._http.get("/tasks", params=params)
        r.raise_for_status()
        return r.json()

    async def write_memory(
        self, content, type="doc", tags=None, parents=None, confidence=1.0, project=None
    ) -> dict:
        r = await self._http.post(
            "/memory",
            json={
                "content": content,
                "type": type,
                "scope": "project",
                "tags": tags or [],
                "parents": parents or [],
                "confidence": confidence,
                "project": project,
            },
        )
        r.raise_for_status()
        return r.json()

    async def patch_memory(self, entry_id: str, **fields) -> dict:
        r = await self._http.patch(f"/memory/{entry_id}", json=fields)
        r.raise_for_status()
        return r.json()

    async def delete_memory(self, entry_id: str) -> None:
        r = await self._http.delete(f"/memory/{entry_id}")
        r.raise_for_status()

    async def get_memory(self, entry_id: str) -> dict:
        r = await self._http.get(f"/memory/{entry_id}")
        r.raise_for_status()
        return r.json()

    async def create_task(self, title, description=None, priority="normal", project=None) -> dict:
        r = await self._http.post(
            "/tasks",
            json={
                "title": title,
                "description": description or "",
                "priority": priority,
                "project": project,
            },
        )
        r.raise_for_status()
        return r.json()

    async def search_memory(
        self, q: str, limit: int = 10, max_distance: float | None = None
    ) -> list[dict]:
        params: dict = {"q": q, "limit": limit}
        if max_distance is not None:
            params["max_distance"] = max_distance
        r = await self._http.get("/memory/search", params=params)
        r.raise_for_status()
        return r.json()

    async def get_task(self, task_id: str) -> dict:
        r = await self._http.get(f"/tasks/{task_id}")
        r.raise_for_status()
        return r.json()

    async def add_task_comment(self, task_id: str, body: str) -> dict:
        r = await self._http.post(f"/tasks/{task_id}/comments", json={"body": body})
        r.raise_for_status()
        return r.json()

    async def list_task_comments(self, task_id: str) -> list[dict]:
        r = await self._http.get(f"/tasks/{task_id}/comments")
        r.raise_for_status()
        return r.json()

    async def send_message(self, to, subject, body) -> dict:
        r = await self._http.post("/messages", json={"to": to, "subject": subject, "body": body})
        r.raise_for_status()
        return r.json()


@pytest_asyncio.fixture
async def arch_scenario(scenario):
    r = await scenario._admin.post("/agents/register", json={"agent_id": ARCHIVIST_ID})
    r.raise_for_status()
    api_key = r.json()["api_key"]
    db = db_mod.get_db()
    db.execute("UPDATE agents SET role='owner' WHERE id=?", (ARCHIVIST_ID,))
    db.commit()
    http = AsyncClient(
        transport=scenario._transport,
        base_url="http://test",
        headers={"x-agent-id": ARCHIVIST_ID, "x-api-key": api_key},
    )
    client = _ScenarioArchivistClient(http)
    yield scenario, client, http
    await http.aclose()


async def _run_synthesis_mocked(client, llm_response: str, decay_floor: float = 0.05):
    with (
        patch("artel.archivist.synthesis.is_configured", return_value=True),
        patch("artel.archivist.synthesis.settings") as mock_settings,
        patch("artel.archivist.synthesis.complete", new=AsyncMock(return_value=llm_response)),
    ):
        mock_settings.archivist_id = ARCHIVIST_ID
        mock_settings.directive_conflict_threshold = 0.85
        mock_settings.decay_floor = decay_floor
        await run_synthesis(client)


async def test_curator_merge_op(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("merge-a")
    agent_b = await scenario.agent("merge-b")

    mem_a = await agent_a.write_memory("Service X uses OAuth2 for auth", tags=["auth"])
    mem_b = await agent_b.write_memory(
        "Service X authenticates via OAuth2", tags=["auth", "security"]
    )

    llm_response = f'[{{"op":"merge","entries":["{mem_a["id"]}","{mem_b["id"]}"],"merged_content":"Service X uses OAuth2 for authentication and authorization"}}]'
    await _run_synthesis_mocked(arch_client, llm_response)

    all_entries = await agent_a.list_memory()
    ids = [e["id"] for e in all_entries]
    assert mem_a["id"] not in ids
    assert mem_b["id"] not in ids

    merged = [
        e for e in all_entries if e.get("agent_id") == ARCHIVIST_ID and e.get("type") == "memory"
    ]
    assert len(merged) == 1
    merged_entry = merged[0]
    assert "OAuth2" in merged_entry["content"]
    assert set(merged_entry.get("parents", [])) == {mem_a["id"], mem_b["id"]}
    merged_tags = set(merged_entry.get("tags", []))
    assert "auth" in merged_tags
    assert "security" in merged_tags


async def test_curator_promote_op(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("promoter-a")
    agent_b = await scenario.agent("promoter-b")

    mem = await agent_a.write_memory("The DB uses WAL mode for concurrent reads", tags=["database"])
    await agent_b.write_memory("Second entry to satisfy two-entry threshold")
    assert mem["type"] == "memory"

    llm_response = f'[{{"op":"promote","entry":"{mem["id"]}"}}]'
    await _run_synthesis_mocked(arch_client, llm_response)

    updated = await agent_a.get_memory(mem["id"])
    assert updated["type"] == "doc"


async def test_curator_prune_op(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("pruner-a")
    agent_b = await scenario.agent("pruner-b")

    mem_keep = await agent_a.write_memory("Stable fact that should survive")
    mem_prune = await agent_b.write_memory("Stale and superseded finding", confidence=0.05)

    llm_response = f'[{{"op":"prune","entry":"{mem_prune["id"]}"}}]'
    await _run_synthesis_mocked(arch_client, llm_response)

    all_entries = await agent_a.list_memory()
    ids = [e["id"] for e in all_entries]
    assert mem_prune["id"] not in ids
    assert mem_keep["id"] in ids


async def test_curator_tag_op(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("tagger-a")
    agent_b = await scenario.agent("tagger-b")

    mem_a = await agent_a.write_memory("Entry that will get tagged", tags=["existing"])
    await agent_b.write_memory("Second entry to satisfy two-entry threshold")

    llm_response = f'[{{"op":"tag","entry":"{mem_a["id"]}","add_tags":["new-tag"]}}]'
    await _run_synthesis_mocked(arch_client, llm_response)

    updated = await agent_a.get_memory(mem_a["id"])
    tags = set(updated.get("tags", []))
    assert "existing" in tags
    assert "new-tag" in tags


async def test_curator_adjust_confidence_op(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("confidence-a")
    agent_b = await scenario.agent("confidence-b")

    mem_a = await agent_a.write_memory("High-confidence entry to be adjusted", confidence=0.9)
    await agent_b.write_memory("Second entry to satisfy two-entry threshold")

    llm_response = f'[{{"op":"adjust_confidence","entry":"{mem_a["id"]}","confidence":0.4}}]'
    await _run_synthesis_mocked(arch_client, llm_response)

    updated = await agent_a.get_memory(mem_a["id"])
    assert abs(updated["confidence"] - 0.4) < 0.001


async def test_curator_task_op(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("task-creator-a")
    agent_b = await scenario.agent("task-creator-b")

    await agent_a.write_memory("Observation A")
    await agent_b.write_memory("Observation B")

    llm_response = '[{"op":"task","title":"Investigate gap","description":"A gap was found in coverage","priority":"high","project":null}]'
    await _run_synthesis_mocked(arch_client, llm_response)

    tasks = await agent_a.list_tasks()
    assert any(t["title"] == "Investigate gap" for t in tasks)
    task = next(t for t in tasks if t["title"] == "Investigate gap")
    assert task["priority"] == "high"


async def test_curator_no_synthesis_doc(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("nodoc-a")
    agent_b = await scenario.agent("nodoc-b")

    mem_a = await agent_a.write_memory("First memory entry")
    await agent_b.write_memory("Second memory entry")

    llm_response = f'[{{"op":"prune","entry":"{mem_a["id"]}"}}]'
    await _run_synthesis_mocked(arch_client, llm_response)

    all_entries = await agent_a.list_memory()
    archivist_docs = [
        e for e in all_entries if e.get("agent_id") == ARCHIVIST_ID and e.get("type") == "doc"
    ]
    assert archivist_docs == []


async def test_curator_hallucinated_id_skipped(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("halluc-a")
    agent_b = await scenario.agent("halluc-b")

    await agent_a.write_memory("Valid entry one")
    await agent_b.write_memory("Valid entry two")

    entries_before = await agent_a.list_memory()
    count_before = len(entries_before)

    llm_response = '[{"op":"promote","entry":"fake-id-that-does-not-exist"}]'
    await _run_synthesis_mocked(arch_client, llm_response)

    entries_after = await agent_a.list_memory()
    assert len(entries_after) == count_before


async def test_curator_multiple_ops(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("multi-a")
    agent_b = await scenario.agent("multi-b")

    mem_a = await agent_a.write_memory("Stable finding worth promoting", tags=["core"])
    mem_b = await agent_b.write_memory("Another finding to tag")

    llm_response = (
        f"["
        f'{{"op":"promote","entry":"{mem_a["id"]}"}},'
        f'{{"op":"tag","entry":"{mem_b["id"]}","add_tags":["reviewed"]}},'
        f'{{"op":"task","title":"Follow-up research","priority":"normal","project":null}}'
        f"]"
    )
    await _run_synthesis_mocked(arch_client, llm_response)

    promoted = await agent_a.get_memory(mem_a["id"])
    assert promoted["type"] == "doc"

    tagged = await agent_b.get_memory(mem_b["id"])
    assert "reviewed" in tagged.get("tags", [])

    tasks = await agent_a.list_tasks()
    assert any(t["title"] == "Follow-up research" for t in tasks)


async def test_curator_malformed_json(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("malform-a")
    agent_b = await scenario.agent("malform-b")

    await agent_a.write_memory("Entry one")
    await agent_b.write_memory("Entry two")

    entries_before = await agent_a.list_memory()
    count_before = len(entries_before)

    await _run_synthesis_mocked(arch_client, "not json")

    entries_after = await agent_a.list_memory()
    assert len(entries_after) == count_before


async def test_curator_empty_ops(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("empty-a")
    agent_b = await scenario.agent("empty-b")

    mem_a = await agent_a.write_memory("Will not be touched")
    mem_b = await agent_b.write_memory("Also untouched")

    await _run_synthesis_mocked(arch_client, "[]")

    still_a = await agent_a.get_memory(mem_a["id"])
    assert still_a["type"] == "memory"
    still_b = await agent_b.get_memory(mem_b["id"])
    assert still_b["type"] == "memory"


async def test_curator_directives_loaded_as_preamble(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    owner = await scenario.owner_agent()
    await owner.write_memory_raw(
        {"content": "always tag security findings with sec-critical", "type": "directive"}
    )

    agent_a = await scenario.agent("preamble-a")
    agent_b = await scenario.agent("preamble-b")

    await agent_a.write_memory("Security observation one")
    await agent_b.write_memory("Security observation two")

    captured_system = []

    async def capture_complete(system, user, max_tokens):
        captured_system.append(system)
        return "[]"

    with (
        patch("artel.archivist.synthesis.is_configured", return_value=True),
        patch("artel.archivist.synthesis.settings") as mock_settings,
        patch("artel.archivist.synthesis.complete", new=capture_complete),
    ):
        mock_settings.archivist_id = ARCHIVIST_ID
        mock_settings.directive_conflict_threshold = 0.85
        await run_synthesis(arch_client)

    assert len(captured_system) == 1
    system_prompt = captured_system[0]
    assert "--- STANDING DIRECTIVES ---" in system_prompt
    assert "always tag security findings with sec-critical" in system_prompt
    assert "--- END DIRECTIVES ---" in system_prompt


async def test_curator_split_op(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("split-a")
    agent_b = await scenario.agent("split-b")

    mem = await agent_a.write_memory(
        "Python is used for the backend. Postgres is used for storage.", tags=["tech"]
    )
    await agent_b.write_memory("Second entry to satisfy two-entry threshold")

    llm_response = (
        f'[{{"op":"split","entry":"{mem["id"]}",'
        f'"parts":['
        f'{{"content":"Python is used for the backend","tags":["backend"]}},'
        f'{{"content":"Postgres is used for storage","tags":["storage"]}}'
        f"]}}]"
    )
    await _run_synthesis_mocked(arch_client, llm_response)

    all_entries = await agent_a.list_memory()
    ids = [e["id"] for e in all_entries]
    assert mem["id"] not in ids

    archivist_entries = [e for e in all_entries if e.get("agent_id") == ARCHIVIST_ID]
    assert len(archivist_entries) == 2
    for entry in archivist_entries:
        assert mem["id"] in entry.get("parents", [])
        assert "tech" in entry.get("tags", [])


async def test_curator_extract_op_with_remaining(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("extract-a")
    agent_b = await scenario.agent("extract-b")

    mem_from = await agent_a.write_memory("API uses REST. DB uses WAL mode.", tags=["infra"])
    mem_into = await agent_b.write_memory("DB is Postgres.", tags=["db"])

    llm_response = (
        f'[{{"op":"extract",'
        f'"from":"{mem_from["id"]}",'
        f'"into":"{mem_into["id"]}",'
        f'"extracted_content":"DB uses WAL mode.",'
        f'"remaining_content":"API uses REST.",'
        f'"merged_content":"DB is Postgres and uses WAL mode."}}]'
    )
    await _run_synthesis_mocked(arch_client, llm_response)

    updated_from = await agent_a.get_memory(mem_from["id"])
    assert updated_from["content"] == "API uses REST."

    updated_into = await agent_b.get_memory(mem_into["id"])
    assert updated_into["content"] == "DB is Postgres and uses WAL mode."


async def test_curator_extract_op_deletes_source(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("extract-del-a")
    agent_b = await scenario.agent("extract-del-b")

    mem_from = await agent_a.write_memory("Entire content belongs elsewhere.", tags=["misc"])
    mem_into = await agent_b.write_memory("Target entry.", tags=["core"])

    llm_response = (
        f'[{{"op":"extract",'
        f'"from":"{mem_from["id"]}",'
        f'"into":"{mem_into["id"]}",'
        f'"extracted_content":"Entire content belongs elsewhere.",'
        f'"remaining_content":"",'
        f'"merged_content":"Target entry. Entire content belongs elsewhere."}}]'
    )
    await _run_synthesis_mocked(arch_client, llm_response)

    all_entries = await agent_a.list_memory()
    ids = [e["id"] for e in all_entries]
    assert mem_from["id"] not in ids

    updated_into = await agent_b.get_memory(mem_into["id"])
    assert updated_into["content"] == "Target entry. Entire content belongs elsewhere."


async def test_curator_prune_flags_high_confidence(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("flag-a")
    agent_b = await scenario.agent("flag-b")

    mem = await agent_a.write_memory("Entry with high confidence", confidence=0.8)
    await agent_b.write_memory("Second entry to satisfy two-entry threshold")

    llm_response = f'[{{"op":"prune","entry":"{mem["id"]}"}}]'
    await _run_synthesis_mocked(arch_client, llm_response)

    updated = await agent_a.get_memory(mem["id"])
    assert abs(updated["confidence"] - 0.05) < 0.001
    assert "archivist-flagged" in updated.get("tags", [])


async def test_curator_prune_deletes_at_floor(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("floor-del-a")
    agent_b = await scenario.agent("floor-del-b")

    mem_keep = await agent_a.write_memory("Stable entry that survives")
    mem_del = await agent_b.write_memory("Entry already at decay floor", confidence=0.05)

    llm_response = f'[{{"op":"prune","entry":"{mem_del["id"]}"}}]'
    await _run_synthesis_mocked(arch_client, llm_response)

    all_entries = await agent_a.list_memory()
    ids = [e["id"] for e in all_entries]
    assert mem_del["id"] not in ids
    assert mem_keep["id"] in ids


async def _run_triage_mocked(client, llm_response: str):
    with (
        patch("artel.archivist.synthesis.is_configured", return_value=True),
        patch("artel.archivist.synthesis.settings") as mock_settings,
        patch("artel.archivist.synthesis.complete", new=AsyncMock(return_value=llm_response)),
    ):
        mock_settings.archivist_id = ARCHIVIST_ID
        await run_task_triage(client)


async def _run_triage_passive(client):
    with patch("artel.archivist.synthesis.is_configured", return_value=False):
        await run_task_triage(client)


async def _run_on_task_completed_mocked(client, task_id: str, agent_id: str, llm_response: str):
    with (
        patch("artel.archivist.synthesis.is_configured", return_value=True),
        patch("artel.archivist.synthesis.complete", new=AsyncMock(return_value=llm_response)),
    ):
        await on_task_completed(task_id, agent_id, client)


async def _run_on_task_completed_passive(client, task_id: str, agent_id: str):
    with patch("artel.archivist.synthesis.is_configured", return_value=False):
        await on_task_completed(task_id, agent_id, client)


async def test_triage_passive_comments_on_related_memory(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("triage-passive-a")
    task = await agent_a.create_task("Add 3 more cities to BuildData")
    await agent_a.write_memory("BuildData currently covers 60 cities")

    await _run_triage_passive(arch_client)

    comments = await arch_client.list_task_comments(task["id"])
    archivist_comments = [c for c in comments if c["agent_id"] == ARCHIVIST_ID]
    assert len(archivist_comments) == 1
    assert "[archivist]" in archivist_comments[0]["body"]


async def test_triage_skips_claimed_tasks(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("triage-skip-a")
    agent_b = await scenario.agent("triage-skip-b")

    claimed_task = await agent_a.create_task("Claimed task — should be skipped")
    await agent_a.claim_task(claimed_task["id"])

    open_task = await agent_b.create_task("Open unclaimed task")
    await agent_b.write_memory("Some relevant knowledge")

    await _run_triage_passive(arch_client)

    claimed_comments = await arch_client.list_task_comments(claimed_task["id"])
    open_comments = await arch_client.list_task_comments(open_task["id"])

    assert not any(c["agent_id"] == ARCHIVIST_ID for c in claimed_comments)
    assert any(c["agent_id"] == ARCHIVIST_ID for c in open_comments)


async def test_triage_no_comment_when_no_memory(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("triage-empty-a")
    task = await agent_a.create_task("Task with no related memory")

    await _run_triage_passive(arch_client)

    comments = await arch_client.list_task_comments(task["id"])
    assert not any(c["agent_id"] == ARCHIVIST_ID for c in comments)


async def test_triage_llm_link_comment(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("triage-llm-link-a")
    task = await agent_a.create_task("Expand coverage to new cities")
    mem = await agent_a.write_memory("BuildData covers 60 cities across Canada")

    mem_id_short = mem["id"][:8]
    llm_response = f'{{"link_comment": "See {mem_id_short}: current city count", "duplicate_of": null, "already_done": false}}'
    await _run_triage_mocked(arch_client, llm_response)

    comments = await arch_client.list_task_comments(task["id"])
    archivist_comments = [c for c in comments if c["agent_id"] == ARCHIVIST_ID]
    assert len(archivist_comments) == 1
    assert "[archivist]" in archivist_comments[0]["body"]


async def test_triage_llm_duplicate_flag(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("triage-dup-a")
    task = await agent_a.create_task("Add cities to BuildData")
    await agent_a.write_memory("BuildData city expansion was tracked before")

    llm_response = '{"link_comment": null, "duplicate_of": "Expand coverage to new cities", "already_done": false}'
    await _run_triage_mocked(arch_client, llm_response)

    comments = await arch_client.list_task_comments(task["id"])
    archivist_comments = [c for c in comments if c["agent_id"] == ARCHIVIST_ID]
    assert len(archivist_comments) == 1
    assert "duplicate" in archivist_comments[0]["body"].lower()


async def test_triage_llm_already_done_flag(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("triage-done-a")
    task = await agent_a.create_task("Set up CI pipeline")
    await agent_a.write_memory("CI pipeline using GitHub Actions is fully configured and passing")

    llm_response = '{"link_comment": null, "duplicate_of": null, "already_done": true}'
    await _run_triage_mocked(arch_client, llm_response)

    comments = await arch_client.list_task_comments(task["id"])
    archivist_comments = [c for c in comments if c["agent_id"] == ARCHIVIST_ID]
    assert len(archivist_comments) == 1
    body = archivist_comments[0]["body"].lower()
    assert "already" in body or "complete" in body


async def test_on_task_completed_passive_writes_observation(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("tc-passive-a")
    task = await agent_a.create_task("Deploy new service")
    await agent_a.claim_task(task["id"])
    await agent_a.write_memory("Service deployment checklist: tests, migration, rollout")

    await _run_on_task_completed_passive(arch_client, task["id"], agent_a.id)

    all_entries = await agent_a.list_memory()
    completion_entries = [
        e
        for e in all_entries
        if e["agent_id"] == ARCHIVIST_ID and "task-completion" in e.get("tags", [])
    ]
    assert len(completion_entries) == 1
    assert task["title"] in completion_entries[0]["content"]


async def test_on_task_completed_llm_extracts_fact(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("tc-llm-a")
    task = await agent_a.create_task(
        "Add 3 more cities to BuildData", description="Expand to 63 cities"
    )
    await agent_a.claim_task(task["id"])
    await agent_a.write_memory("BuildData covers 60 cities")

    import json as _json

    llm_response = _json.dumps(
        {
            "facts": ["BuildData now covers 63 cities after the expansion task completed"],
            "update_ids": [],
        }
    )
    await _run_on_task_completed_mocked(arch_client, task["id"], agent_a.id, llm_response)

    all_entries = await agent_a.list_memory()
    extracted = [
        e
        for e in all_entries
        if e["agent_id"] == ARCHIVIST_ID and "archivist-extracted" in e.get("tags", [])
    ]
    assert len(extracted) == 1
    assert "63 cities" in extracted[0]["content"]


async def test_on_task_completed_llm_updates_existing_memory(arch_scenario):
    scenario, arch_client, arch_http = arch_scenario

    agent_a = await scenario.agent("tc-update-a")
    task = await agent_a.create_task("Migrate auth to OAuth2")
    await agent_a.claim_task(task["id"])
    mem = await agent_a.write_memory("Auth system uses session cookies")

    import json as _json

    llm_response = _json.dumps(
        {
            "facts": [],
            "update_ids": [
                {
                    "id": mem["id"],
                    "content": "Auth system uses OAuth2 (migrated from session cookies)",
                }
            ],
        }
    )
    await _run_on_task_completed_mocked(arch_client, task["id"], agent_a.id, llm_response)

    updated = await agent_a.get_memory(mem["id"])
    assert "OAuth2" in updated["content"]
