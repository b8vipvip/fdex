from __future__ import annotations

from pathlib import Path

import pytest

from app.account_cleanup import _safe_direct_owner_path


OWNER = "usr_phase726_cleanup"


def test_codex_home_owner_path_is_confined_to_configured_root(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    root.mkdir()
    target = _safe_direct_owner_path(root, OWNER)
    assert target == (root / OWNER).resolve()
    assert root.resolve() in target.parents


def test_codex_home_symlink_escape_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / OWNER).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="direct owner path"):
        _safe_direct_owner_path(root, OWNER)


def test_account_cleanup_erases_owner_scoped_codex_home_after_runtime_state() -> None:
    source = (Path(__file__).parents[1] / "app" / "account_cleanup.py").read_text(encoding="utf-8")
    assert "settings.fdex_agent_codex_home_root" in source
    assert "codex_home_target = _safe_direct_owner_path(codex_home_root, clean)" in source
    assert "shutil.rmtree(codex_home_target)" in source
    assert '"codex_home_directories": codex_home_removed' in source
    # Lease/database erasure precedes filesystem deletion so no active local capability remains
    # while official Runtime state is being removed.
    assert source.index("remote_mcp_lease_store().delete_owner(clean)") < source.index("shutil.rmtree(codex_home_target)")
    assert source.index("codex_host_store().delete_owner(clean)") < source.index("shutil.rmtree(codex_home_target)")
