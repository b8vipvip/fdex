from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from app.config import fresh_settings
from app.github_app import GitHubAppClient, GitHubAppError
from app.github_app_agent_projects import GitHubAppAgentProjectStore, agent_project_store


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat((value or "").strip().replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class GitHubAppFlowError(RuntimeError):
    pass


class GitHubAppInstallationFlowStore:
    """Short-lived identity proof used to bind a GitHub App installation to one FDEX user.

    GitHub warns that installation_id arriving at a setup URL is untrusted. FDEX therefore first
    performs a GitHub App user authorization and keeps that user token encrypted only for the
    short installation window. The setup callback is accepted only if /user/installations proves
    that the same GitHub user can access the installation. The user token is then erased.
    """

    def __init__(
        self,
        path: Path | None = None,
        project_store: GitHubAppAgentProjectStore | None = None,
        client: GitHubAppClient | None = None,
    ) -> None:
        settings = fresh_settings()
        data = Path(settings.app_dir).expanduser().resolve() / "server" / "data"
        self.path = (path or data / "github-app-flows.db").resolve()
        self.projects = project_store or agent_project_store()
        self.client = client or GitHubAppClient()

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self.db() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS github_app_flows (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    oauth_state_hash TEXT NOT NULL UNIQUE,
                    install_state_hash TEXT NOT NULL DEFAULT '',
                    verifier_cipher TEXT NOT NULL DEFAULT '',
                    user_token_cipher TEXT NOT NULL DEFAULT '',
                    github_user_id TEXT NOT NULL DEFAULT '',
                    github_login TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'oauth_pending',
                    installation_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_github_app_flow_owner
                    ON github_app_flows(owner_id,created_at DESC);
                """
            )
        try:
            os.chmod(self.path.parent, 0o700)
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def start(self, owner_id: str) -> dict[str, str]:
        self.client.ensure_ready()
        owner_id = self._owner(owner_id)
        self.init()
        state = secrets.token_urlsafe(32)
        verifier = _b64url(secrets.token_bytes(48))
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        flow_id = secrets.token_hex(16)
        now = _now()
        expires = now + timedelta(minutes=fresh_settings().fdex_github_app_flow_minutes)
        with self.db() as conn:
            conn.execute(
                """UPDATE github_app_flows
                   SET status='expired',verifier_cipher='',user_token_cipher='',error='superseded'
                   WHERE owner_id=? AND status IN ('oauth_pending','install_pending')""",
                (owner_id,),
            )
            conn.execute(
                """INSERT INTO github_app_flows(
                       id,owner_id,oauth_state_hash,verifier_cipher,status,created_at,expires_at
                   ) VALUES(?,?,?,?,'oauth_pending',?,?)""",
                (flow_id, owner_id, _hash(state), self.projects.encrypt(verifier), _iso(now), _iso(expires)),
            )
            self._trim(conn, owner_id)
        return {"flow_id": flow_id, "authorize_url": self.client.authorize_url(state=state, challenge=challenge)}

    def complete_identity(self, owner_id: str, *, state: str, code: str) -> dict[str, str]:
        owner_id = self._owner(owner_id)
        state_hash = _hash((state or "").strip())
        code = (code or "").strip()
        if not state or not code:
            raise GitHubAppFlowError("GitHub 身份授权返回参数不完整")
        self.init()
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM github_app_flows WHERE owner_id=? AND oauth_state_hash=?",
                (owner_id, state_hash),
            ).fetchone()
            row = self._require_pending(conn, row, "oauth_pending")
            verifier = self.projects.decrypt(str(row["verifier_cipher"] or ""))

        try:
            token_result = self.client.exchange_user_code(code=code, verifier=verifier)
            user_token = str(token_result.get("access_token") or "").strip()
            profile = self.client.user_profile(user_token)
            user_id = str(profile.get("id") or "").strip()
            login = str(profile.get("login") or "").strip()
            if not user_id or not login:
                raise GitHubAppError("GitHub 用户身份不完整")
            install_state = secrets.token_urlsafe(32)
            with self.db() as conn:
                updated = conn.execute(
                    """UPDATE github_app_flows
                       SET install_state_hash=?,verifier_cipher='',user_token_cipher=?,github_user_id=?,
                           github_login=?,status='install_pending',error=''
                       WHERE id=? AND owner_id=? AND status='oauth_pending'""",
                    (
                        _hash(install_state),
                        self.projects.encrypt(user_token),
                        user_id,
                        login,
                        row["id"],
                        owner_id,
                    ),
                )
                if updated.rowcount != 1:
                    raise GitHubAppFlowError("GitHub 授权流程已失效，请重新连接")
            return {"flow_id": str(row["id"]), "install_url": self.client.install_url(state=install_state)}
        except (GitHubAppError, ValueError, RuntimeError) as exc:
            self._fail(owner_id, str(row["id"]), str(exc))
            raise GitHubAppFlowError(str(exc)) from exc

    def complete_installation(
        self,
        owner_id: str,
        *,
        installation_id: int,
        state: str = "",
        setup_action: str = "install",
    ) -> dict[str, Any]:
        owner_id = self._owner(owner_id)
        if int(installation_id) <= 0:
            raise GitHubAppFlowError("GitHub App installation id 无效")
        self.init()
        clean_state = (state or "").strip()

        # GitHub may send the setup URL again after a user changes repository selection. In that
        # case there may be no new state, but only an installation already bound to this FDEX user
        # may be refreshed. A new installation always requires the identity-bound state proof.
        if not clean_state:
            if (setup_action or "").strip() != "update":
                raise GitHubAppFlowError("缺少 GitHub 安装校验状态，请从 FDEX 用户中心重新连接")
            existing = self.projects.find_github_app_connection(owner_id, int(installation_id))
            if existing is None:
                raise GitHubAppFlowError("该 GitHub App 安装尚未绑定当前 FDEX 账号")
            try:
                installation = self.client.get_installation(int(installation_id))
                return self.projects.save_github_app_connection(
                    owner_id,
                    installer_user_id=str(existing.get("github_user_id") or ""),
                    installation=installation,
                )
            except (GitHubAppError, ValueError, RuntimeError) as exc:
                raise GitHubAppFlowError(str(exc)) from exc

        install_state_hash = _hash(clean_state)
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM github_app_flows WHERE owner_id=? AND install_state_hash=?",
                (owner_id, install_state_hash),
            ).fetchone()
            row = self._require_pending(conn, row, "install_pending")
            user_token = self.projects.decrypt(str(row["user_token_cipher"] or ""))
            if not user_token:
                raise GitHubAppFlowError("GitHub 临时身份凭据已失效，请重新连接")

        try:
            proof = self.client.find_user_installation(user_token, int(installation_id))
            installation = self.client.get_installation(int(installation_id))
            proof_account = proof.get("account") if isinstance(proof.get("account"), dict) else {}
            canonical_account = installation.get("account") if isinstance(installation.get("account"), dict) else {}
            if str(proof_account.get("id") or "") != str(canonical_account.get("id") or ""):
                raise GitHubAppError("GitHub App 安装账号验证失败")
            connection = self.projects.save_github_app_connection(
                owner_id,
                installer_user_id=str(row["github_user_id"] or ""),
                installation=installation,
            )
            with self.db() as conn:
                updated = conn.execute(
                    """UPDATE github_app_flows
                       SET status='completed',user_token_cipher='',verifier_cipher='',installation_id=?,
                           completed_at=?,error=''
                       WHERE id=? AND owner_id=? AND status='install_pending'""",
                    (str(int(installation_id)), _iso(_now()), row["id"], owner_id),
                )
                if updated.rowcount != 1:
                    raise GitHubAppFlowError("GitHub 安装流程已失效，请重新连接")
            return connection
        except (GitHubAppError, ValueError, RuntimeError) as exc:
            self._fail(owner_id, str(row["id"]), str(exc))
            raise GitHubAppFlowError(str(exc)) from exc

    def delete_owner(self, owner_id: str) -> int:
        owner_id = self._owner(owner_id)
        self.init()
        with self.db() as conn:
            count = int(conn.execute("SELECT COUNT(*) FROM github_app_flows WHERE owner_id=?", (owner_id,)).fetchone()[0])
            conn.execute("DELETE FROM github_app_flows WHERE owner_id=?", (owner_id,))
        return count

    @staticmethod
    def _require_pending(
        conn: sqlite3.Connection,
        row: sqlite3.Row | None,
        expected_status: str,
    ) -> sqlite3.Row:
        if row is None:
            raise GitHubAppFlowError("GitHub 授权状态无效，请重新连接")
        if str(row["status"] or "") != expected_status:
            raise GitHubAppFlowError("该 GitHub 授权流程已经使用或失效")
        expires = _parse(str(row["expires_at"] or ""))
        if expires is None or expires <= _now():
            conn.execute(
                """UPDATE github_app_flows
                   SET status='expired',verifier_cipher='',user_token_cipher='',error='expired'
                   WHERE id=?""",
                (row["id"],),
            )
            raise GitHubAppFlowError("GitHub 授权已过期，请重新连接")
        return row

    def _fail(self, owner_id: str, flow_id: str, error: str) -> None:
        with self.db() as conn:
            conn.execute(
                """UPDATE github_app_flows
                   SET status='error',verifier_cipher='',user_token_cipher='',error=?
                   WHERE id=? AND owner_id=?""",
                ((error or "github_app_error")[:500], flow_id, owner_id),
            )

    @staticmethod
    def _trim(conn: sqlite3.Connection, owner_id: str) -> None:
        conn.execute(
            """DELETE FROM github_app_flows WHERE owner_id=? AND id NOT IN (
                   SELECT id FROM github_app_flows WHERE owner_id=? ORDER BY created_at DESC LIMIT 20
               )""",
            (owner_id, owner_id),
        )

    @staticmethod
    def _owner(owner_id: str) -> str:
        clean = (owner_id or "").strip()
        if not clean.startswith("usr_") or len(clean) < 12:
            raise GitHubAppFlowError("无效的 FDEX 用户")
        return clean
