from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import ENV_FILE


class CodexSubAgentSettings(BaseSettings):
    """Operator-owned limits for the official Codex Multi-Agent V2 runtime.

    These values are intentionally separate from user CODEX_HOME configuration. FDEX injects
    them as CLI config overrides so a tenant cannot loosen the Center's concurrency/token policy
    by editing a persisted Codex config.toml.
    """

    fdex_agent_subagents_enabled: bool = True
    # Official MultiAgentV2 counts the root agent in this number. Default 4 therefore permits
    # the root plus at most three simultaneously open child threads.
    fdex_agent_subagent_max_concurrent: int = Field(default=4, ge=1, le=16)
    # Shared weighted-token budget for the root thread plus all official sub-agent threads.
    fdex_agent_subagent_rollout_budget_tokens: int = Field(default=80_000, ge=10_000, le=2_000_000)
    fdex_agent_subagent_wait_min_ms: int = Field(default=1_000, ge=0, le=600_000)
    fdex_agent_subagent_wait_default_ms: int = Field(default=15_000, ge=0, le=3_600_000)
    fdex_agent_subagent_wait_max_ms: int = Field(default=60_000, ge=0, le=3_600_000)
    fdex_agent_subagent_sampling_token_weight: float = Field(default=1.0, ge=0.0, le=10.0)
    fdex_agent_subagent_prefill_token_weight: float = Field(default=0.25, ge=0.0, le=10.0)

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def _wait_bounds(self) -> "CodexSubAgentSettings":
        if not (
            self.fdex_agent_subagent_wait_min_ms
            <= self.fdex_agent_subagent_wait_default_ms
            <= self.fdex_agent_subagent_wait_max_ms
        ):
            raise ValueError("sub-agent wait timeout must satisfy min <= default <= max")
        return self


def fresh_subagent_settings() -> CodexSubAgentSettings:
    return CodexSubAgentSettings()


@lru_cache(maxsize=1)
def subagent_settings() -> CodexSubAgentSettings:
    return CodexSubAgentSettings()


def clear_subagent_settings_cache() -> None:
    subagent_settings.cache_clear()


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _reminders(limit: int) -> list[int]:
    # Notify before exhaustion without generating noisy tiny thresholds. Descending + de-duplicated
    # values match the official rollout_budget semantics.
    values = {max(1, int(limit * ratio)) for ratio in (0.25, 0.10, 0.05)}
    return sorted((value for value in values if value < limit), reverse=True)


def codex_subagent_policy(settings: CodexSubAgentSettings | None = None) -> dict[str, object]:
    cfg = settings or subagent_settings()
    max_threads = int(cfg.fdex_agent_subagent_max_concurrent)
    budget = int(cfg.fdex_agent_subagent_rollout_budget_tokens)
    return {
        "enabled": bool(cfg.fdex_agent_subagents_enabled),
        "protocol": "official-codex-multi-agent-v2",
        "tool_namespace": "collaboration",
        "max_concurrent_threads_per_session": max_threads,
        "max_parallel_subagents": max(0, max_threads - 1),
        "rollout_budget_tokens": budget,
        "rollout_budget_reminders": _reminders(budget),
        "wait_min_ms": int(cfg.fdex_agent_subagent_wait_min_ms),
        "wait_default_ms": int(cfg.fdex_agent_subagent_wait_default_ms),
        "wait_max_ms": int(cfg.fdex_agent_subagent_wait_max_ms),
        "sampling_token_weight": float(cfg.fdex_agent_subagent_sampling_token_weight),
        "prefill_token_weight": float(cfg.fdex_agent_subagent_prefill_token_weight),
        # FDEX does not expose the model/reasoning override fields. Spawned agents inherit the
        # already-selected FDEX Responses provider/model instead of becoming a provider bypass.
        "spawn_model_overrides": False,
        # MultiAgentV2 uses canonical task paths (/root/...) for hierarchy. The bundled 0.147
        # runtime does not expose a hard V2 depth knob, so FDEX bounds the tree with total open
        # threads + shared budget rather than pretending agents.max_depth controls V2.
        "hard_v2_depth_limit_available": False,
    }


def codex_subagent_cli_overrides(
    settings: CodexSubAgentSettings | None = None,
) -> tuple[str, ...]:
    cfg = settings or subagent_settings()
    if not cfg.fdex_agent_subagents_enabled:
        return (
            "features.collab=false",
            "features.multi_agent_v2=false",
            "features.rollout_budget=false",
        )

    limit = int(cfg.fdex_agent_subagent_rollout_budget_tokens)
    reminders = ", ".join(str(value) for value in _reminders(limit))
    multi_agent = (
        "features.multi_agent_v2={ "
        "enabled = true, "
        f"max_concurrent_threads_per_session = {int(cfg.fdex_agent_subagent_max_concurrent)}, "
        f"min_wait_timeout_ms = {int(cfg.fdex_agent_subagent_wait_min_ms)}, "
        f"default_wait_timeout_ms = {int(cfg.fdex_agent_subagent_wait_default_ms)}, "
        f"max_wait_timeout_ms = {int(cfg.fdex_agent_subagent_wait_max_ms)}, "
        "tool_namespace = \"collaboration\", "
        "expose_spawn_agent_model_overrides = false, "
        "hide_spawn_agent_metadata = false, "
        "wait_agent_enabled = true, non_code_mode_only = false }"
    )
    rollout = (
        "features.rollout_budget={ "
        "enabled = true, "
        f"limit_tokens = {limit}, "
        f"reminder_at_remaining_tokens = [{reminders}], "
        f"sampling_token_weight = {float(cfg.fdex_agent_subagent_sampling_token_weight):g}, "
        f"prefill_token_weight = {float(cfg.fdex_agent_subagent_prefill_token_weight):g} }}"
    )
    return ("features.collab=true", multi_agent, rollout)
