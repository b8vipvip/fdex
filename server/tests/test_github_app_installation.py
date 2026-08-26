from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app import agent_projects as legacy_agent_projects
from app.github_app import GitHubAppClient, GitHubAppError
from app.github_app_agent_projects import GitHubAppAgentProjectStore
from app.github_app_bootstrap import install_github_app_project_store
from app.github_app_flow import GitHubAppFlowError, GitHubAppInstallationFlowStore
from app.github_app_portal_routes import router as github_app_router

OWNER_A = "usr_1234567890abcdef12345678"
OWNER_B = "usr_abcdef1234567890abcdef12"


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _configure_app(monkeypatch: pytest.MonkeyPatch, *, private_key_b64: str = "dGVzdA==") -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://fdex.example")
    monkeypatch.setenv("FDEX_GITHUB_APP_ID", "12345")
    monkeypatch.setenv("FDEX_GITHUB_APP_SLUG", "fdex-test")
    monkeypatch.setenv("FDEX_GITHUB_APP_CLIENT_ID", "Iv23.fdex-app-test")
    monkeypatch.setenv("FDEX_GITHUB_APP_CLIENT_SECRET", "github-app-client-secret")
    monkeypatch.setenv("FDEX_GITHUB_APP_PRIVATE_KEY_PATH", "")
    monkeypatch.setenv("FDEX_GITHUB_APP_PRIVATE_KEY_B64", private_key_b64)
    monkeypatch.setenv("FDEX_GITHUB_APP_FLOW_MINUTES", "10")


def _installation(*, installation_id: int = 9001, account_id: int = 42, login: str = "octocat", pull_requests: str = "write") -> dict[str, object]:
    return {
        "id": installation_id,
        "app_id": 12345,
        "app_slug": "fdex-test",
        "account": {"id": account_id, "login": login, "type": "User"},
        "repository_selection": "selected",
        "permissions": {"contents": "write", "pull_requests": pull_requests, "metadata": "read"},
        "suspended_at": None,
    }


class FakeGitHubAppClient:
    def __init__(self, installation: dict[str, object] | None = None) -> None:
        self.installation = installation or _installation()
        self.installation_tokens: list[tuple[int, str, dict[str, str] | None]] = []
        self.find_error: Exception | None = None

    def ensure_ready(self) -> None:
        return None

    def authorize_url(self, *, state: str, challenge: str) -> str:
        return "https://github.test/oauth?" + f"state={state}&challenge={challenge}"

    def install_url(self, *, state: str) -> str:
        return "https://github.test/install?" + f"state={state}"

    def exchange_user_code(self, *, code: str, verifier: str) -> dict[str, object]:
        assert code == "temporary-code"
        assert verifier
        return {"access_token": "temporary-user-token"}

    def user_profile(self, user_token: str) -> dict[str, object]:
        assert user_token == "temporary-user-token"
        return {"id": 42, "login": "octocat"}

    def find_user_installation(self, user_token: str, installation_id: int) -> dict[str, object]:
        assert user_token == "temporary-user-token"
        if self.find_error is not None:
            raise self.find_error
        assert installation_id == int(self.installation["id"])
        return self.installation

    def get_installation(self, installation_id: int) -> dict[str, object]:
        assert installation_id == int(self.installation["id"])
        return self.installation

    def installation_token(
        self,
        installation_id: int,
        *,
        repository: str = "",
        permissions: dict[str, str] | None = None,
    ) -> str:
        self.installation_tokens.append((installation_id, repository, permissions))
        return "ephemeral-installation-token"

    def installation_repositories(self, installation_id: int, *, page: int = 1, per_page: int = 100) -> list[dict[str, object]]:
        assert installation_id == int(self.installation["id"])
        return [
            {
                "id": 101,
                "name": "alpha",
                "full_name": "octocat/alpha",
                "private": True,
                "default_branch": "main",
                "archived": False,
                "description": "installed repository",
                "updated_at": "2026-08-26T00:00:00Z",
            }
        ]


def _stores(tmp_path: Path, fake: FakeGitHubAppClient | None = None) -> tuple[GitHubAppAgentProjectStore, GitHubAppInstallationFlowStore, FakeGitHubAppClient]:
    projects = GitHubAppAgentProjectStore(tmp_path / "projects.db", tmp_path / "projects.key")
    projects.init()
    client = fake or FakeGitHubAppClient()
    flow = GitHubAppInstallationFlowStore(tmp_path / "flows.db", projects, client)  # type: ignore[arg-type]
    return projects, flow, client


