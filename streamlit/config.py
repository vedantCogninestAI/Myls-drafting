from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class StreamlitSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    API_BASE_URL: str


settings = StreamlitSettings()
