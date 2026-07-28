from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = SERVER_DIR / ".env"


class Settings(BaseSettings):
    app_name: str = "FDEX Server"
    app_version: str = "1.0.0"
    environment: str = "production"
    public_base_url: str = "https://fdex.k2n.cn"
    api_prefix: str = "/api"
    cors_origins: str = "https://fdex.k2n.cn"

    # FDEX uses its own loopback-only port. Change FDEX_PORT when the default is occupied.
    fdex_host: str = "127.0.0.1"
    fdex_port: int = Field(default=18080, ge=1, le=65535)
    fdex_workers: int = Field(default=2, ge=1, le=16)

    # Third-party AI/API credentials are server-only. Never return api_key to clients.
    ai_provider: str = "openai_compatible"
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    ai_timeout_seconds: float = Field(default=60.0, ge=5.0, le=600.0)

    # Admin dashboard. update_server.sh generates password and session secret when missing.
    admin_username: str = "admin"
    admin_password: str = ""
    admin_session_secret: str = ""
    admin_cookie_secure: bool = True
    admin_session_hours: int = Field(default=12, ge=1, le=168)

    github_repo: str = "b8vipvip/fdex"
    app_dir: str = "/opt/fdex"
    service_name: str = "fdex"
    admin_log_lines: int = Field(default=300, ge=50, le=2000)

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
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

    @property
    def admin_ready(self) -> bool:
        return bool(
            self.admin_username.strip()
            and len(self.admin_password) >= 12
            and len(self.admin_session_secret) >= 32
        )

    @property
    def github_owner_repo(self) -> tuple[str, str]:
        owner, _, repo = self.github_repo.partition("/")
        return owner or "b8vipvip", repo or "fdex"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def fresh_settings() -> Settings:
    """Load the current .env without using the process cache."""
    return Settings()
