from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from artel.archivist import conflict, synthesis
from artel.archivist.llm import _api_key, _default_model, is_configured
from artel.archivist.synthesis import _execute_operations, _parse_operations


class TestLlmConfig:
    def test_not_configured_when_no_keys(self):
        with patch("artel.archivist.llm.settings") as s:
            s.archivist_api_key = ""
            s.archivist_provider = "anthropic"
            s.anthropic_api_key = ""
            assert not is_configured()

    def test_configured_via_anthropic_key(self):
        with patch("artel.archivist.llm.settings") as s:
            s.archivist_api_key = ""
            s.archivist_provider = "anthropic"
            s.anthropic_api_key = "sk-ant-test"
            assert is_configured()

    def test_configured_via_dedicated_key(self):
        with patch("artel.archivist.llm.settings") as s:
            s.archivist_api_key = "test-key"
            s.archivist_provider = "openai"
            s.anthropic_api_key = ""
            assert is_configured()

    def test_not_configured_openai_without_key(self):
        with patch("artel.archivist.llm.settings") as s:
            s.archivist_api_key = ""
            s.archivist_provider = "openai"
            s.anthropic_api_key = "sk-ant-test"
            assert not is_configured()

    def test_dedicated_key_takes_priority(self):
        with patch("artel.archivist.llm.settings") as s:
            s.archivist_api_key = "dedicated"
            s.archivist_provider = "anthropic"
            s.anthropic_api_key = "fallback"
            assert _api_key() == "dedicated"

    def test_anthropic_fallback_key(self):
        with patch("artel.archivist.llm.settings") as s:
            s.archivist_api_key = ""
            s.archivist_provider = "anthropic"
            s.anthropic_api_key = "fallback"
            assert _api_key() == "fallback"

    def test_default_model_anthropic(self):
        with patch("artel.archivist.llm.settings") as s:
            s.archivist_provider = "anthropic"
            assert _default_model() == "claude-sonnet-4-6"

    def test_default_model_openai(self):
        with patch("artel.archivist.llm.settings") as s:
            s.archivist_provider = "openai"
            assert _default_model() == "gpt-4o"


class TestPassiveMode:
    @pytest.fixture
    def artel_client(self):
        client = MagicMock()
        client.get_memory = AsyncMock()
        client.search_memory = AsyncMock(return_value=[])
        client.get_delta = AsyncMock(return_value=[])
        return client

    async def test_synthesis_skips_when_not_configured(self, artel_client):
        with patch("artel.archivist.synthesis.is_configured", return_value=False):
            await synthesis.run_synthesis(artel_client)
            artel_client.get_delta.assert_not_called()

    async def test_conflict_skips_when_not_configured(self, artel_client):
        with patch("artel.archivist.conflict.is_configured", return_value=False):
            await conflict.check_and_merge("some-id", artel_client)
            artel_client.get_memory.assert_not_called()

    async def test_synthesis_skips_with_fewer_than_two_entries(self, artel_client):
        artel_client.get_delta = AsyncMock(
            return_value=[
                {
                    "agent_id": "agent-a",
                    "id": "1",
                    "type": "memory",
                    "content": "x",
                    "project": None,
                }
            ]
        )
        with patch("artel.archivist.synthesis.is_configured", return_value=True):
            await synthesis.run_synthesis(artel_client)
            artel_client.write_memory = AsyncMock()
            artel_client.write_memory.assert_not_called()

    async def test_conflict_skips_when_no_conflicts(self, artel_client):
        artel_client.get_memory = AsyncMock(
            return_value={
                "id": "a",
                "agent_id": "agent-a",
                "content": "hello",
                "tags": [],
                "type": "memory",
                "project": None,
                "parents": [],
            }
        )
        artel_client.search_memory = AsyncMock(return_value=[])
        with patch("artel.archivist.conflict.is_configured", return_value=True):
            await conflict.check_and_merge("a", artel_client)
            artel_client.write_memory = AsyncMock()
            artel_client.write_memory.assert_not_called()


