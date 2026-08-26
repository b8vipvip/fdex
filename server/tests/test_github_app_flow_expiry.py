from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.github_app_agent_projects import GitHubAppAgentProjectStore
from app.github_app_flow import GitHubAppInstallationFlowStore


class NoopClient:
    def ensure_ready(self) -> None:
        return None


def test_expired_install_flow_scrubs_temporary_github_user_token(tmp_path: Path) -> None:
    projects = GitHubAppAgentProjectStore(tmp_path / "projects.db", tmp_path / "projects.key")
    projects.init()
    flow = GitHubAppInstallationFlowStore(tmp_path / "flows.db", projects, NoopClient())  # type: ignore[arg-type]
    flow.init()
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
    with flow.db() as conn:
        conn.execute(
            """INSERT INTO github_app_flows(
                   id,owner_id,oauth_state_hash,install_state_hash,verifier_cipher,user_token_cipher,
                   github_user_id,github_login,status,created_at,expires_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "expired-flow",
                "usr_1234567890abcdef12345678",
                "oauth-state-hash",
                "install-state-hash",
                projects.encrypt("pkce-verifier"),
                projects.encrypt("temporary-user-token"),
                "42",
                "octocat",
                "install_pending",
                expired,
                expired,
            ),
        )

    # init() is intentionally safe to call from the periodic janitor and scrubs every owner.
    flow.init()
    with flow.db() as conn:
        row = conn.execute(
            "SELECT status,verifier_cipher,user_token_cipher,error FROM github_app_flows WHERE id='expired-flow'"
        ).fetchone()
    assert row is not None
    assert row["status"] == "expired"
    assert row["verifier_cipher"] == ""
    assert row["user_token_cipher"] == ""
    assert row["error"] == "expired"
