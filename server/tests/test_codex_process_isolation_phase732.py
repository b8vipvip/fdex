from __future__ import annotations

from pathlib import Path

import pytest

from app.codex_app_server import CodexAppServerClient
from app import codex_process_isolation as isolation


class _Settings:
    service_name = "fdex"
    fdex_agent_sandbox_memory_mb = 2048
    fdex_agent_sandbox_cpu_percent = 150
    fdex_agent_sandbox_pids_max = 512
    fdex_agent_process_isolation_required = True
    fdex_agent_process_stop_grace_seconds = 3.0


def _ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(isolation.platform, "system", lambda: "Linux")
    monkeypatch.setattr(isolation.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(isolation, "_controllers", lambda: {"cpu", "memory", "pids", "io"})
    monkeypatch.setattr(
        isolation,
        "_run",
        lambda args, timeout=5.0: (0, "active") if tuple(args)[:2] == ("systemctl", "is-active") else (0, ""),
    )


def test_phase732_status_requires_linux_systemd_cgroup_v2_and_parent_service(monkeypatch: pytest.MonkeyPatch) -> None:
    _ready(monkeypatch)
    status = isolation.codex_process_isolation_status(_Settings())
    assert status["ready"] is True
    assert status["enforced"] is True
    assert status["parent_unit"] == "fdex.service"
    assert {"cpu", "memory", "pids"}.issubset(set(status["controllers"]))


def test_phase732_required_isolation_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(isolation.platform, "system", lambda: "Darwin")
    status = isolation.codex_process_isolation_status(_Settings())
    assert status["ready"] is False
    assert status["enforced"] is False
    with pytest.raises(isolation.CodexProcessIsolationError, match="Linux cgroup v2"):
        isolation.build_codex_process_isolation("owner-a", "task-a", settings=_Settings())


def test_phase732_transient_service_owns_limits_without_secret_in_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    _ready(monkeypatch)
    spec = isolation.build_codex_process_isolation("owner-a", "task-a", settings=_Settings())
    assert spec is not None
    env = {
        "FDEX_CODEX_PROVIDER_KEY": "super-secret-provider-key",
        "CODEX_HOME": "/srv/fdex/codex/owner-a",
        "HOME": "/srv/fdex/codex/owner-a",
        "PATH": "/usr/bin:/bin",
        "CI": "true",
    }
    wrapped = spec.wrap_launch_args(
        ("/srv/fdex/codex_env_wrapper.py", "--config", "base_url=\"https://example.test/$tenant%25\"", "app-server"),
        env,
    )
    joined = "\n".join(wrapped)
    assert wrapped[0] == "systemd-run"
    assert "--pipe" in wrapped
    assert "--wait" in wrapped
    assert "--service-type=exec" in wrapped
    assert "--property=KillMode=control-group" in wrapped
    assert "--property=MemoryMax=2048M" in wrapped
    assert "--property=CPUQuota=150%" in wrapped
    assert "--property=TasksMax=512" in wrapped
    assert "--property=BindsTo=fdex.service" in wrapped
    assert "--setenv=FDEX_CODEX_PROVIDER_KEY" in wrapped
    assert "super-secret-provider-key" not in joined
    assert "$tenant" not in joined
    assert "$$tenant" in joined
    assert "%25" not in joined
    assert "%%25" in joined


def test_phase732_unit_name_is_stable_but_does_not_expose_owner_or_task(monkeypatch: pytest.MonkeyPatch) -> None:
    _ready(monkeypatch)
    first = isolation.build_codex_process_isolation("alice@example.com", "task-123", settings=_Settings())
    second = isolation.build_codex_process_isolation("alice@example.com", "task-123", settings=_Settings())
    other = isolation.build_codex_process_isolation("alice@example.com", "task-456", settings=_Settings())
    assert first is not None and second is not None and other is not None
    assert first.unit_name == second.unit_name
    assert first.unit_name != other.unit_name
    assert "alice" not in first.unit_name
    assert "task" not in first.unit_name
    assert first.unit_name.endswith(".service")


def test_phase732_fdex_provider_hosts_auto_require_process_isolation(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    fdex_client = CodexAppServerClient(
        ("/bin/true",),
        env={
            "FDEX_CODEX_PROVIDER_KEY": "secret",
            "CODEX_HOME": str(codex_home),
            "HOME": str(codex_home),
            "PATH": "/usr/bin:/bin",
        },
        cwd=tmp_path,
        client_version="test",
    )
    raw_transport_test = CodexAppServerClient(
        ("/bin/true",),
        env={"CODEX_HOME": str(codex_home), "HOME": str(codex_home), "PATH": "/usr/bin:/bin"},
        cwd=tmp_path,
        client_version="test",
    )
    assert fdex_client._auto_isolation is True
    assert raw_transport_test._auto_isolation is False


def test_phase732_close_path_targets_entire_systemd_unit() -> None:
    source = (Path(__file__).parents[1] / "app" / "codex_app_server.py").read_text(encoding="utf-8")
    controller = (Path(__file__).parents[1] / "app" / "codex_process_isolation.py").read_text(encoding="utf-8")
    assert "self.process_isolation.terminate_tree" in source
    assert '"--kill-who=all"' in controller
    assert '"--signal=SIGKILL"' in controller
    assert '"--property=KillMode=control-group"' in controller
    assert "proc.kill()" in source  # relay fallback remains after verified cgroup cleanup
