from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "FDEX Server"
    app_version: str = "1.0.0"
    environment: str = "production"
    public_base_url: str = "https://fdex.k2n.cn"
    api_prefix: str = "/api"
    cors_origins: str = "https://fdex.k2n.cn"

    # Third-party AI/API credentials are server-only. Never return api_key to clients.
    ai_provider: str = "openai_compatible"
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    ai_timeout_seconds: float = 60.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        values = [item.strip() for item in self.cors_origins.split(",") if item.strip()]
        return values or [self.public_base_url]

    @property
    def ai_enabled(self) -> bool:
        return all(
            value.strip()
            for value in (self.ai_provider, self.ai_base_url, self.ai_api_key, self.ai_model)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