class TestParseOperations:
    def test_valid_json_array(self):
        text = '[{"op": "prune", "entry": "abc123"}]'
        ops = _parse_operations(text)
        assert len(ops) == 1
        assert ops[0]["op"] == "prune"

    def test_multiple_ops(self):
        text = '[{"op": "prune", "entry": "a"}, {"op": "promote", "entry": "b"}, {"op": "task", "title": "do something", "priority": "high"}]'
        ops = _parse_operations(text)
        assert len(ops) == 3

    def test_malformed_json_returns_empty(self):
        ops = _parse_operations("this is not json at all")
        assert ops == []

    def test_not_an_array_returns_empty(self):
        ops = _parse_operations('{"op": "prune", "entry": "a"}')
        assert ops == []

    def test_unknown_ops_are_skipped(self):
        text = '[{"op": "prune", "entry": "a"}, {"op": "summarize", "entry": "b"}]'
        ops = _parse_operations(text)
        assert len(ops) == 1
        assert ops[0]["op"] == "prune"

    def test_all_unknown_ops_returns_empty(self):
        text = '[{"op": "report"}, {"op": "write_doc"}]'
        ops = _parse_operations(text)
        assert ops == []

    def test_markdown_fenced_json(self):
        text = '```json\n[{"op": "promote", "entry": "xyz"}]\n```'
        ops = _parse_operations(text)
        assert len(ops) == 1
        assert ops[0]["op"] == "promote"

    def test_markdown_fenced_no_language(self):
        text = '```\n[{"op": "prune", "entry": "abc"}]\n```'
        ops = _parse_operations(text)
        assert len(ops) == 1
        assert ops[0]["op"] == "prune"

    def test_empty_array(self):
        ops = _parse_operations("[]")
        assert ops == []

    def test_non_dict_items_skipped(self):
        text = '[{"op": "prune", "entry": "a"}, "bad_item", 42]'
        ops = _parse_operations(text)
        assert len(ops) == 1

    def test_split_op_accepted(self):
        text = '[{"op": "split", "entry": "abc123", "parts": [{"content": "part one", "tags": ["a"]}, {"content": "part two", "tags": ["b"]}]}]'
        ops = _parse_operations(text)
        assert len(ops) == 1
        assert ops[0]["op"] == "split"

    def test_extract_op_accepted(self):
        text = '[{"op": "extract", "from": "src-id", "into": "dst-id", "extracted_content": "segment", "remaining_content": "rest", "merged_content": "combined"}]'
        ops = _parse_operations(text)
        assert len(ops) == 1
        assert ops[0]["op"] == "extract"

    def test_all_known_ops_accepted(self):
        text = (
            "["
            '{"op": "merge", "entries": ["a", "b"], "merged_content": "combined"},'
            '{"op": "promote", "entry": "c"},'
            '{"op": "prune", "entry": "d"},'
            '{"op": "tag", "entry": "e", "add_tags": ["x"]},'
            '{"op": "adjust_confidence", "entry": "f", "confidence": 0.5},'
            '{"op": "task", "title": "Do work", "priority": "low"},'
            '{"op": "split", "entry": "g", "parts": [{"content": "x"}, {"content": "y"}]},'
            '{"op": "extract", "from": "h", "into": "i", "extracted_content": "s", "remaining_content": "r", "merged_content": "m"}'
            "]"
        )
        ops = _parse_operations(text)
        assert len(ops) == 8


