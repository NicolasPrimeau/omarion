from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True)

    db_path: str = "omarion.db"
    host: str = "0.0.0.0"
    port: int = 8000
    agent_keys: str = ""
    ui_password: str = ""
    ui_agent_id: str = "nimbus"

    def api_keys(self) -> dict[str, str]:
        pairs: dict[str, str] = {}
        for pair in self.agent_keys.split(","):
            parts = [p.strip() for p in pair.strip().split(":")]
            if len(parts) >= 2:
                pairs[parts[1]] = parts[0]
        return pairs

    def agent_projects(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for pair in self.agent_keys.split(","):
            parts = [p.strip() for p in pair.strip().split(":")]
            if len(parts) >= 2:
                agent_id = parts[0]
                if len(parts) >= 3 and parts[2] and parts[2] != "*":
                    result[agent_id] = parts[2].split(";")
        return result

    def ui_agent_key(self) -> str:
        for key, agent_id in self.api_keys().items():
            if agent_id == self.ui_agent_id:
                return key
        return ""


settings = Settings()
