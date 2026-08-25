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

    fdex_host: str = "127.0.0.1"
    fdex_port: int = Field(default=18080, ge=1, le=65535)
    fdex_workers: int = Field(default=2, ge=1, le=16)

    # Central FDEX account/session service. Opaque access + rotating refresh tokens are
    # hashed server-side; account user_id becomes the canonical owner scope.
    fdex_auth_registration_enabled: bool = True
    fdex_auth_access_minutes: int = Field(default=60, ge=5, le=1440)
    fdex_auth_refresh_days: int = Field(default=30, ge=1, le=365)
    fdex_auth_login_max_failures: int = Field(default=5, ge=2, le=50)
    fdex_auth_login_window_minutes: int = Field(default=10, ge=1, le=1440)
    fdex_auth_login_block_minutes: int = Field(default=15, ge=1, le=1440)
    fdex_auth_reset_code_minutes: int = Field(default=10, ge=2, le=60)
    fdex_auth_reset_max_attempts: int = Field(default=5, ge=1, le=20)

    # SMTP is used only for FDEX account-security mail such as password reset codes.
    # If host/from are blank, reset requests fail closed without exposing whether an email exists.
    fdex_smtp_host: str = ""
    fdex_smtp_port: int = Field(default=587, ge=1, le=65535)
    fdex_smtp_username: str = ""
    fdex_smtp_password: str = ""
    fdex_smtp_from_email: str = ""
    fdex_smtp_from_name: str = "FDEX"
    fdex_smtp_starttls: bool = True
    fdex_smtp_ssl: bool = False
    fdex_smtp_timeout_seconds: float = Field(default=15.0, ge=2.0, le=120.0)

    # Legacy one-provider fields are retained only for migration. Runtime AI traffic,
    # including Coding Agent, is routed through the encrypted provider pool.
    ai_provider: str = "openai_compatible"
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    ai_timeout_seconds: float = Field(default=60.0, ge=5.0, le=600.0)

    # Coding Agent uses the shared provider pool. No Agent-specific AI endpoint/key/model exists.
    fdex_agent_enabled: bool = True
    # Bootstrap/enrollment secret retained only for migration from pre-central-auth clients.
    fdex_agent_access_token: str = ""
    fdex_agent_workspace: str = "/opt/fdex"
    fdex_agent_worktree_root: str = str(SERVER_DIR / "data" / "agent-worktrees")
    fdex_agent_sandbox_root: str = str(SERVER_DIR / "data" / "agent-sandboxes")
    fdex_agent_default_owner: str = "local"
    fdex_agent_command_timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    fdex_agent_build_timeout_seconds: float = Field(default=900.0, ge=30.0, le=1800.0)
    fdex_agent_max_output_chars: int = Field(default=20000, ge=1000, le=200000)
    fdex_agent_max_file_chars: int = Field(default=200000, ge=1000, le=1000000)
    fdex_agent_max_steps: int = Field(default=10, ge=1, le=30)
    fdex_agent_model_max_tokens: int = Field(default=1600, ge=128, le=4000)
    fdex_agent_sandbox_memory_mb: int = Field(default=2048, ge=128, le=16384)
    fdex_agent_sandbox_cpu_percent: int = Field(default=150, ge=10, le=800)
    fdex_agent_sandbox_pids_max: int = Field(default=512, ge=32, le=4096)
    fdex_agent_sandbox_max_concurrent: int = Field(default=1, ge=1, le=8)
    # Disk is not preallocated. This is an owner-wide admission budget for repository clones,
    # worktrees and build caches; users can release completed workspaces from the client.
    fdex_agent_account_disk_mb: int = Field(default=20480, ge=512, le=204800)

    # Per-account GitHub authorization uses OAuth Device Flow. The client secret is
    # deliberately not needed by the device flow; access/refresh tokens stay encrypted
    # in the server-side Agent project store and are never returned to Android.
    fdex_github_oauth_client_id: str = ""
    fdex_github_oauth_scope: str = "repo read:user offline_access"
    fdex_github_oauth_refresh_skew_seconds: int = Field(default=300, ge=30, le=3600)

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

    admin_username: str = "admin"
    admin_password: str = ""
    admin_session_secret: str = ""
    admin_cookie_secure: bool = True
    admin_session_hours: int = Field(default=12, ge=1, le=168)

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
        return all(value.strip() for value in (self.ai_provider, self.ai_base_url, self.ai_api_key, self.ai_model))

    @property
    def admin_ready(self) -> bool:
        return bool(self.admin_username.strip() and len(self.admin_password) >= 12 and len(self.admin_session_secret) >= 32)

    @property
    def smtp_ready(self) -> bool:
        return bool(self.fdex_smtp_host.strip() and self.fdex_smtp_from_email.strip())

    @property
    def github_owner_repo(self) -> tuple[str, str]:
        owner, _, repo = self.github_repo.partition("/")
        return owner or "b8vipvip", repo or "fdex"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def fresh_settings() -> Settings:
    return Settings()
