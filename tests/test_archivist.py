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
