from __future__ import annotations

import hashlib
import io
import os
import tarfile
from pathlib import Path

import pytest

from app import codex_runtime_manager as manager


def _release(**updates: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "tag_name": "rust-v0.151.0",
        "draft": False,
        "prerelease": False,
        "published_at": "2026-08-29T09:55:39Z",
        "assets": [
            {
                "id": 535048201,
                "name": "codex-x86_64-unknown-linux-musl.tar.gz",
                "size": 103_937_941,
                "digest": "sha256:" + "a" * 64,
                "browser_download_url": "https://github.com/openai/codex/releases/download/rust-v0.151.0/codex-x86_64-unknown-linux-musl.tar.gz",
            }
        ],
    }
    raw.update(updates)
    return raw


def _patch_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "codex-runtimes"
    monkeypatch.setattr(manager, "_ROOT", root)
    monkeypatch.setattr(manager, "_RELEASES", root / "releases")
    monkeypatch.setattr(manager, "_STATE", root / "state.json")


def _write_tar(path: Path, members: list[tuple[tarfile.TarInfo, bytes]]) -> None:
    with tarfile.open(path, "w:gz") as bundle:
        for info, payload in members:
            bundle.addfile(info, io.BytesIO(payload) if info.isfile() else None)


def _regular(name: str, payload: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.mode = 0o755
    info.size = len(payload)
    return info, payload


def test_release_metadata_accepts_only_exact_official_linux_asset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager.platform, "machine", lambda: "x86_64")
    release = manager._validated_release(_release())
    assert release["tag"] == "rust-v0.151.0"
    assert release["version"] == "0.151.0"
    assert release["asset_id"] == 535048201
    assert release["asset_name"] == "codex-x86_64-unknown-linux-musl.tar.gz"
    assert release["binary_member"] == "codex-x86_64-unknown-linux-musl"
    assert release["asset_sha256"] == "a" * 64


def test_release_metadata_rejects_prerelease_wrong_url_missing_digest_and_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager.platform, "machine", lambda: "x86_64")
    with pytest.raises(manager.CodexRuntimeManagerError, match="draft/prerelease"):
        manager._validated_release(_release(prerelease=True))

    wrong_url = _release()
    wrong_url["assets"][0]["browser_download_url"] = "https://evil.example/codex.tar.gz"  # type: ignore[index]
    with pytest.raises(manager.CodexRuntimeManagerError, match="escaped"):
        manager._validated_release(wrong_url)

    missing_digest = _release()
    missing_digest["assets"][0]["digest"] = None  # type: ignore[index]
    with pytest.raises(manager.CodexRuntimeManagerError, match="sha256"):
        manager._validated_release(missing_digest)

    bad_size = _release()
    bad_size["assets"][0]["size"] = 1024  # type: ignore[index]
    with pytest.raises(manager.CodexRuntimeManagerError, match="size/id"):
        manager._validated_release(bad_size)


def test_release_metadata_maps_arm64_to_official_musl_asset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager.platform, "machine", lambda: "aarch64")
    raw = _release()
    raw["assets"] = [
        {
            "id": 535048000,
            "name": "codex-aarch64-unknown-linux-musl.tar.gz",
            "size": 90_000_000,
            "digest": "sha256:" + "b" * 64,
            "browser_download_url": "https://github.com/openai/codex/releases/download/rust-v0.151.0/codex-aarch64-unknown-linux-musl.tar.gz",
        }
    ]
    release = manager._validated_release(raw)
    assert release["binary_member"] == "codex-aarch64-unknown-linux-musl"


def test_safe_tar_extracts_only_expected_binary(tmp_path: Path) -> None:
    archive = tmp_path / "codex.tar.gz"
    payload = b"fake-codex-binary"
    readme = _regular("README.md", b"ignored")
    binary = _regular("codex-x86_64-unknown-linux-musl", payload)
    _write_tar(archive, [readme, binary])
    target = tmp_path / "codex"
    manager._extract_verified_binary(archive, "codex-x86_64-unknown-linux-musl", target)
    assert target.read_bytes() == payload
    assert os.access(target, os.X_OK)


def test_safe_tar_rejects_path_traversal_and_links(tmp_path: Path) -> None:
    binary = _regular("codex-x86_64-unknown-linux-musl", b"binary")

    traversal = tmp_path / "traversal.tar.gz"
    _write_tar(traversal, [_regular("../outside", b"bad"), binary])
    with pytest.raises(manager.CodexRuntimeManagerError, match="unsafe path"):
        manager._extract_verified_binary(traversal, "codex-x86_64-unknown-linux-musl", tmp_path / "out1")

    linked = tmp_path / "link.tar.gz"
    symlink = tarfile.TarInfo("harmless-link")
    symlink.type = tarfile.SYMTYPE
    symlink.linkname = "/etc/passwd"
    _write_tar(linked, [(symlink, b""), binary])
    with pytest.raises(manager.CodexRuntimeManagerError, match="links/devices"):
        manager._extract_verified_binary(linked, "codex-x86_64-unknown-linux-musl", tmp_path / "out2")


def test_safe_tar_rejects_ambiguous_expected_binaries(tmp_path: Path) -> None:
    archive = tmp_path / "ambiguous.tar.gz"
    _write_tar(
        archive,
        [
            _regular("codex-x86_64-unknown-linux-musl", b"one"),
            _regular("nested/codex-x86_64-unknown-linux-musl", b"two"),
        ],
    )
    with pytest.raises(manager.CodexRuntimeManagerError, match="exactly one"):
        manager._extract_verified_binary(archive, "codex-x86_64-unknown-linux-musl", tmp_path / "out")


