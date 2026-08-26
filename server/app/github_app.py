from __future__ import annotations

import base64
import binascii
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.config import fresh_settings

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_URL = "https://api.github.com"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class GitHubAppError(RuntimeError):
    pass


class GitHubAppClient:
    """GitHub App service client with no persistent installation-token cache.

    FDEX stores an installation id as the durable delegated identity. Every Git/REST operation
    mints a short-lived installation token from the app private key. GitHub documents these
    installation tokens as one-hour credentials; this class deliberately returns them only to
    the current call stack and never writes them to SQLite, browser storage or Android.
    """

    def __init__(self) -> None:
        self.settings = fresh_settings()

    def ensure_ready(self) -> None:
        if not self.settings.github_app_ready:
            raise GitHubAppError("FDEX GitHub App 尚未配置")

    def oauth_callback_url(self) -> str:
        return self.settings.public_base_url.rstrip("/") + "/account/github/app/oauth/callback"

    def setup_url(self) -> str:
        return self.settings.public_base_url.rstrip("/") + "/account/github/app/setup"

    def authorize_url(self, *, state: str, challenge: str) -> str:
        self.ensure_ready()
        query = urlencode(
            {
                "client_id": self.settings.fdex_github_app_client_id.strip(),
                "redirect_uri": self.oauth_callback_url(),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{GITHUB_AUTHORIZE_URL}?{query}"

    def install_url(self, *, state: str) -> str:
        self.ensure_ready()
        slug = self.settings.fdex_github_app_slug.strip()
        return f"https://github.com/apps/{slug}/installations/new?{urlencode({'state': state})}"

    def app_jwt(self, *, now: int | None = None) -> str:
        self.ensure_ready()
        stamp = int(now if now is not None else time.time())
        header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
        payload = _b64url(
            json.dumps(
                {
                    "iat": stamp - 60,
                    "exp": stamp + 540,
                    "iss": self.settings.fdex_github_app_id.strip(),
                },
                separators=(",", ":"),
            ).encode()
        )
        signing_input = f"{header}.{payload}".encode("ascii")
        try:
            private_key = serialization.load_pem_private_key(self._private_key_bytes(), password=None)
            signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        except Exception as exc:  # key parsing/signing details should not leak to end users
            raise GitHubAppError("GitHub App 私钥无效") from exc
        return f"{header}.{payload}.{_b64url(signature)}"

    def exchange_user_code(self, *, code: str, verifier: str) -> dict[str, Any]:
        self.ensure_ready()
        payload = self._request(
            "POST",
            GITHUB_TOKEN_URL,
            auth="none",
            form={
                "client_id": self.settings.fdex_github_app_client_id.strip(),
                "client_secret": self.settings.fdex_github_app_client_secret.strip(),
                "code": (code or "").strip(),
                "redirect_uri": self.oauth_callback_url(),
                "code_verifier": (verifier or "").strip(),
            },
        )
        if not isinstance(payload, dict):
            raise GitHubAppError("GitHub 用户授权返回格式错误")
        if payload.get("error"):
            raise GitHubAppError(str(payload.get("error_description") or payload["error"])[:500])
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise GitHubAppError("GitHub 用户授权没有返回临时访问凭据")
        return payload

    def user_profile(self, user_token: str) -> dict[str, Any]:
        result = self._request("GET", f"{GITHUB_API_URL}/user", token=user_token)
        if not isinstance(result, dict) or not str(result.get("id") or ""):
            raise GitHubAppError("GitHub 用户身份验证失败")
        return result

    def find_user_installation(self, user_token: str, installation_id: int) -> dict[str, Any]:
        """Verify that the temporary GitHub user token can access this app installation.

        GitHub explicitly warns that setup_url installation_id is attacker-controlled input.
        Therefore a setup callback is accepted only when the just-authorized user can enumerate
        the same installation through /user/installations.
        """
        expected = int(installation_id)
        for page in range(1, 11):
            result = self._request(
                "GET",
                f"{GITHUB_API_URL}/user/installations?{urlencode({'per_page': 100, 'page': page})}",
                token=user_token,
            )
            if not isinstance(result, dict):
                raise GitHubAppError("GitHub 安装列表返回格式错误")
            installations = result.get("installations") if isinstance(result.get("installations"), list) else []
            for installation in installations:
                if isinstance(installation, dict) and int(installation.get("id") or 0) == expected:
                    app_id = str(installation.get("app_id") or "")
                    configured = self.settings.fdex_github_app_id.strip()
                    if app_id and configured and app_id != configured:
                        raise GitHubAppError("该安装不属于 FDEX GitHub App")
                    return installation
            if len(installations) < 100:
                break
        raise GitHubAppError("当前 GitHub 用户无权使用该 App 安装")

    def get_installation(self, installation_id: int) -> dict[str, Any]:
        result = self._request(
            "GET",
            f"{GITHUB_API_URL}/app/installations/{int(installation_id)}",
            token=self.app_jwt(),
        )
        if not isinstance(result, dict):
            raise GitHubAppError("GitHub App 安装信息返回格式错误")
        return result

    def delete_installation(self, installation_id: int) -> None:
        """Uninstall the FDEX GitHub App so server-side repository authority is revoked."""
        try:
            self._request(
                "DELETE",
                f"{GITHUB_API_URL}/app/installations/{int(installation_id)}",
                token=self.app_jwt(),
            )
        except GitHubAppError as exc:
            # DELETE is idempotent from FDEX's perspective: if GitHub already removed it,
            # delegated repository access is already revoked.
            if "GitHub HTTP 404" in str(exc):
                return
            raise

    def installation_token(
        self,
        installation_id: int,
        *,
        repository: str = "",
        permissions: dict[str, str] | None = None,
    ) -> str:
        body: dict[str, Any] = {}
        clean_repo = (repository or "").strip()
        if clean_repo:
            _, sep, name = clean_repo.partition("/")
            if not sep or not name:
                raise GitHubAppError("GitHub 仓库名称无效")
            body["repositories"] = [name]
        if permissions:
            body["permissions"] = {str(key): str(value) for key, value in permissions.items() if key and value}
        result = self._request(
            "POST",
            f"{GITHUB_API_URL}/app/installations/{int(installation_id)}/access_tokens",
            token=self.app_jwt(),
            json_body=body,
        )
        if not isinstance(result, dict):
            raise GitHubAppError("GitHub App 临时访问凭据返回格式错误")
        token = str(result.get("token") or "").strip()
        if not token:
            raise GitHubAppError("GitHub App 没有返回临时安装凭据")
        return token

    def installation_repositories(
        self,
        installation_id: int,
        *,
        page: int = 1,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        token = self.installation_token(installation_id)
        result = self._request(
            "GET",
            f"{GITHUB_API_URL}/installation/repositories?{urlencode({'page': page, 'per_page': per_page})}",
            token=token,
        )
        if not isinstance(result, dict):
            raise GitHubAppError("GitHub App 仓库列表返回格式错误")
        repositories = result.get("repositories")
        if not isinstance(repositories, list):
            raise GitHubAppError("GitHub App 仓库列表缺失")
        return [item for item in repositories if isinstance(item, dict)]

    def _private_key_bytes(self) -> bytes:
        encoded = self.settings.fdex_github_app_private_key_b64.strip()
        if encoded:
            try:
                return base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise GitHubAppError("GitHub App 私钥 Base64 无效") from exc
        path_value = self.settings.fdex_github_app_private_key_path.strip()
        if not path_value:
            raise GitHubAppError("GitHub App 私钥未配置")
        path = Path(path_value).expanduser().resolve()
        try:
            return path.read_bytes()
        except OSError as exc:
            raise GitHubAppError("无法读取 GitHub App 私钥") from exc

    @staticmethod
    def _request(
        method: str,
        url: str,
        *,
        token: str = "",
        auth: str = "bearer",
        form: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "fdex-github-app",
        }
        if auth == "bearer" and token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                response = client.request(method, url, headers=headers, data=form, json=json_body)
        except httpx.HTTPError as exc:
            raise GitHubAppError(f"GitHub 请求失败：{type(exc).__name__}") from exc
        if response.status_code >= 400:
            detail = response.text[:500].strip()
            raise GitHubAppError(f"GitHub HTTP {response.status_code}{': ' + detail if detail else ''}")
        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise GitHubAppError("GitHub 返回了无效 JSON") from exc