class TestExecuteOperations:
    def _make_entries(self):
        return [
            {
                "id": "aaaa-1111",
                "agent_id": "agent-a",
                "type": "memory",
                "content": "entry A content",
                "tags": ["infra"],
                "confidence": 0.9,
                "project": "proj-x",
            },
            {
                "id": "bbbb-2222",
                "agent_id": "agent-b",
                "type": "memory",
                "content": "entry B content",
                "tags": ["infra", "deploy"],
                "confidence": 0.7,
                "project": "proj-x",
            },
            {
                "id": "cccc-3333",
                "agent_id": "agent-c",
                "type": "memory",
                "content": "entry C content",
                "tags": [],
                "confidence": 0.5,
                "project": None,
            },
        ]

    def _make_client(self):
        client = MagicMock()
        client.write_memory = AsyncMock(return_value={"id": "new-entry-id"})
        client.delete_memory = AsyncMock()
        client.patch_memory = AsyncMock(return_value={})
        client.get_memory = AsyncMock()
        client.create_task = AsyncMock(return_value={"id": "task-id"})
        return client

    async def test_merge_writes_new_entry_and_deletes_both(self):
        entries = self._make_entries()
        client = self._make_client()
        ops = [
            {
                "op": "merge",
                "entries": ["aaaa-1111", "bbbb-2222"],
                "merged_content": "A and B combined",
            }
        ]
        await _execute_operations(ops, client, entries)

        client.write_memory.assert_called_once()
        call_kwargs = client.write_memory.call_args
        assert call_kwargs.kwargs["content"] == "A and B combined"
        assert set(call_kwargs.kwargs["tags"]) == {"infra", "deploy"}
        assert call_kwargs.kwargs["project"] == "proj-x"
        assert set(call_kwargs.kwargs["parents"]) == {"aaaa-1111", "bbbb-2222"}
        assert client.delete_memory.call_count == 2
        deleted_ids = {c.args[0] for c in client.delete_memory.call_args_list}
        assert deleted_ids == {"aaaa-1111", "bbbb-2222"}

    async def test_merge_uses_higher_confidence_type(self):
        entries = self._make_entries()
        entries[0]["type"] = "doc"
        entries[1]["type"] = "memory"
        client = self._make_client()
        ops = [
            {
                "op": "merge",
                "entries": ["aaaa-1111", "bbbb-2222"],
                "merged_content": "merged",
            }
        ]
        await _execute_operations(ops, client, entries)
        assert client.write_memory.call_args.kwargs["type"] == "doc"

    async def test_merge_project_null_when_different_projects(self):
        entries = self._make_entries()
        entries[1]["project"] = "proj-y"
        client = self._make_client()
        ops = [
            {
                "op": "merge",
                "entries": ["aaaa-1111", "bbbb-2222"],
                "merged_content": "merged",
            }
        ]
        await _execute_operations(ops, client, entries)
        assert client.write_memory.call_args.kwargs["project"] is None

    async def test_prune_high_confidence_flags_not_deletes(self):
        entries = self._make_entries()
        client = self._make_client()
        with patch("artel.archivist.synthesis.settings") as mock_settings:
            mock_settings.decay_floor = 0.05
            ops = [{"op": "prune", "entry": "cccc-3333"}]
            await _execute_operations(ops, client, entries)
        client.delete_memory.assert_not_called()
        client.patch_memory.assert_called_once()
        call_kwargs = client.patch_memory.call_args
        assert call_kwargs.args[0] == "cccc-3333"
        assert call_kwargs.kwargs["confidence"] == 0.05
        assert "archivist-flagged" in call_kwargs.kwargs["tags"]

    async def test_prune_at_floor_deletes(self):
        entries = self._make_entries()
        entries[2]["confidence"] = 0.05
        client = self._make_client()
        with patch("artel.archivist.synthesis.settings") as mock_settings:
            mock_settings.decay_floor = 0.05
            ops = [{"op": "prune", "entry": "cccc-3333"}]
            await _execute_operations(ops, client, entries)
        client.delete_memory.assert_called_once_with("cccc-3333")
        client.patch_memory.assert_not_called()

    async def test_promote_patches_type_to_doc(self):
        entries = self._make_entries()
        client = self._make_client()
        ops = [{"op": "promote", "entry": "aaaa-1111"}]
        await _execute_operations(ops, client, entries)
        client.patch_memory.assert_called_once_with("aaaa-1111", type="doc")

    async def test_tag_merges_with_existing_tags(self):
        entries = self._make_entries()
        client = self._make_client()
        client.get_memory = AsyncMock(return_value={"tags": ["infra"]})
        ops = [{"op": "tag", "entry": "aaaa-1111", "add_tags": ["critical", "infra"]}]
        await _execute_operations(ops, client, entries)
        client.get_memory.assert_called_once_with("aaaa-1111")
        patched_tags = set(client.patch_memory.call_args.kwargs["tags"])
        assert patched_tags == {"infra", "critical"}

    async def test_adjust_confidence_clamps_to_valid_range(self):
        entries = self._make_entries()
        client = self._make_client()
        ops = [
            {"op": "adjust_confidence", "entry": "aaaa-1111", "confidence": 1.5},
            {"op": "adjust_confidence", "entry": "bbbb-2222", "confidence": -0.3},
        ]
        await _execute_operations(ops, client, entries)
        calls = client.patch_memory.call_args_list
        assert calls[0] == call("aaaa-1111", confidence=1.0)
        assert calls[1] == call("bbbb-2222", confidence=0.0)

    async def test_task_creates_task(self):
        entries = self._make_entries()
        client = self._make_client()
        ops = [
            {
                "op": "task",
                "title": "Investigate rate limit spikes",
                "description": "Spikes observed across three agents",
                "priority": "high",
                "project": "proj-x",
            }
        ]
        await _execute_operations(ops, client, entries)
        client.create_task.assert_called_once_with(
            title="Investigate rate limit spikes",
            description="Spikes observed across three agents",
            priority="high",
            project="proj-x",
        )

    async def test_task_invalid_priority_defaults_to_normal(self):
        entries = self._make_entries()
        client = self._make_client()
        ops = [{"op": "task", "title": "Do something", "priority": "critical"}]
        await _execute_operations(ops, client, entries)
        assert client.create_task.call_args.kwargs["priority"] == "normal"

    async def test_hallucinated_id_is_skipped_for_promote(self):
        entries = self._make_entries()
        client = self._make_client()
        ops = [{"op": "promote", "entry": "hallucinated-id-xyz"}]
        await _execute_operations(ops, client, entries)
        client.patch_memory.assert_not_called()

    async def test_hallucinated_id_is_skipped_for_prune(self):
        entries = self._make_entries()
        client = self._make_client()
        ops = [{"op": "prune", "entry": "does-not-exist"}]
        await _execute_operations(ops, client, entries)
        client.delete_memory.assert_not_called()

    async def test_hallucinated_id_is_skipped_for_merge(self):
        entries = self._make_entries()
        client = self._make_client()
        ops = [
            {
                "op": "merge",
                "entries": ["aaaa-1111", "fake-id-999"],
                "merged_content": "should not happen",
            }
        ]
        await _execute_operations(ops, client, entries)
        client.write_memory.assert_not_called()
        client.delete_memory.assert_not_called()

    async def test_one_op_failure_does_not_abort_batch(self):
        entries = self._make_entries()
        client = self._make_client()
        client.patch_memory = AsyncMock(side_effect=Exception("network error"))
        with patch("artel.archivist.synthesis.settings") as mock_settings:
            mock_settings.decay_floor = 0.05
            ops = [
                {"op": "prune", "entry": "aaaa-1111"},
                {"op": "promote", "entry": "bbbb-2222"},
            ]
            await _execute_operations(ops, client, entries)
        assert client.patch_memory.call_count == 2

    async def test_empty_ops_list(self):
        entries = self._make_entries()
        client = self._make_client()
        await _execute_operations([], client, entries)
        client.write_memory.assert_not_called()
        client.delete_memory.assert_not_called()
        client.patch_memory.assert_not_called()
        client.create_task.assert_not_called()

    async def test_split_writes_parts_deletes_original(self):
        entries = self._make_entries()
        client = self._make_client()
        client.write_memory = AsyncMock(side_effect=[{"id": "new-1"}, {"id": "new-2"}])
        ops = [
            {
                "op": "split",
                "entry": "aaaa-1111",
                "parts": [
                    {"content": "part one content", "tags": ["part-a"]},
                    {"content": "part two content", "tags": ["part-b"]},
                ],
            }
        ]
        await _execute_operations(ops, client, entries)
        assert client.write_memory.call_count == 2
        first_call = client.write_memory.call_args_list[0]
        assert first_call.kwargs["content"] == "part one content"
        assert "aaaa-1111" in first_call.kwargs["parents"]
        assert "infra" in first_call.kwargs["tags"]
        assert "part-a" in first_call.kwargs["tags"]
        second_call = client.write_memory.call_args_list[1]
        assert second_call.kwargs["content"] == "part two content"
        assert "aaaa-1111" in second_call.kwargs["parents"]
        assert "infra" in second_call.kwargs["tags"]
        assert "part-b" in second_call.kwargs["tags"]
        client.delete_memory.assert_called_once_with("aaaa-1111")

    async def test_split_requires_two_parts(self):
        entries = self._make_entries()
        client = self._make_client()
        ops = [
            {
                "op": "split",
                "entry": "aaaa-1111",
                "parts": [{"content": "only one part", "tags": []}],
            }
        ]
        await _execute_operations(ops, client, entries)
        client.write_memory.assert_not_called()
        client.delete_memory.assert_not_called()

    async def test_extract_rewrites_both(self):
        entries = self._make_entries()
        client = self._make_client()
        ops = [
            {
                "op": "extract",
                "from": "aaaa-1111",
                "into": "bbbb-2222",
                "extracted_content": "the segment",
                "remaining_content": "what stays in from",
                "merged_content": "combined into content",
            }
        ]
        await _execute_operations(ops, client, entries)
        assert client.patch_memory.call_count == 2
        patch_calls = {c.args[0]: c.kwargs for c in client.patch_memory.call_args_list}
        assert patch_calls["bbbb-2222"]["content"] == "combined into content"
        assert patch_calls["aaaa-1111"]["content"] == "what stays in from"
        client.delete_memory.assert_not_called()

    async def test_extract_deletes_from_when_remaining_empty(self):
        entries = self._make_entries()
        client = self._make_client()
        ops = [
            {
                "op": "extract",
                "from": "aaaa-1111",
                "into": "bbbb-2222",
                "extracted_content": "the segment",
                "remaining_content": "",
                "merged_content": "combined into content",
            }
        ]
        await _execute_operations(ops, client, entries)
        client.patch_memory.assert_called_once_with("bbbb-2222", content="combined into content")
        client.delete_memory.assert_called_once_with("aaaa-1111")

    async def test_extract_rejects_same_id(self):
        entries = self._make_entries()
        client = self._make_client()
        ops = [
            {
                "op": "extract",
                "from": "aaaa-1111",
                "into": "aaaa-1111",
                "extracted_content": "segment",
                "remaining_content": "rest",
                "merged_content": "merged",
            }
        ]
        await _execute_operations(ops, client, entries)
        client.patch_memory.assert_not_called()
        client.delete_memory.assert_not_called()