def test_runtime_validation_requires_version_and_governance_app_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    binary = tmp_path / "codex"
    binary.write_bytes(b"binary")
    binary.chmod(0o755)
    calls: list[list[str]] = []

    def fake_run(_binary: Path, args: list[str], timeout: float = 15.0) -> tuple[int, str]:
        calls.append(args)
        if args == ["--version"]:
            return 0, "codex-cli 0.151.0"
        return 0, "codex app-server --listen stdio://"

    monkeypatch.setattr(manager, "_run_binary", fake_run)
    monkeypatch.setattr(manager, "codex_subagent_cli_overrides", lambda: ("features.collab=true", "features.multi_agent_v2={ enabled = true }"))
    result = manager.validate_runtime_binary(binary, "0.151.0")
    assert result["version"] == "0.151.0"
    assert calls[1][-2:] == ["app-server", "--help"]
    assert "features.multi_agent_v2={ enabled = true }" in calls[1]

    with pytest.raises(manager.CodexRuntimeManagerError, match="version mismatch"):
        manager.validate_runtime_binary(binary, "9.9.9")


def test_install_release_is_staged_hashed_and_immutable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_root(monkeypatch, tmp_path)
    payload = b"verified-runtime"
    archive_bytes = io.BytesIO()
    with tarfile.open(fileobj=archive_bytes, mode="w:gz") as bundle:
        info = tarfile.TarInfo("codex-x86_64-unknown-linux-musl")
        info.mode = 0o755
        info.size = len(payload)
        bundle.addfile(info, io.BytesIO(payload))
    archive_payload = archive_bytes.getvalue()
    release = {
        "tag": "rust-v0.151.0",
        "version": "0.151.0",
        "published_at": "2026-08-29T09:55:39Z",
        "asset_id": 1,
        "asset_name": "codex-x86_64-unknown-linux-musl.tar.gz",
        "binary_member": "codex-x86_64-unknown-linux-musl",
        "asset_url": "https://github.com/openai/codex/releases/download/rust-v0.151.0/codex-x86_64-unknown-linux-musl.tar.gz",
        "asset_size": len(archive_payload),
        "asset_sha256": hashlib.sha256(archive_payload).hexdigest(),
    }

    def fake_download(_release: dict[str, object], destination: Path) -> str:
        destination.write_bytes(archive_payload)
        return hashlib.sha256(archive_payload).hexdigest()

    monkeypatch.setattr(manager, "_download_release", fake_download)
    monkeypatch.setattr(
        manager,
        "validate_runtime_binary",
        lambda binary, expected_version=None: {
            "path": str(binary.resolve()),
            "version": expected_version or "0.151.0",
            "version_output": "codex-cli 0.151.0",
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        },
    )
    installed = manager.install_release(release)
    target = manager._RELEASES / "0.151.0"
    assert installed["version"] == "0.151.0"
    assert (target / "codex").read_bytes() == payload
    assert (target / "manifest.json").is_file()
    assert not any(path.name.startswith(".stage-") for path in manager._ROOT.iterdir())


def test_activation_kills_all_old_trees_before_writing_pin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_root(monkeypatch, tmp_path)
    events: list[str] = []
    monkeypatch.setattr(manager, "codex_process_isolation_status", lambda: {"enforced": True})
    monkeypatch.setattr(manager, "read_env", lambda: {"FDEX_AGENT_CODEX_BIN": "/old/codex"})
    monkeypatch.setattr(manager, "terminate_all_codex_trees", lambda: events.append("kill") or ["fdex-codex-a.service"])
    monkeypatch.setattr(manager, "write_env", lambda updates: events.append("write:" + updates["FDEX_AGENT_CODEX_BIN"]))
    monkeypatch.setattr(manager.get_settings, "cache_clear", lambda: None)
    monkeypatch.setattr(manager, "runtime_manager_status", lambda: {"active_pin": "/new/codex"})
    result = manager._activate_pin(
        "/new/codex",
        {"version": "0.151.0", "path": "/new/codex"},
        action="upgrade",
    )
    assert events == ["kill", "write:/new/codex"]
    assert result["active_pin"] == "/new/codex"
    state = manager._load_json(manager._STATE)
    assert state["previous_pin"] == "/old/codex"
    assert state["active_pin"] == "/new/codex"


def test_activation_fails_closed_before_env_write_when_isolation_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_root(monkeypatch, tmp_path)
    written: list[object] = []
    monkeypatch.setattr(manager, "codex_process_isolation_status", lambda: {"enforced": False, "reason": "no cgroup"})
    monkeypatch.setattr(manager, "write_env", lambda updates: written.append(updates))
    with pytest.raises(manager.CodexRuntimeManagerError, match="cgroup isolation"):
        manager._activate_pin("/new/codex", {"version": "0.151.0"}, action="upgrade")
    assert written == []


def test_runtime_admin_surface_and_routes_are_registered() -> None:
    root = Path(__file__).parents[1] / "app"
    parent = (root / "agent_admin_routes.py").read_text(encoding="utf-8")
    routes = (root / "codex_runtime_admin_routes.py").read_text(encoding="utf-8")
    template = (root / "templates" / "agent_runtime_manager.html").read_text(encoding="utf-8")
    base = (root / "templates" / "base.html").read_text(encoding="utf-8")
    assert "router.include_router(codex_runtime_admin_router)" in parent
    assert 'prefix="/runtime"' in routes
    assert 'action="/admin/agent/runtime/upgrade"' in template
    assert 'action="/admin/agent/runtime/rollback"' in template
    assert "SHA-256" in template
    assert 'href="/admin/agent/runtime"' in base
