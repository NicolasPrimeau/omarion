import json
from unittest.mock import AsyncMock, MagicMock, patch

from artel.archivist.synthesis import on_task_completed, run_task_triage


def _make_client(
    task=None,
    search_results=None,
    tasks=None,
):
    client = MagicMock()
    client.get_task = AsyncMock(return_value=task or {})
    client.search_memory = AsyncMock(return_value=search_results or [])
    client.write_memory = AsyncMock(return_value={"id": "new-mem-id"})
    client.patch_memory = AsyncMock(return_value={})
    client.list_tasks = AsyncMock(return_value=tasks or [])
    client.add_task_comment = AsyncMock(return_value={"id": "comment-id"})
    client.log = AsyncMock()
    return client


def _make_task(
    task_id="task-abc",
    title="Add 3 more cities to BuildData",
    description="Expand city coverage",
    expected_outcome="63 cities total",
    project="nimbus",
    assigned_to=None,
    status="open",
):
    return {
        "id": task_id,
        "title": title,
        "description": description,
        "expected_outcome": expected_outcome,
        "project": project,
        "assigned_to": assigned_to,
        "status": status,
    }


def _make_memory(
    mem_id="mem-xyz",
    content="BuildData currently covers 60 cities",
    confidence=0.9,
    tags=None,
    agent_id="agent-a",
    project="nimbus",
):
    return {
        "id": mem_id,
        "content": content,
        "confidence": confidence,
        "tags": tags or [],
        "agent_id": agent_id,
        "project": project,
    }


class TestOnTaskCompletedPassiveMode:
    async def test_no_related_memory_writes_nothing(self):
        task = _make_task()
        client = _make_client(task=task, search_results=[])
        with patch("artel.archivist.synthesis.is_configured", return_value=False):
            await on_task_completed("task-abc", "agent-a", client)
        client.write_memory.assert_not_called()

    async def test_writes_generic_completion_entry_when_related(self):
        task = _make_task()
        mem = _make_memory()
        client = _make_client(task=task, search_results=[mem])
        with patch("artel.archivist.synthesis.is_configured", return_value=False):
            await on_task_completed("task-abc", "agent-a", client)
        client.write_memory.assert_called_once()
        call_kwargs = client.write_memory.call_args.kwargs
        assert "task-completion" in call_kwargs["tags"]
        assert task["title"] in call_kwargs["content"]

    async def test_task_fetch_failure_is_swallowed(self):
        client = _make_client()
        client.get_task = AsyncMock(side_effect=Exception("network error"))
        with patch("artel.archivist.synthesis.is_configured", return_value=False):
            await on_task_completed("task-abc", "agent-a", client)
        client.write_memory.assert_not_called()


class TestOnTaskCompletedLLMMode:
    async def test_extracts_facts_from_llm_response(self):
        task = _make_task()
        mem = _make_memory()
        client = _make_client(task=task, search_results=[mem])
        llm_response = json.dumps(
            {
                "facts": ["BuildData now covers 63 cities after expansion"],
                "update_ids": [],
            }
        )
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value=llm_response)),
        ):
            await on_task_completed("task-abc", "agent-a", client)

        client.write_memory.assert_called_once()
        call_kwargs = client.write_memory.call_args.kwargs
        assert "63 cities" in call_kwargs["content"]
        assert "archivist-extracted" in call_kwargs["tags"]

    async def test_updates_existing_memory_entry(self):
        task = _make_task()
        mem = _make_memory(mem_id="mem-xyz", content="BuildData covers 60 cities")
        client = _make_client(task=task, search_results=[mem])
        llm_response = json.dumps(
            {
                "facts": [],
                "update_ids": [{"id": "mem-xyz", "content": "BuildData covers 63 cities"}],
            }
        )
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value=llm_response)),
        ):
            await on_task_completed("task-abc", "agent-a", client)

        client.patch_memory.assert_called_once_with("mem-xyz", content="BuildData covers 63 cities")
        client.write_memory.assert_not_called()

    async def test_ignores_update_with_hallucinated_id(self):
        task = _make_task()
        mem = _make_memory(mem_id="mem-xyz")
        client = _make_client(task=task, search_results=[mem])
        llm_response = json.dumps(
            {
                "facts": [],
                "update_ids": [{"id": "hallucinated-id", "content": "some update"}],
            }
        )
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value=llm_response)),
        ):
            await on_task_completed("task-abc", "agent-a", client)

        client.patch_memory.assert_not_called()

    async def test_empty_llm_response_writes_nothing(self):
        task = _make_task()
        mem = _make_memory()
        client = _make_client(task=task, search_results=[mem])
        llm_response = json.dumps({"facts": [], "update_ids": []})
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value=llm_response)),
        ):
            await on_task_completed("task-abc", "agent-a", client)

        client.write_memory.assert_not_called()
        client.patch_memory.assert_not_called()

    async def test_unparseable_llm_response_is_swallowed(self):
        task = _make_task()
        mem = _make_memory()
        client = _make_client(task=task, search_results=[mem])
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value="not json at all")),
        ):
            await on_task_completed("task-abc", "agent-a", client)

        client.write_memory.assert_not_called()

    async def test_strips_markdown_fence_from_llm_response(self):
        task = _make_task()
        mem = _make_memory()
        client = _make_client(task=task, search_results=[mem])
        llm_response = '```json\n{"facts": ["A fact"], "update_ids": []}\n```'
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value=llm_response)),
        ):
            await on_task_completed("task-abc", "agent-a", client)

        client.write_memory.assert_called_once()


