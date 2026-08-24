from __future__ import annotations

from pathlib import Path

from app.agent_accounts import AgentAccountStore


def test_account_tokens_are_unique_and_scoped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FDEX_AGENT_DEFAULT_OWNER", "local")
    store = AgentAccountStore(tmp_path / "accounts.db")

    first, first_token = store.enroll("first")
    second, second_token = store.enroll("second")

    assert first["owner_id"] == "local"
    assert second["owner_id"] != first["owner_id"]
    assert first_token != second_token
    assert store.authenticate(first_token)["owner_id"] == first["owner_id"]
    assert store.authenticate(second_token)["owner_id"] == second["owner_id"]
    assert store.authenticate("wrong-token") is None


def test_account_database_never_stores_plain_token(tmp_path: Path) -> None:
    store = AgentAccountStore(tmp_path / "accounts.db")
    account, token = store.enroll("private", preferred_owner_id="acct-test")
    assert account["owner_id"] == "acct-test"
    raw = (tmp_path / "accounts.db").read_bytes()
    assert token.encode("utf-8") not in raw
