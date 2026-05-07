import asyncio

import httpx
import pytest_asyncio
from httpx import ASGITransport

from tests.conftest import AGENT2, KEY2, TEST_AGENT, TEST_KEY


@pytest_asyncio.fixture
async def mcp(tmp_path, monkeypatch):
    import artel.mcp.server as mcp_mod
    import artel.server.broadcast as bc_mod
    import artel.server.config as cfg_mod
    import artel.store.db as db_mod

    db_mod._conn = None
    bc_mod._subscribers.clear()

    test_db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(cfg_mod.settings, "db_path", test_db_path)
    monkeypatch.setattr(cfg_mod.settings, "registration_key", "regkey")
    monkeypatch.setattr(mcp_mod.settings, "mcp_agent_id", TEST_AGENT)

    conn = db_mod.get_db(test_db_path)
    conn.execute("INSERT INTO agents (id, api_key) VALUES (?, ?)", (TEST_AGENT, TEST_KEY))
    conn.execute("INSERT INTO agents (id, api_key) VALUES (?, ?)", (AGENT2, KEY2))
    conn.commit()

    from artel.server.app import app

    def test_http():
        return httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"x-agent-id": TEST_AGENT, "x-api-key": TEST_KEY},
            timeout=30.0,
        )

    monkeypatch.setattr(mcp_mod, "_http", test_http)

    yield mcp_mod

    if db_mod._conn:
        db_mod._conn.close()
        db_mod._conn = None
    bc_mod._subscribers.clear()


def _extract_id(result: str) -> str:
    return result.split("[")[1].split("]")[0]


async def test_memory_write_returns_id(mcp):
    result = await mcp.memory_write("the sky is blue")
    assert result.startswith("written [")


async def test_memory_write_private_scope(mcp):
    result = await mcp.memory_write("secret thought", scope="agent")
    assert result.startswith("written [")


async def test_memory_get_full_content(mcp):
    write = await mcp.memory_write("full content here")
    entry_id = _extract_id(write)
    result = await mcp.memory_get(entry_id)
    assert "full content here" in result
    assert entry_id in result
    assert "conf=" in result


async def test_memory_get_not_found(mcp):
    result = await mcp.memory_get("00000000-0000-0000-0000-000000000000")
    assert result.startswith("error 404")


async def test_memory_search_returns_results(mcp):
    await mcp.memory_write("python is a programming language")
    result = await mcp.memory_search("programming")
    assert result != "No results."
    assert "python is a programming language" in result


async def test_memory_search_no_results(mcp):
    result = await mcp.memory_search("xyzzy nonexistent query 12345")
    assert result == "No results."


async def test_memory_delta(mcp):
    await mcp.memory_write("delta entry")
    result = await mcp.memory_delta("1970-01-01T00:00:00.000Z")
    assert result != "No changes."
    assert "delta entry" in result


async def test_memory_delta_empty(mcp):
    result = await mcp.memory_delta("2099-01-01T00:00:00.000Z")
    assert result == "No changes."


async def test_task_get_by_id(mcp):
    create = await mcp.task_create("detailed task", description="lots of detail here")
    task_id = _extract_id(create)
    result = await mcp.task_get(task_id)
    assert task_id in result
    assert "detailed task" in result
    assert "lots of detail here" in result
    assert "created by:" in result


async def test_task_get_not_found(mcp):
    result = await mcp.task_get("00000000-0000-0000-0000-000000000000")
    assert result.startswith("error 404")


async def test_task_create_returns_summary(mcp):
    result = await mcp.task_create("fix the login bug", priority="high")
    assert result.startswith("created [")
    assert "high" in result
    assert "fix the login bug" in result


async def test_task_claim_returns_title(mcp):
    create = await mcp.task_create("claimable task")
    task_id = _extract_id(create)
    result = await mcp.task_claim(task_id)
    assert result.startswith("claimed [")
    assert "claimable task" in result


async def test_task_complete_returns_title(mcp):
    create = await mcp.task_create("completable task")
    task_id = _extract_id(create)
    await mcp.task_claim(task_id)
    result = await mcp.task_complete(task_id)
    assert result.startswith("completed [")
    assert "completable task" in result


async def test_task_fail_returns_title(mcp):
    create = await mcp.task_create("failable task")
    task_id = _extract_id(create)
    await mcp.task_claim(task_id)
    result = await mcp.task_fail(task_id)
    assert result.startswith("failed [")
    assert "failable task" in result


async def test_task_list_project_filter(mcp):
    await mcp.task_create("in proj-a", project="proj-a")
    await mcp.task_create("no project")
    result = await mcp.task_list(project="proj-a")
    assert "in proj-a" in result
    assert "no project" not in result


async def test_task_list_status_filter(mcp):
    await mcp.task_create("open task")
    result = await mcp.task_list(status="open")
    assert "open task" in result
    result_completed = await mcp.task_list(status="completed")
    assert "open task" not in result_completed


async def test_task_list_empty(mcp):
    result = await mcp.task_list(status="completed")
    assert result == "No tasks."


async def test_task_claim_not_found(mcp):
    result = await mcp.task_claim("00000000-0000-0000-0000-000000000000")
    assert result.startswith("error 404")


async def test_task_claim_already_claimed(mcp):
    create = await mcp.task_create("double-claim")
    task_id = _extract_id(create)
    await mcp.task_claim(task_id)
    result = await mcp.task_claim(task_id)
    assert result.startswith("error 409")


async def test_send_message_returns_confirmation(mcp):
    result = await mcp.message_send(to=AGENT2, body="hello", subject="greet")
    assert result.startswith("sent to")
    assert AGENT2 in result


async def test_read_inbox_empty(mcp):
    result = await mcp.message_inbox()
    assert result == "No unread messages."


async def test_list_participants(mcp):
    result = await mcp.agent_list()
    assert TEST_AGENT in result
    assert AGENT2 in result


async def test_session_context_no_args_uses_own_id(mcp):
    result = await mcp.session_context()
    assert "No previous session" in result
    assert "error" not in result


async def test_session_context_explicit_agent_id(mcp):
    result = await mcp.session_context(agent_id=TEST_AGENT)
    assert "No previous session" in result


async def test_session_handoff_and_context(mcp):
    handoff = await mcp.session_handoff(
        summary="finished the auth refactor",
        next_steps=["deploy to prod", "monitor errors"],
        in_progress=["task-123"],
    )
    assert handoff.startswith("handoff saved [")

    context = await mcp.session_context()
    assert "finished the auth refactor" in context
    assert "deploy to prod" in context
    assert "monitor errors" in context


async def test_session_context_includes_memory_delta(mcp):
    await mcp.session_handoff(summary="first session")
    await asyncio.sleep(0.005)
    await mcp.memory_write("something new after handoff")

    context = await mcp.session_context()
    assert "something new after handoff" in context


async def test_agent_delete_self(mcp):
    import artel.store.db as db_mod

    result = await mcp.agent_delete()
    assert "deregistered" in result
    assert "credentials" in result
    row = db_mod.get_db().execute("SELECT id FROM agents WHERE id=?", (TEST_AGENT,)).fetchone()
    assert row is None


async def test_agent_delete_removes_from_participants(mcp):
    await mcp.agent_delete()
    result = await mcp.agent_list()
    assert TEST_AGENT not in result