class TestRunTaskTriagePassiveMode:
    async def test_skips_claimed_tasks(self):
        claimed = _make_task(assigned_to="some-agent", status="open")
        unclaimed = _make_task(task_id="task-2", title="Another task")
        mem = _make_memory()
        client = _make_client(tasks=[claimed, unclaimed], search_results=[mem])
        with patch("artel.archivist.synthesis.is_configured", return_value=False):
            await run_task_triage(client)

        assert client.add_task_comment.call_count == 1
        commented_id = client.add_task_comment.call_args.args[0]
        assert commented_id == "task-2"

    async def test_no_unclaimed_tasks_does_nothing(self):
        claimed = _make_task(assigned_to="agent-x", status="open")
        client = _make_client(tasks=[claimed])
        with patch("artel.archivist.synthesis.is_configured", return_value=False):
            await run_task_triage(client)
        client.add_task_comment.assert_not_called()

    async def test_no_related_memory_skips_comment(self):
        task = _make_task()
        client = _make_client(tasks=[task], search_results=[])
        with patch("artel.archivist.synthesis.is_configured", return_value=False):
            await run_task_triage(client)
        client.add_task_comment.assert_not_called()

    async def test_passive_adds_link_comment_when_memory_found(self):
        task = _make_task()
        mem = _make_memory()
        client = _make_client(tasks=[task], search_results=[mem])
        with patch("artel.archivist.synthesis.is_configured", return_value=False):
            await run_task_triage(client)
        client.add_task_comment.assert_called_once()
        body = client.add_task_comment.call_args.args[1]
        assert "[archivist]" in body


class TestRunTaskTriageLLMMode:
    async def test_adds_link_comment_from_llm(self):
        task = _make_task()
        mem = _make_memory()
        client = _make_client(tasks=[task], search_results=[mem])
        llm_response = json.dumps(
            {
                "link_comment": "Related: mem-xyz describes current city count",
                "duplicate_of": None,
                "already_done": False,
            }
        )
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value=llm_response)),
        ):
            await run_task_triage(client)

        client.add_task_comment.assert_called_once()
        body = client.add_task_comment.call_args.args[1]
        assert "[archivist]" in body
        assert "mem-xyz" in body

    async def test_flags_duplicate_task(self):
        task = _make_task()
        mem = _make_memory()
        client = _make_client(tasks=[task], search_results=[mem])
        llm_response = json.dumps(
            {
                "link_comment": None,
                "duplicate_of": "Expand city coverage to 63",
                "already_done": False,
            }
        )
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value=llm_response)),
        ):
            await run_task_triage(client)

        client.add_task_comment.assert_called_once()
        body = client.add_task_comment.call_args.args[1]
        assert "duplicate" in body.lower()

    async def test_flags_already_done_task(self):
        task = _make_task()
        mem = _make_memory()
        client = _make_client(tasks=[task], search_results=[mem])
        llm_response = json.dumps(
            {
                "link_comment": None,
                "duplicate_of": None,
                "already_done": True,
            }
        )
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value=llm_response)),
        ):
            await run_task_triage(client)

        client.add_task_comment.assert_called_once()
        body = client.add_task_comment.call_args.args[1]
        assert "already" in body.lower() or "complete" in body.lower()

    async def test_multiple_comments_per_task(self):
        task = _make_task()
        mem = _make_memory()
        client = _make_client(tasks=[task], search_results=[mem])
        llm_response = json.dumps(
            {
                "link_comment": "See mem-xyz",
                "duplicate_of": "Other task",
                "already_done": True,
            }
        )
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value=llm_response)),
        ):
            await run_task_triage(client)

        assert client.add_task_comment.call_count == 3

    async def test_no_related_memory_skips_llm(self):
        task = _make_task()
        client = _make_client(tasks=[task], search_results=[])
        mock_complete = AsyncMock()
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.complete", mock_complete),
        ):
            await run_task_triage(client)

        mock_complete.assert_not_called()
        client.add_task_comment.assert_not_called()

    async def test_list_tasks_failure_is_swallowed(self):
        client = _make_client()
        client.list_tasks = AsyncMock(side_effect=Exception("db error"))
        with patch("artel.archivist.synthesis.is_configured", return_value=True):
            await run_task_triage(client)
        client.add_task_comment.assert_not_called()

    async def test_unparseable_llm_response_is_swallowed(self):
        task = _make_task()
        mem = _make_memory()
        client = _make_client(tasks=[task], search_results=[mem])
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value="not json")),
        ):
            await run_task_triage(client)
        client.add_task_comment.assert_not_called()
