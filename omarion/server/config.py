from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True)

    db_path: str = "omarion.db"
    host: str = "0.0.0.0"
    port: int = 8000
    agent_keys: str = ""

    def api_keys(self) -> dict[str, str]:
        pairs: dict[str, str] = {}
        for pair in self.agent_keys.split(","):
            pair = pair.strip()
            if ":" in pair:
                agent_id, key = pair.split(":", 1)
                pairs[key.strip()] = agent_id.strip()
        return pairs


settings = Settings()
