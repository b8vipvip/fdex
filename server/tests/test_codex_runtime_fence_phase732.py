from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from app import codex_runtime_fence as fence
from app import codex_runtime_switch as runtime_switch


def test_switch_fence_blocks_new_launch_fence_until_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(fence, "_LOCK_PATH", tmp_path / "runtime.lock")
    monkeypatch.setattr(fence, "_recorded_runtime_path", lambda: Path("/opt/codex/current").resolve())

    switch_entered = threading.Event()
    allow_switch_exit = threading.Event()
    launch_entered = threading.Event()

    def switcher() -> None:
        with fence.runtime_switch_fence():
            switch_entered.set()
            allow_switch_exit.wait(timeout=3.0)

    def launcher() -> None:
        switch_entered.wait(timeout=3.0)
        with fence.runtime_launch_fence("/opt/codex/current"):
            launch_entered.set()

    first = threading.Thread(target=switcher, daemon=True)
    second = threading.Thread(target=launcher, daemon=True)
    first.start()
    assert switch_entered.wait(timeout=2.0)
    second.start()
    time.sleep(0.08)
    assert launch_entered.is_set() is False
    allow_switch_exit.set()
    first.join(timeout=2.0)
    second.join(timeout=2.0)
    assert launch_entered.is_set() is True


def test_stale_runtime_path_is_rejected_before_exec_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(fence, "_LOCK_PATH", tmp_path / "runtime.lock")
    monkeypatch.setattr(fence, "_recorded_runtime_path", lambda: Path("/opt/codex/new").resolve())
    with pytest.raises(fence.CodexRuntimeFenceError, match="stale launch was rejected"):
        with fence.runtime_launch_fence("/opt/codex/old"):
            raise AssertionError("stale Host must never enter the exec boundary")


def test_no_managed_switch_record_preserves_existing_environment_based_pin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(fence, "_LOCK_PATH", tmp_path / "runtime.lock")
    monkeypatch.setattr(fence, "_ACTIVE_PATH", tmp_path / "runtime.active")
    # The fence deliberately does not infer the initial pin source. A service-level Environment=
    # FDEX_AGENT_CODEX_BIN deployment remains valid until FDEX performs its first managed switch.
    with fence.runtime_launch_fence("/operator/environment/codex"):
        pass


def test_managed_switch_record_is_atomic_and_exact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(fence, "_ACTIVE_PATH", tmp_path / "runtime.active")
    fence.record_switched_runtime("/srv/fdex/codex/releases/1.2.3/codex")
    assert fence._recorded_runtime_path() == Path("/srv/fdex/codex/releases/1.2.3/codex").resolve()
    assert (tmp_path / "runtime.active").read_text(encoding="utf-8").strip().endswith("/1.2.3/codex")


def test_upgrade_download_and_verification_happen_before_exclusive_activation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    binary = tmp_path / "codex"
    binary.write_text("fake", encoding="utf-8")

    monkeypatch.setattr(
        runtime_switch.manager,
        "fetch_release",
        lambda tag=None: events.append("fetch") or {"tag": "rust-v9.9.9", "version": "9.9.9"},
    )
    monkeypatch.setattr(
        runtime_switch.manager,
        "install_release",
        lambda release: events.append("install")
        or {
            "tag": release["tag"],
            "version": release["version"],
            "path": str(binary),
            "binary_sha256": "a" * 64,
        },
    )

    class _Fence:
        def __enter__(self):
            events.append("lock")

        def __exit__(self, exc_type, exc, tb):
            events.append("unlock")

    monkeypatch.setattr(runtime_switch, "runtime_switch_fence", lambda: _Fence())
    monkeypatch.setattr(
        runtime_switch.manager,
        "_activate_pin",
        lambda pin, current, action: events.append("activate") or {"active_pin": pin},
    )
    monkeypatch.setattr(
        runtime_switch,
        "record_switched_runtime",
        lambda path: events.append("record"),
    )

    result = runtime_switch.upgrade_runtime_safely()
    assert result["active_pin"] == str(binary)
    assert events == ["fetch", "install", "lock", "activate", "record", "unlock"]


def test_empty_rollback_target_uses_same_system_before_bundled_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_switch.shutil, "which", lambda name: "/usr/local/bin/codex")
    seen: list[Path] = []
    monkeypatch.setattr(
        runtime_switch.manager,
        "validate_runtime_binary",
        lambda path: seen.append(Path(path)) or {"path": str(path), "version": "1.2.3"},
    )
    result = runtime_switch._fallback_validation()
    assert result["version"] == "1.2.3"
    assert seen == [Path("/usr/local/bin/codex").resolve()]


def test_trusted_wrapper_and_admin_routes_use_runtime_fence() -> None:
    root = Path(__file__).parents[1] / "app"
    wrapper = (root / "codex_env_wrapper.py").read_text(encoding="utf-8")
    routes = (root / "codex_runtime_admin_routes.py").read_text(encoding="utf-8")
    switch_source = (root / "codex_runtime_switch.py").read_text(encoding="utf-8")
    assert "runtime_launch_fence(real_codex)" in wrapper
    assert wrapper.index("runtime_launch_fence(real_codex)") < wrapper.index("os.execve(real_codex")
    assert "upgrade_runtime_safely" in routes
    assert "rollback_runtime_safely" in routes
    assert "upgrade_runtime(requested)" not in routes
    assert "rollback_runtime()" not in routes
    assert "record_switched_runtime(binary)" in switch_source
    assert "record_switched_runtime(Path(str(validation[\"path\"])))" in switch_source
