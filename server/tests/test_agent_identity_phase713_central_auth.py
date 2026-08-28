from pathlib import Path

from app.agent_identity_runtime import install_agent_identity_runtime
from app.central_auth import CentralAuthStore


def test_central_auth_legacy_company_field_is_forced_empty(tmp_path: Path) -> None:
    install_agent_identity_runtime()
    store = CentralAuthStore(tmp_path / "accounts.db")
    session = store.register(
        name="Test",
        email="test@example.com",
        password="password-123",
        company_name="old-company-value",
        device_name="pytest",
    )
    assert session["user"]["company_name"] == ""
    with store.db() as conn:
        assert conn.execute("SELECT company_name FROM users").fetchone()[0] == ""
