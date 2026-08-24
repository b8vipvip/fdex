from __future__ import annotations

from pathlib import Path

from app.agent_sandbox import SandboxLimits, SystemdExecutionSandbox


def test_systemd_sandbox_command_is_ephemeral_and_bounded(tmp_path: Path) -> None:
    sandbox = SystemdExecutionSandbox()
    sandbox.sandbox_root = (tmp_path / "sandboxes").resolve()
    worktree = tmp_path / "sandboxes" / "owners" / "acct-a" / "projects" / "1" / "worktrees" / "task-a"
    worktree.mkdir(parents=True)
    cwd = worktree / "server"
    cwd.mkdir()

    command = sandbox.build_systemd_command(
        owner_id="acct-a",
        task_id="task-123456",
        worktree=worktree,
        cwd=cwd,
        argv=("python", "-m", "pytest", "-q"),
        limits=SandboxLimits(memory_mb=1536, cpu_percent=120, pids_max=256, allow_network=False),
    )
    joined = " ".join(command)
    assert "--collect" in command
    assert "MemoryMax=1536M" in joined
    assert "CPUQuota=120%" in joined
    assert "TasksMax=256" in joined
    assert "PrivateNetwork=yes" in joined
    assert "ProtectSystem=strict" in joined
    assert "NoNewPrivileges=yes" in joined
    assert str(worktree) in joined
    assert "/etc" not in [part.removeprefix("ReadWritePaths=") for part in command]


def test_network_can_be_explicitly_enabled_per_project(tmp_path: Path) -> None:
    sandbox = SystemdExecutionSandbox()
    sandbox.sandbox_root = (tmp_path / "sandboxes").resolve()
    worktree = tmp_path / "sandboxes" / "owners" / "acct-b" / "projects" / "2" / "worktrees" / "task-b"
    worktree.mkdir(parents=True)
    command = sandbox.build_systemd_command(
        owner_id="acct-b",
        task_id="task-b",
        worktree=worktree,
        cwd=worktree,
        argv=("gradle", "--version"),
        limits=SandboxLimits(memory_mb=3072, cpu_percent=200, pids_max=512, allow_network=True),
    )
    assert "PrivateNetwork=yes" not in " ".join(command)
