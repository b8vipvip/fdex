from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from app.agent_projects import AgentProjectStore
from app.github_web_oauth import GitHubWebOAuthError, GitHubWebOAuthStore
from app.user_portal_routes import router


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://fdex.example")
    monkeypatch.setenv("FDEX_GITHUB_WEB_OAUTH_CLIENT_ID", "Iv1.fdex-web-test")
    monkeypatch.setenv("FDEX_GITHUB_WEB_OAUTH_CLIENT_SECRET", "web-oauth-secret-for-tests")
    monkeypatch.setenv("FDEX_GITHUB_WEB_OAUTH_SCOPE", "repo read:user")
    monkeypatch.setenv("FDEX_GITHUB_WEB_OAUTH_FLOW_MINUTES", "10")


def _stores(tmp_path: Path) -> tuple[AgentProjectStore, GitHubWebOAuthStore]:
    projects = AgentProjectStore(tmp_path / "projects.db", tmp_path / "projects.key")
    web = GitHubWebOAuthStore(tmp_path / "web-oauth.db", projects)
    return projects, web


def test_web_oauth_state_is_owner_bound_hashed_and_pkce_verifier_encrypted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    projects, web = _stores(tmp_path)

    started = web.start("usr_1234567890abcdef12345678")
    query = parse_qs(urlparse(started["authorize_url"]).query)
    state = query["state"][0]

    assert query["client_id"] == ["Iv1.fdex-web-test"]
    assert query["redirect_uri"] == ["https://fdex.example/account/github/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) >= 43
    with web.db() as conn:
        row = conn.execute("SELECT * FROM github_web_oauth_flows WHERE id=?", (started["flow_id"],)).fetchone()
    assert row is not None
    assert row["state_hash"] == hashlib.sha256(state.encode()).hexdigest()
    assert state not in str(row["state_hash"])
    assert "code_challenge" not in str(dict(row))
    verifier = projects.decrypt(str(row["verifier_cipher"]))
    assert verifier
    assert verifier not in str(row["verifier_cipher"])


def test_web_oauth_callback_cannot_be_completed_by_another_fdex_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    _, web = _stores(tmp_path)
    started = web.start("usr_1234567890abcdef12345678")
    state = parse_qs(urlparse(started["authorize_url"]).query)["state"][0]

    with pytest.raises(GitHubWebOAuthError, match="状态无效"):
        web.complete("usr_abcdef1234567890abcdef12", state=state, code="temporary-code")


def test_web_oauth_exchanges_code_and_returns_only_safe_connection_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    projects, web = _stores(tmp_path)
    started = web.start("usr_1234567890abcdef12345678")
    state = parse_qs(urlparse(started["authorize_url"]).query)["state"][0]
    captured: dict[str, str] = {}

    def exchange(form: dict[str, str]) -> dict[str, object]:
        captured.update(form)
        return {"access_token": "user-github-oauth-secret", "scope": "repo read:user", "token_type": "bearer"}

    def github_json(token: str, url: str, **kwargs):
        assert token == "user-github-oauth-secret"
        return {"id": 42, "login": "octocat"}

    saved: dict[str, object] = {}

    def save(owner_id: str, profile: dict[str, object], token_result: dict[str, object], *, client_id: str = ""):
        saved.update({"owner_id": owner_id, "profile": profile, "token_result": token_result, "client_id": client_id})
        return {"id": 7, "owner_id": owner_id, "login": "octocat", "name": "GitHub · octocat", "auth_type": "oauth"}

    monkeypatch.setattr(web, "_exchange", exchange)
    monkeypatch.setattr(projects, "_github_json", github_json)
    monkeypatch.setattr(projects, "_save_oauth_connection", save)

    connection = web.complete("usr_1234567890abcdef12345678", state=state, code="temporary-code")

    assert captured["client_id"] == "Iv1.fdex-web-test"
    assert captured["client_secret"] == "web-oauth-secret-for-tests"
    assert captured["code"] == "temporary-code"
    assert captured["redirect_uri"] == "https://fdex.example/account/github/callback"
    assert captured["code_verifier"]
    assert saved["owner_id"] == "usr_1234567890abcdef12345678"
    assert connection["login"] == "octocat"
    assert "token" not in connection
    with web.db() as conn:
        row = conn.execute("SELECT status,verifier_cipher,connection_id FROM github_web_oauth_flows WHERE id=?", (started["flow_id"],)).fetchone()
    assert row is not None
    assert row["status"] == "authorized"
    assert row["verifier_cipher"] == ""
    assert row["connection_id"] == 7


def test_user_portal_routes_are_separate_from_admin_and_cover_permissions() -> None:
    methods = {(route.path, tuple(sorted(route.methods or []))) for route in router.routes}
    assert ("/account/login", ("GET",)) in methods
    assert ("/account/login", ("POST",)) in methods
    assert ("/account/github", ("GET",)) in methods
    assert ("/account/github/connect", ("POST",)) in methods
    assert ("/account/github/callback", ("GET",)) in methods
    assert ("/account/github/projects", ("POST",)) in methods
    assert ("/account/github/projects/{project_id}", ("POST",)) in methods
    assert ("/account/github/projects/{project_id}/delete", ("POST",)) in methods
    assert all(not route.path.startswith("/admin") for route in router.routes)


def test_android_github_setup_no_longer_collects_or_polls_github_credentials() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "app/src/main/java/com/b8vipvip/fdex/ui/GitHubProjectSetup.kt").read_text(encoding="utf-8")
    assert "/account/github" in source
    assert "GitHub Token" not in source
    assert "startGitHubDeviceFlow" not in source
    assert "pollGitHubDeviceFlow" not in source
    assert "access token / refresh token" in source