def test_github_app_jwt_is_rs256_signed_and_short_lived(monkeypatch: pytest.MonkeyPatch) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    _configure_app(monkeypatch, private_key_b64=base64.b64encode(pem).decode("ascii"))

    token = GitHubAppClient().app_jwt(now=2_000_000_000)
    encoded_header, encoded_payload, encoded_signature = token.split(".")
    header = json.loads(_b64url_decode(encoded_header))
    payload = json.loads(_b64url_decode(encoded_payload))

    assert header == {"alg": "RS256", "typ": "JWT"}
    assert payload["iss"] == "12345"
    assert payload["iat"] == 1_999_999_940
    assert payload["exp"] == 2_000_000_540
    private_key.public_key().verify(
        _b64url_decode(encoded_signature),
        f"{encoded_header}.{encoded_payload}".encode("ascii"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_install_flow_is_owner_bound_and_erases_temporary_user_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_app(monkeypatch)
    projects, flow, _ = _stores(tmp_path)

    started = flow.start(OWNER_A)
    oauth_query = parse_qs(urlparse(started["authorize_url"]).query)
    oauth_state = oauth_query["state"][0]
    assert oauth_query["challenge"][0]

    with flow.db() as conn:
        row = conn.execute("SELECT * FROM github_app_flows WHERE id=?", (started["flow_id"],)).fetchone()
    assert row is not None
    assert row["oauth_state_hash"] == hashlib.sha256(oauth_state.encode()).hexdigest()
    assert oauth_state not in str(dict(row))
    assert projects.decrypt(str(row["verifier_cipher"]))
    assert row["user_token_cipher"] == ""

    with pytest.raises(GitHubAppFlowError, match="状态无效"):
        flow.complete_identity(OWNER_B, state=oauth_state, code="temporary-code")

    identity = flow.complete_identity(OWNER_A, state=oauth_state, code="temporary-code")
    install_state = parse_qs(urlparse(identity["install_url"]).query)["state"][0]
    with flow.db() as conn:
        pending = conn.execute("SELECT * FROM github_app_flows WHERE id=?", (started["flow_id"],)).fetchone()
    assert pending is not None
    assert pending["status"] == "install_pending"
    assert pending["verifier_cipher"] == ""
    assert projects.decrypt(str(pending["user_token_cipher"])) == "temporary-user-token"
    assert pending["install_state_hash"] == hashlib.sha256(install_state.encode()).hexdigest()

    connection = flow.complete_installation(OWNER_A, installation_id=9001, state=install_state)
    assert connection["auth_type"] == "github_app"
    assert connection["github_app_installation_id"] == "9001"
    assert connection["github_app_repository_selection"] == "selected"
    assert connection["token_configured"] is False
    assert connection["refresh_configured"] is False

    with flow.db() as conn:
        completed = conn.execute("SELECT * FROM github_app_flows WHERE id=?", (started["flow_id"],)).fetchone()
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["user_token_cipher"] == ""
    assert completed["verifier_cipher"] == ""

    raw = projects.get_connection(OWNER_A, int(connection["id"]), secret=True)
    assert raw["token"] == ""
    assert raw["refresh_token"] == ""


def test_spoofed_setup_installation_is_rejected_and_secret_is_cleared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_app(monkeypatch)
    fake = FakeGitHubAppClient()
    fake.find_error = GitHubAppError("not owned by this user")
    projects, flow, _ = _stores(tmp_path, fake)
    started = flow.start(OWNER_A)
    oauth_state = parse_qs(urlparse(started["authorize_url"]).query)["state"][0]
    identity = flow.complete_identity(OWNER_A, state=oauth_state, code="temporary-code")
    install_state = parse_qs(urlparse(identity["install_url"]).query)["state"][0]

    with pytest.raises(GitHubAppFlowError, match="not owned"):
        flow.complete_installation(OWNER_A, installation_id=9001, state=install_state)

    assert projects.list_connections(OWNER_A) == []
    with flow.db() as conn:
        failed = conn.execute("SELECT status,user_token_cipher,verifier_cipher FROM github_app_flows WHERE id=?", (started["flow_id"],)).fetchone()
    assert failed is not None
    assert failed["status"] == "error"
    assert failed["user_token_cipher"] == ""
    assert failed["verifier_cipher"] == ""


def test_installation_cannot_bridge_two_fdex_users(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_app(monkeypatch)
    projects, _, _ = _stores(tmp_path)
    projects.save_github_app_connection(OWNER_A, installer_user_id="42", installation=_installation())

    with pytest.raises(ValueError, match="另一个 FDEX"):
        projects.save_github_app_connection(OWNER_B, installer_user_id="42", installation=_installation())


def test_agent_mints_repo_scoped_ephemeral_token_without_persisting_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_app(monkeypatch)
    projects, _, fake = _stores(tmp_path)
    connection = projects.save_github_app_connection(OWNER_A, installer_user_id="42", installation=_installation())

    monkeypatch.setattr("app.github_app_agent_projects.GitHubAppClient", lambda: fake)
    token = projects.connection_token(
        OWNER_A,
        int(connection["id"]),
        repository="octocat/alpha",
        permissions={"contents": "write"},
    )

    assert token == "ephemeral-installation-token"
    assert fake.installation_tokens == [(9001, "octocat/alpha", {"contents": "write"})]
    with projects.db() as conn:
        raw = conn.execute("SELECT token_cipher,refresh_token_cipher FROM github_connections WHERE id=?", (connection["id"],)).fetchone()
    assert raw is not None
    assert raw["token_cipher"] == ""
    assert raw["refresh_token_cipher"] == ""


def test_installed_repo_list_and_project_permissions_are_double_gated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_app(monkeypatch)
    fake = FakeGitHubAppClient(_installation(pull_requests="read"))
    projects, _, _ = _stores(tmp_path, fake)
    connection = projects.save_github_app_connection(OWNER_A, installer_user_id="42", installation=fake.installation)
    monkeypatch.setattr("app.github_app_agent_projects.GitHubAppClient", lambda: fake)

    repositories = projects.list_repositories(OWNER_A, int(connection["id"]))
    assert [item["full_name"] for item in repositories] == ["octocat/alpha"]
    assert repositories[0]["can_push"] is True
    assert repositories[0]["can_pr"] is False

    project = projects.save_project(
        OWNER_A,
        name="alpha",
        repo_full_name="octocat/alpha",
        connection_id=int(connection["id"]),
        allow_push=True,
        allow_pr=False,
    )
    assert project["allow_push"] is True
    assert project["allow_pr"] is False

    with pytest.raises(ValueError, match="Pull requests"):
        projects.save_project(
            OWNER_A,
            name="alpha",
            repo_full_name="octocat/alpha",
            connection_id=int(connection["id"]),
            allow_push=True,
            allow_pr=True,
            project_id=int(project["id"]),
        )


def test_update_without_state_only_refreshes_already_bound_installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_app(monkeypatch)
    projects, flow, _ = _stores(tmp_path)
    with pytest.raises(GitHubAppFlowError, match="尚未绑定"):
        flow.complete_installation(OWNER_A, installation_id=9001, setup_action="update", state="")

    projects.save_github_app_connection(OWNER_A, installer_user_id="42", installation=_installation())
    refreshed = flow.complete_installation(OWNER_A, installation_id=9001, setup_action="update", state="")
    assert refreshed["github_app_installation_id"] == "9001"


def test_bootstrap_and_user_routes_expose_github_app_installation_flow() -> None:
    original = legacy_agent_projects.agent_project_store
    try:
        install_github_app_project_store()
        assert legacy_agent_projects.agent_project_store.__module__ == "app.github_app_agent_projects"
    finally:
        legacy_agent_projects.agent_project_store = original

    methods = {(route.path, tuple(sorted(route.methods or []))) for route in github_app_router.routes}
    assert ("/account/github/app/connect", ("POST",)) in methods
    assert ("/account/github/app/oauth/callback", ("GET",)) in methods
    assert ("/account/github/app/setup", ("GET",)) in methods
    assert ("/account/github/app/connections/{connection_id}/refresh", ("POST",)) in methods

    root = Path(__file__).resolve().parents[2]
    template = (root / "server/app/templates/user_github.html").read_text(encoding="utf-8")
    assert "/account/github/app/connect" in template
    assert "installation_id" in template
    assert "短期 installation token" in template
    assert "GitHub Token" in template  # wording explicitly says users do not paste one
    assert 'name="token"' not in template
