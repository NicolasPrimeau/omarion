from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    artel_url: str = "http://localhost:8000"
    mcp_agent_id: str = "mcp"
    mcp_agent_key: str = ""
    mcp_transport: str = "stdio"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8001
    mcp_project: str = ""


settings = MCPSettings()