class TestRunSynthesisStructured:
    def _make_entries(self):
        return [
            {
                "id": "entry-alpha",
                "agent_id": "agent-a",
                "type": "memory",
                "content": "Alpha observation",
                "tags": ["deploy"],
                "confidence": 0.8,
                "project": "myproject",
            },
            {
                "id": "entry-beta",
                "agent_id": "agent-b",
                "type": "memory",
                "content": "Beta observation",
                "tags": ["deploy"],
                "confidence": 0.6,
                "project": "myproject",
            },
        ]

    async def test_run_synthesis_executes_ops_from_llm(self):
        entries = self._make_entries()
        llm_response = '[{"op": "promote", "entry": "entry-alpha"}]'

        client = MagicMock()
        client.get_directives = AsyncMock(return_value=[])
        client.get_delta = AsyncMock(return_value=entries)
        client.list_tasks = AsyncMock(return_value=[])
        client.patch_memory = AsyncMock(return_value={})
        client.write_memory = AsyncMock(return_value={"id": "new"})
        client.delete_memory = AsyncMock()
        client.create_task = AsyncMock(return_value={"id": "t"})

        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.settings") as mock_settings,
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value=llm_response)),
        ):
            mock_settings.archivist_id = "archivist"
            mock_settings.directive_conflict_threshold = 0.85
            await synthesis.run_synthesis(client)

        client.patch_memory.assert_called_once_with("entry-alpha", type="doc")
        client.write_memory.assert_not_called()

    async def test_run_synthesis_no_synthesis_doc_written(self):
        entries = self._make_entries()
        llm_response = '[{"op": "prune", "entry": "entry-beta"}]'

        client = MagicMock()
        client.get_directives = AsyncMock(return_value=[])
        client.get_delta = AsyncMock(return_value=entries)
        client.list_tasks = AsyncMock(return_value=[])
        client.patch_memory = AsyncMock(return_value={})
        client.write_memory = AsyncMock(return_value={"id": "new"})
        client.delete_memory = AsyncMock()
        client.create_task = AsyncMock(return_value={"id": "t"})

        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.settings") as mock_settings,
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value=llm_response)),
        ):
            mock_settings.archivist_id = "archivist"
            mock_settings.directive_conflict_threshold = 0.85
            mock_settings.decay_floor = 0.05
            await synthesis.run_synthesis(client)

        client.delete_memory.assert_not_called()
        client.patch_memory.assert_called_once()
        patch_kwargs = client.patch_memory.call_args
        assert patch_kwargs.args[0] == "entry-beta"
        assert patch_kwargs.kwargs["confidence"] == 0.05
        assert "archivist-flagged" in patch_kwargs.kwargs["tags"]
        client.write_memory.assert_not_called()

    async def test_run_synthesis_empty_ops_no_side_effects(self):
        entries = self._make_entries()
        llm_response = "[]"

        client = MagicMock()
        client.get_directives = AsyncMock(return_value=[])
        client.get_delta = AsyncMock(return_value=entries)
        client.list_tasks = AsyncMock(return_value=[])
        client.patch_memory = AsyncMock(return_value={})
        client.write_memory = AsyncMock(return_value={"id": "new"})
        client.delete_memory = AsyncMock()
        client.create_task = AsyncMock(return_value={"id": "t"})

        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.settings") as mock_settings,
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value=llm_response)),
        ):
            mock_settings.archivist_id = "archivist"
            mock_settings.directive_conflict_threshold = 0.85
            await synthesis.run_synthesis(client)

        client.write_memory.assert_not_called()
        client.delete_memory.assert_not_called()
        client.patch_memory.assert_not_called()
        client.create_task.assert_not_called()

    async def test_run_synthesis_malformed_llm_output_no_crash(self):
        entries = self._make_entries()
        llm_response = "I cannot produce JSON right now, sorry."

        client = MagicMock()
        client.get_directives = AsyncMock(return_value=[])
        client.get_delta = AsyncMock(return_value=entries)
        client.list_tasks = AsyncMock(return_value=[])
        client.patch_memory = AsyncMock(return_value={})
        client.write_memory = AsyncMock(return_value={"id": "new"})
        client.delete_memory = AsyncMock()
        client.create_task = AsyncMock(return_value={"id": "t"})

        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.settings") as mock_settings,
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value=llm_response)),
        ):
            mock_settings.archivist_id = "archivist"
            mock_settings.directive_conflict_threshold = 0.85
            await synthesis.run_synthesis(client)

        client.write_memory.assert_not_called()
        client.delete_memory.assert_not_called()

    async def test_run_synthesis_task_op_creates_task(self):
        entries = self._make_entries()
        llm_response = '[{"op": "task", "title": "Audit deploy pipeline", "priority": "high", "project": "myproject"}]'

        client = MagicMock()
        client.get_directives = AsyncMock(return_value=[])
        client.get_delta = AsyncMock(return_value=entries)
        client.list_tasks = AsyncMock(return_value=[])
        client.patch_memory = AsyncMock(return_value={})
        client.write_memory = AsyncMock(return_value={"id": "new"})
        client.delete_memory = AsyncMock()
        client.create_task = AsyncMock(return_value={"id": "t"})

        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.settings") as mock_settings,
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value=llm_response)),
        ):
            mock_settings.archivist_id = "archivist"
            mock_settings.directive_conflict_threshold = 0.85
            await synthesis.run_synthesis(client)

        client.create_task.assert_called_once()
        assert client.create_task.call_args.kwargs["title"] == "Audit deploy pipeline"
        assert client.create_task.call_args.kwargs["priority"] == "high"
