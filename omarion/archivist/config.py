from pydantic_settings import BaseSettings, SettingsConfigDict


class ArchivistSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True)

    omarion_url: str = "http://localhost:8000"
    archivist_id: str = "archivist"
    archivist_key: str = ""
    anthropic_api_key: str = ""
    synthesis_interval: int = 3600
    conflict_threshold: float = 0.92


settings = ArchivistSettings()
