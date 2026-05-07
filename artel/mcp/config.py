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
    mcp_agent_id: str = ""
    mcp_agent_key: str = ""
    mcp_registration_key: str = ""
    mcp_transport: str = "stdio"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8001
    mcp_project: str = ""


settings = MCPSettings()
