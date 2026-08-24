from pathlib import Path

import pytest

from app.central_auth import AuthRateLimitError, CentralAuthStore


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


def test_user_admin_can_list_revoke_disable_and_restore(tmp_path: Path) -> None:
    store = CentralAuthStore(tmp_path / "accounts.db")
    first = store.register(name="Admin Target", email="target@example.com", password="password-123", device_name="phone")
    user_id = str(first["user"]["id"])
    second = store.login(email="target@example.com", password="password-123", device_name="tablet")

    users = store.list_users()
    assert len(users) == 1
    assert users[0]["id"] == user_id
    assert users[0]["session_count"] == 2
    assert users[0]["active_session_count"] == 2
    assert store.user_stats() == {"total": 1, "enabled": 1, "disabled": 0, "active_sessions": 2}

    sessions = store.list_sessions(user_id)
    assert {session["device_name"] for session in sessions} == {"phone", "tablet"}
    assert all("access_hash" not in session and "refresh_hash" not in session for session in sessions)
    assert all(session["active"] for session in sessions)

    assert store.revoke_user_sessions(user_id) == 2
    assert store.authenticate_access(first["access_token"]) is None
    assert store.authenticate_access(second["access_token"]) is None
    assert store.list_users()[0]["active_session_count"] == 0

    third = store.login(email="target@example.com", password="password-123", device_name="phone-again")
    assert store.authenticate_access(third["access_token"])["id"] == user_id
    disabled = store.set_user_enabled(user_id, False)
    assert disabled["enabled"] is False
    assert store.authenticate_access(third["access_token"]) is None
    with pytest.raises(ValueError, match="邮箱或密码错误"):
        store.login(email="target@example.com", password="password-123", device_name="blocked")

    restored = store.set_user_enabled(user_id, True)
    assert restored["enabled"] is True
    fresh = store.login(email="target@example.com", password="password-123", device_name="restored")
    assert store.authenticate_access(fresh["access_token"])["id"] == user_id


def test_change_password_keeps_current_session_and_revokes_other_devices(tmp_path: Path) -> None:
    store = CentralAuthStore(tmp_path / "accounts.db")
    phone = store.register(name="A", email="a@example.com", password="password-123", device_name="phone")
    tablet = store.login(email="a@example.com", password="password-123", device_name="tablet")
    user_id = str(phone["user"]["id"])

    revoked = store.change_password(
        user_id,
        "password-123",
        "new-password-456",
        current_session_id=str(phone["session_id"]),
    )
    assert revoked == 1
    assert store.authenticate_access(phone["access_token"])["id"] == user_id
    assert store.authenticate_access(tablet["access_token"]) is None
    with pytest.raises(ValueError, match="邮箱或密码错误"):
        store.login(email="a@example.com", password="password-123", device_name="old-password")
    assert store.login(email="a@example.com", password="new-password-456", device_name="new-password")["user"]["id"] == user_id


def test_password_reset_code_rotates_password_and_all_sessions(tmp_path: Path) -> None:
    store = CentralAuthStore(tmp_path / "accounts.db")
    original = store.register(name="A", email="a@example.com", password="password-123", device_name="phone")
    store.login(email="a@example.com", password="password-123", device_name="tablet")
    reset = store.create_password_reset_code("a@example.com", client_ip="1.2.3.4")
    assert reset is not None
    _, code = reset

    store.confirm_password_reset("a@example.com", code, "reset-password-789", client_ip="1.2.3.4")
    assert store.authenticate_access(original["access_token"]) is None
    with pytest.raises(ValueError, match="邮箱或密码错误"):
        store.login(email="a@example.com", password="password-123", device_name="old")
    assert store.login(email="a@example.com", password="reset-password-789", device_name="new")["user"]["email"] == "a@example.com"
    with pytest.raises(ValueError, match="验证码错误或已过期"):
        store.confirm_password_reset("a@example.com", code, "another-password-000")


def test_repeated_login_failures_are_rate_limited_and_audited(tmp_path: Path) -> None:
    store = CentralAuthStore(tmp_path / "accounts.db")
    registered = store.register(name="A", email="a@example.com", password="password-123", device_name="phone")
    user_id = str(registered["user"]["id"])
    for _ in range(4):
        with pytest.raises(ValueError, match="邮箱或密码错误"):
            store.login(email="a@example.com", password="wrong-password", device_name="attacker", client_ip="9.9.9.9")
    with pytest.raises(AuthRateLimitError):
        store.login(email="a@example.com", password="wrong-password", device_name="attacker", client_ip="9.9.9.9")
    assert store.login_retry_after("a@example.com", "9.9.9.9") > 0
    events = store.security_events(user_id)
    assert any(event["event"] == "login_failed" for event in events)


def test_account_delete_removes_identity_and_allows_clean_reregistration(tmp_path: Path) -> None:
    store = CentralAuthStore(tmp_path / "accounts.db")
    session = store.register(name="A", email="a@example.com", password="password-123", device_name="phone")
    user_id = str(session["user"]["id"])
    deleted = store.delete_account(user_id, "password-123")
    assert deleted["email"] == "a@example.com"
    assert store.authenticate_access(session["access_token"]) is None
    replacement = store.register(name="A2", email="a@example.com", password="password-456", device_name="new-phone")
    assert replacement["user"]["id"] != user_id
