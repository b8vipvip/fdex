from __future__ import annotations

import shutil
import time
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app import agent_projects_core as _core
from app.agent_projects_core import *  # noqa: F401,F403
from app.config import fresh_settings
from app.github_app import GitHubAppClient, GitHubAppError

# Preserve private helper imports used by the GitHub App compatibility layer.
_safe_scope = _core._safe_scope
_safe_repo = _core._safe_repo
_safe_branch = _core._safe_branch
_now = _core._now
_utcnow = _core._utcnow
_as_time = _core._as_time
_after = _core._after
_owner_write_guard = _core._owner_write_guard

_RETRYABLE_GIT_ERRORS = (
    "could not resolve host",
    "failed to connect",
    "connection reset",
    "connection timed out",
    "operation timed out",
    "the requested url returned error: 502",
    "the requested url returned error: 503",
    "the requested url returned error: 504",
    "early eof",
    "rpc failed",
    "http/2 stream",
    "tls",
    "proxy",
    "connection closed",
    "remote end hung up",
)
_GIT_PROXY_HOSTS = (
    "github.com",
    "githubusercontent.com",
    "githubassets.com",
)


def github_proxy_url() -> str:
    """Return the dedicated GitHub proxy after validating its transport scheme."""
    proxy = fresh_settings().fdex_github_http_proxy.strip()
    if not proxy:
        return ""
    scheme = (urlsplit(proxy).scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise ValueError("GitHub 出站代理仅支持 http:// 或 https://")
    return proxy


def apply_github_proxy_to_git_env(env: dict[str, str]) -> dict[str, str]:
    """Add process-local Git config for GitHub-owned HTTPS hosts only.

    This intentionally does not set HTTP_PROXY/HTTPS_PROXY on the FDEX process, so AI providers,
    SMTP and other outbound services keep their own network path. GitHub redirects/assets remain
    inside the same dedicated FDEX proxy path without turning the proxy into a global Git setting.
    """
    proxy = github_proxy_url()
    if not proxy:
        return env

    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0") or 0)
    except ValueError:
        count = 0
    settings = fresh_settings()
    entries: list[tuple[str, str]] = []
    for host in _GIT_PROXY_HOSTS:
        entries.append((f"http.https://{host}.proxy", proxy))
    entries.extend(
        [
            ("http.https://github.com.lowSpeedLimit", "1"),
            (
                "http.https://github.com.lowSpeedTime",
                str(max(5, int(settings.fdex_github_read_timeout_seconds))),
            ),
        ]
    )
    for key, value in entries:
        env[f"GIT_CONFIG_KEY_{count}"] = key
        env[f"GIT_CONFIG_VALUE_{count}"] = value
        count += 1
    env["GIT_CONFIG_COUNT"] = str(count)
    return env


def _route_label() -> str:
    return "FDEX GitHub 专用代理" if fresh_settings().fdex_github_http_proxy.strip() else "服务器直连"


def _retryable_git_error(output: str) -> bool:
    text = (output or "").casefold()
    return any(marker in text for marker in _RETRYABLE_GIT_ERRORS)


def _cleanup_failed_clone(args: tuple[str, ...], cwd: Path) -> None:
    if len(args) < 3 or args[1] != "clone":
        return
    destination = Path(args[-1]).expanduser()
    if not destination.is_absolute():
        destination = (cwd / destination).resolve()
    else:
        destination = destination.resolve()
    cwd_resolved = cwd.resolve()
    if destination == cwd_resolved or cwd_resolved not in destination.parents:
        return
    if destination.exists():
        shutil.rmtree(destination)


class AgentProjectStore(_core.AgentProjectStore):
    """Transport-aware Agent project store.

    The durable/account-scoping implementation remains in ``agent_projects_core``. Phase 7.15
    centralizes every GitHub network path here; Phase 7.16 can point that path at a managed,
    loopback-only authenticated Xray/VLESS gateway without changing AI/SMTP or other traffic.
    """

    def _git_env(self, owner_id: str, connection_id: Any, *, required: bool = False) -> dict[str, str]:
        env = super()._git_env(owner_id, connection_id, required=required)
        return apply_github_proxy_to_git_env(env)

    @staticmethod
    def _git(args: tuple[str, ...], *, cwd: Path, env: dict[str, str], timeout: int) -> str:
        command = args[1] if len(args) > 1 else "git"
        retry_safe = command in {"clone", "fetch", "push", "ls-remote"}
        settings = fresh_settings()
        attempts = max(1, int(settings.fdex_github_retry_attempts)) if retry_safe else 1

        for attempt in range(1, attempts + 1):
            try:
                result = _core.subprocess.run(
                    args,
                    cwd=str(cwd),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            except _core.subprocess.TimeoutExpired as exc:
                if retry_safe and attempt < attempts:
                    _cleanup_failed_clone(args, cwd)
                    time.sleep(0.5 * attempt)
                    continue
                raise RuntimeError(
                    f"GitHub Git {command} 超时（{_route_label()}，{timeout} 秒）"
                ) from exc

            output = ((result.stdout or "") + (result.stderr or "")).strip()
            if result.returncode == 0:
                return output[-20000:]
            if retry_safe and attempt < attempts and _retryable_git_error(output):
                _cleanup_failed_clone(args, cwd)
                time.sleep(0.5 * attempt)
                continue

            detail = output[-4000:] or f"git exited with {result.returncode}"
            if retry_safe and _retryable_git_error(detail):
                raise RuntimeError(
                    f"GitHub Git {command} 网络失败（{_route_label()}，已尝试 {attempts} 次）：{detail}"
                )
            raise RuntimeError(detail)

        raise RuntimeError(f"GitHub Git {command} 未获得有效结果")

    @staticmethod
    def _oauth_post(url: str, form: dict[str, str]) -> dict[str, Any]:
        try:
            result = GitHubAppClient()._request(
                "POST",
                url,
                auth="none",
                accept="application/json",
                form=form,
                retry_safe=False,
                operation="GitHub Device OAuth",
            )
        except GitHubAppError as exc:
            raise RuntimeError(str(exc)) from exc
        if not isinstance(result, dict):
            raise RuntimeError("unexpected GitHub OAuth response")
        return result

    @staticmethod
    def _github_api(
        token: str,
        url: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        method_upper = (method or "GET").upper()
        try:
            return GitHubAppClient()._request(
                method_upper,
                url,
                token=token,
                json_body=payload,
                retry_safe=method_upper in {"GET", "HEAD", "OPTIONS"},
                operation=f"GitHub API {method_upper}",
            )
        except GitHubAppError as exc:
            raise ValueError(str(exc)) from exc


@lru_cache(maxsize=1)
def agent_project_store() -> AgentProjectStore:
    store = AgentProjectStore()
    store.init()
    return store
