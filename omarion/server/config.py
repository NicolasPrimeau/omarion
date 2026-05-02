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
            pair = pair.strip()
            if ":" in pair:
                agent_id, key = pair.split(":", 1)
                pairs[key.strip()] = agent_id.strip()
        return pairs

    def ui_agent_key(self) -> str:
        for key, agent_id in self.api_keys().items():
            if agent_id == self.ui_agent_id:
                return key
        return ""


settings = Settings()
