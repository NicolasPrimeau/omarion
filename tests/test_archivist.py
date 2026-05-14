from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artel.archivist import conflict, synthesis
from artel.archivist.llm import _api_key, _default_model, is_configured


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


class TestThreePassOrchestration:
    """run_synthesis routes delta entries to the correct per-scope passes."""

    def _entry(self, agent_id="agent-a", scope="project", project=None):
        return {
            "id": "e1",
            "agent_id": agent_id,
            "type": "memory",
            "content": "x",
            "scope": scope,
            "project": project,
        }

    @pytest.fixture
    def client(self):
        c = MagicMock()
        c.get_delta = AsyncMock(return_value=[])
        c.list_entries = AsyncMock(return_value=[])
        c.write_memory = AsyncMock(return_value={"id": "new"})
        c.patch_memory = AsyncMock()
        c.get_memory = AsyncMock(return_value=None)
        c.search_memory = AsyncMock(return_value=[])
        c.list_tasks = AsyncMock(return_value=[])
        return c

    async def test_no_active_agents_or_projects_still_runs_global(self, client):
        client.get_delta = AsyncMock(return_value=[])
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch(
                "artel.archivist.synthesis._run_global_synthesis", new_callable=AsyncMock
            ) as mock_global,
            patch(
                "artel.archivist.synthesis._run_agent_synthesis", new_callable=AsyncMock
            ) as mock_agent,
            patch(
                "artel.archivist.synthesis._run_project_synthesis", new_callable=AsyncMock
            ) as mock_proj,
        ):
            await synthesis.run_synthesis(client)
            mock_global.assert_called_once()
            mock_agent.assert_not_called()
            mock_proj.assert_not_called()

    async def test_agent_scope_entry_triggers_agent_pass(self, client):
        client.get_delta = AsyncMock(return_value=[self._entry(agent_id="alice", scope="agent")])
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch(
                "artel.archivist.synthesis._run_agent_synthesis", new_callable=AsyncMock
            ) as mock_agent,
            patch("artel.archivist.synthesis._run_project_synthesis", new_callable=AsyncMock),
            patch("artel.archivist.synthesis._run_global_synthesis", new_callable=AsyncMock),
        ):
            await synthesis.run_synthesis(client)
            mock_agent.assert_called_once_with("alice", client)

    async def test_project_entry_triggers_project_pass(self, client):
        client.get_delta = AsyncMock(return_value=[self._entry(scope="project", project="my-proj")])
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis._run_agent_synthesis", new_callable=AsyncMock),
            patch(
                "artel.archivist.synthesis._run_project_synthesis", new_callable=AsyncMock
            ) as mock_proj,
            patch("artel.archivist.synthesis._run_global_synthesis", new_callable=AsyncMock),
        ):
            await synthesis.run_synthesis(client)
            mock_proj.assert_called_once_with("my-proj", client)

    async def test_multiple_agents_each_get_own_pass(self, client):
        client.get_delta = AsyncMock(
            return_value=[
                self._entry(agent_id="alice", scope="agent"),
                self._entry(agent_id="bob", scope="agent"),
            ]
        )
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch(
                "artel.archivist.synthesis._run_agent_synthesis", new_callable=AsyncMock
            ) as mock_agent,
            patch("artel.archivist.synthesis._run_project_synthesis", new_callable=AsyncMock),
            patch("artel.archivist.synthesis._run_global_synthesis", new_callable=AsyncMock),
        ):
            await synthesis.run_synthesis(client)
            called_with = {call.args[0] for call in mock_agent.call_args_list}
            assert called_with == {"alice", "bob"}

    async def test_archivist_entries_excluded_from_passes(self, client):
        with patch("artel.archivist.synthesis.settings") as s:
            s.archivist_id = "archivist"
            s.decay_window_days = 7
            s.decay_floor = 0.1
            s.decay_rate = 0.9
            client.get_delta = AsyncMock(
                return_value=[self._entry(agent_id="archivist", scope="agent")]
            )
            with (
                patch("artel.archivist.synthesis.is_configured", return_value=True),
                patch(
                    "artel.archivist.synthesis._run_agent_synthesis", new_callable=AsyncMock
                ) as mock_agent,
                patch("artel.archivist.synthesis._run_project_synthesis", new_callable=AsyncMock),
                patch("artel.archivist.synthesis._run_global_synthesis", new_callable=AsyncMock),
            ):
                await synthesis.run_synthesis(client)
                mock_agent.assert_not_called()

    async def test_failed_pass_does_not_abort_remaining(self, client):
        client.get_delta = AsyncMock(
            return_value=[
                self._entry(agent_id="alice", scope="agent"),
                self._entry(scope="project", project="proj-a"),
            ]
        )
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch(
                "artel.archivist.synthesis._run_agent_synthesis",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "artel.archivist.synthesis._run_project_synthesis", new_callable=AsyncMock
            ) as mock_proj,
            patch(
                "artel.archivist.synthesis._run_global_synthesis", new_callable=AsyncMock
            ) as mock_global,
        ):
            await synthesis.run_synthesis(client)
            mock_proj.assert_called_once()
            mock_global.assert_called_once()
