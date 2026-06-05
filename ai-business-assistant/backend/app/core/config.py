from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI业务落地助手"
    secret_key: str = "dev-secret-change-me"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7
    database_url: str = "sqlite:///./ai_business_assistant.db"
    upload_dir: str = "uploads"
    max_upload_size_mb: int = 50
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    ai_base_url: str | None = None
    ai_api_key: str | None = None
    ai_model_text: str = "mock-text"
    ai_model_vision: str = "mock-vision"
    ai_model_summary: str = "mock-summary"
    ai_model_code: str = "mock-code"
    ai_model_industry: str = "mock-industry"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
