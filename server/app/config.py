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

    # FDEX Agent Runtime. Disabled by default until an administrator intentionally
    # provisions a server-side workspace. Android never receives shell credentials.
    # A dedicated access token is required whenever the runtime is enabled; never
    # reuse GitHub, admin, or AI provider credentials for this purpose.
    fdex_agent_enabled: bool = False
    fdex_agent_access_token: str = ""
    fdex_agent_workspace: str = "/opt/fdex"
    fdex_agent_worktree_root: str = str(SERVER_DIR / "data" / "agent-worktrees")
    fdex_agent_command_timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    fdex_agent_build_timeout_seconds: float = Field(default=900.0, ge=30.0, le=1800.0)
    fdex_agent_max_output_chars: int = Field(default=20000, ge=1000, le=200000)
    fdex_agent_max_file_chars: int = Field(default=200000, ge=1000, le=1000000)
    fdex_agent_max_steps: int = Field(default=10, ge=1, le=30)
    fdex_agent_model_max_tokens: int = Field(default=1600, ge=128, le=4000)

    # MemPalace raw history + Letta structured memory. FDEX uses the same encrypted
    # provider pool through a loopback-only provider proxy; Android never receives
    # provider credentials or these service tokens.
    fdex_memory_enabled: bool = True
    fdex_memory_managed_stack: bool = True
    fdex_memory_required: bool = False
    fdex_memory_data_dir: str = str(SERVER_DIR / "data" / "memory")
    fdex_memory_context_max_chars: int = Field(default=8000, ge=1000, le=40000)
    fdex_memory_system_max_chars: int = Field(default=11900, ge=2000, le=12000)
    fdex_memory_recall_limit: int = Field(default=6, ge=1, le=30)
    fdex_memory_recall_timeout_seconds: float = Field(default=12.0, ge=0.5, le=120.0)
    fdex_memory_write_timeout_seconds: float = Field(default=180.0, ge=1.0, le=1800.0)

    fdex_memory_proxy_url: str = "http://127.0.0.1:18100/v1"
    fdex_memory_proxy_token: str = ""
    fdex_memory_proxy_port: int = Field(default=18100, ge=1, le=65535)
    fdex_memory_embedding_model: str = "text-embedding-3-small"
    fdex_memory_embedding_dimension: int = Field(default=1536, ge=128, le=4096)
    fdex_memory_embedding_max_chars: int = Field(default=12000, ge=256, le=100000)

    fdex_memory_qdrant_url: str = "http://127.0.0.1:6333"
    fdex_memory_qdrant_port: int = Field(default=6333, ge=1, le=65535)
    fdex_memory_qdrant_collection: str = "fdex_mempalace_remote_v1"
    fdex_memory_qdrant_timeout_seconds: float = Field(default=20.0, ge=1.0, le=120.0)

    fdex_letta_enabled: bool = True
    fdex_letta_base_url: str = "http://127.0.0.1:8283"
    fdex_letta_port: int = Field(default=8283, ge=1, le=65535)
    fdex_letta_server_password: str = ""
    fdex_letta_encryption_key: str = ""
    fdex_letta_model: str = "openai/gpt-4o-mini"
    fdex_letta_embedding: str = "openai/text-embedding-3-small"
    fdex_letta_timeout_seconds: float = Field(default=120.0, ge=1.0, le=600.0)

    # Admin dashboard. update_server.sh generates password and session secret when missing.
    admin_username: str = "admin"
    admin_password: str = ""
    admin_session_secret: str = ""
    admin_cookie_secure: bool = True
    admin_session_hours: int = Field(default=12, ge=1, le=168)

    # GitHub is only used by the server-side release synchronizer. Android never talks to GitHub.
    github_repo: str = "b8vipvip/fdex"
    github_token: str = ""
    release_cache_dir: str = str(SERVER_DIR / "data" / "releases")

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
