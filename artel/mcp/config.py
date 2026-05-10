from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_creds_file = Path.home() / ".config" / "artel" / "credentials"


class MCPSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_creds_file),
        env_ignore_empty=True,
        extra="ignore",
    )

    artel_url: str = "http://localhost:8000"
    agent_keys: str = ""
    mcp_agent_id: str = "mcp"
    mcp_agent_key: str = ""
    mcp_registration_key: str = ""
    mcp_transport: str = "stdio"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8001
    mcp_project: str = ""

    def api_key(self) -> str:
        if self.mcp_agent_key:
            return self.mcp_agent_key
        for pair in self.agent_keys.split(","):
            parts = [p.strip() for p in pair.strip().split(":")]
            if len(parts) >= 2 and parts[0] == self.mcp_agent_id:
                return parts[1]
        return ""

    def resolve_project(self, override: str | None = None) -> str | None:
        return override or self.mcp_project or None


settings = MCPSettings()
