from pydantic_settings import BaseSettings, SettingsConfigDict


class ArchivistSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    artel_url: str = "http://localhost:8000"
    archivist_id: str = "archivist"
    agent_keys: str = ""
    anthropic_api_key: str = ""
    archivist_provider: str = "anthropic"
    archivist_model: str = ""
    archivist_api_key: str = ""
    archivist_base_url: str = ""
    synthesis_interval: int = 3600
    conflict_threshold: float = 0.92
    decay_rate: float = 0.9
    decay_floor: float = 0.05
    decay_window_days: int = 7
    promotion_memory_min_version: int = 3
    promotion_stability_days: int = 7

    def api_key(self) -> str:
        for pair in self.agent_keys.split(","):
            parts = [p.strip() for p in pair.strip().split(":")]
            if len(parts) >= 2 and parts[0] == self.archivist_id:
                return parts[1]
        return ""


settings = ArchivistSettings()
