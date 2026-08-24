from pathlib import Path

import pytest

from app.central_auth import CentralAuthStore


def test_register_login_refresh_and_revoke(tmp_path: Path) -> None:
    store = CentralAuthStore(tmp_path / "accounts.db")
    registered = store.register(
        name="Test User",
        email="USER@example.com",
        password="correct horse battery staple",
        company_name="FDEX Test",
        device_name="pytest",
    )
    user = registered["user"]
    assert user["id"].startswith("usr_")
    assert user["email"] == "user@example.com"
    assert "password_hash" not in user
    assert store.authenticate_access(registered["access_token"])["id"] == user["id"]

    logged_in = store.login(email="user@example.com", password="correct horse battery staple", device_name="second")
    assert logged_in["user"]["id"] == user["id"]

    refreshed = store.refresh(logged_in["refresh_token"])
    assert refreshed["access_token"] != logged_in["access_token"]
    assert refreshed["refresh_token"] != logged_in["refresh_token"]
    assert store.authenticate_access(logged_in["access_token"]) is None
    assert store.authenticate_access(refreshed["access_token"])["id"] == user["id"]

    store.revoke_access(refreshed["access_token"])
    assert store.authenticate_access(refreshed["access_token"]) is None


def test_duplicate_email_and_wrong_password_are_rejected(tmp_path: Path) -> None:
    store = CentralAuthStore(tmp_path / "accounts.db")
    store.register(name="A", email="a@example.com", password="password-123", device_name="one")
    with pytest.raises(ValueError, match="已经注册"):
        store.register(name="B", email="A@example.com", password="password-456", device_name="two")
    with pytest.raises(ValueError, match="邮箱或密码错误"):
        store.login(email="a@example.com", password="wrong-password", device_name="three")


def test_tokens_are_hashed_in_database(tmp_path: Path) -> None:
    store = CentralAuthStore(tmp_path / "accounts.db")
    session = store.register(name="A", email="a@example.com", password="password-123", device_name="one")
    with store.db() as conn:
        row = conn.execute("SELECT access_hash,refresh_hash FROM user_sessions").fetchone()
        password = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    assert session["access_token"] not in str(row["access_hash"])
    assert session["refresh_token"] not in str(row["refresh_hash"])
    assert "password-123" not in password
    assert password.startswith("scrypt$")
