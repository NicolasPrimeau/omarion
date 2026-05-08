from pydantic import PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    db_path: str = "artel.db"
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False
    agent_keys: str = ""
    registration_key: str = ""
    ui_password: str = ""
    ui_agent_id: str = "nimbus"
    public_url: str = ""
    mcp_url: str = ""
    jwt_ttl: int = 2592000

    _keys_cache: dict[str, str] | None = PrivateAttr(default=None)
    _projects_cache: dict[str, list[str]] | None = PrivateAttr(default=None)

    def api_keys(self) -> dict[str, str]:
        if self._keys_cache is None:
            pairs: dict[str, str] = {}
            for pair in self.agent_keys.split(","):
                parts = [p.strip() for p in pair.strip().split(":")]
                if len(parts) >= 2:
                    pairs[parts[1]] = parts[0]
            object.__setattr__(self, "_keys_cache", pairs)
        return self._keys_cache  # type: ignore[return-value]

    def agent_projects(self) -> dict[str, list[str]]:
        if self._projects_cache is None:
            result: dict[str, list[str]] = {}
            for pair in self.agent_keys.split(","):
                parts = [p.strip() for p in pair.strip().split(":")]
                if len(parts) >= 2:
                    agent_id = parts[0]
                    if len(parts) >= 3 and parts[2] and parts[2] != "*":
                        result[agent_id] = parts[2].split(";")
            object.__setattr__(self, "_projects_cache", result)
        return self._projects_cache  # type: ignore[return-value]

    def ui_agent_key(self) -> str:
        for key, agent_id in self.api_keys().items():
            if agent_id == self.ui_agent_id:
                return key
        return ""


settings = Settings()
