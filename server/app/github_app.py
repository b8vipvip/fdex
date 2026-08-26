from __future__ import annotations

import base64
import binascii
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

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

    GitHub's browser authorization page and the FDEX center are two different network paths.
    The browser may reach GitHub successfully while the center cannot reach github.com/api.github.com.
    The transport below therefore has independent connect/read timeouts, bounded retries for
    retry-safe operations, an optional operator-controlled HTTP(S) proxy and stage-specific
    errors instead of the previous opaque ``ReadTimeout`` message.
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
        # An OAuth authorization code is single-use. A read timeout after the request was sent
        # is ambiguous, so this POST is deliberately not replayed. We instead give it a generous
        # configurable read timeout and return an actionable transport error if it still fails.
        payload = self._request(
            "POST",
            GITHUB_TOKEN_URL,
            auth="none",
            accept="application/json",
            form={
                "client_id": self.settings.fdex_github_app_client_id.strip(),
                "client_secret": self.settings.fdex_github_app_client_secret.strip(),
                "code": (code or "").strip(),
                "redirect_uri": self.oauth_callback_url(),
                "code_verifier": (verifier or "").strip(),
            },
            retry_safe=False,
            operation="GitHub OAuth 临时凭据交换",
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
        result = self._request(
            "GET",
            f"{GITHUB_API_URL}/user",
            token=user_token,
            retry_safe=True,
            operation="GitHub 用户身份读取",
        )
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
                retry_safe=True,
                operation="GitHub 用户安装权限验证",
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
            retry_safe=True,
            operation="GitHub App 安装信息读取",
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
                retry_safe=True,
                operation="GitHub App 卸载",
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
            # Creating another one-hour installation token after a transport failure does not
            # broaden authority and is safe to retry; no token is persisted by FDEX.
            retry_safe=True,
            operation="GitHub App 临时 installation token 生成",
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
            retry_safe=True,
            operation="GitHub App 仓库列表读取",
        )
        if not isinstance(result, dict):
            raise GitHubAppError("GitHub App 仓库列表返回格式错误")
        repositories = result.get("repositories")
        if not isinstance(repositories, list):
            raise GitHubAppError("GitHub App 仓库列表缺失")
        return [item for item in repositories if isinstance(item, dict)]

    def network_probe(self) -> dict[str, Any]:
        """Test the center server's actual outbound path without using user credentials."""
        targets = (
            ("github.com", "https://github.com/"),
            ("api.github.com", f"{GITHUB_API_URL}/meta"),
        )
        result: dict[str, Any] = {
            "proxy_configured": bool(self.settings.fdex_github_http_proxy.strip()),
            "connect_timeout_seconds": self.settings.fdex_github_connect_timeout_seconds,
            "read_timeout_seconds": self.settings.fdex_github_read_timeout_seconds,
            "targets": [],
        }
        for label, url in targets:
            started = time.perf_counter()
            try:
                with self._client(follow_redirects=True) as client:
                    response = client.get(
                        url,
                        headers={"Accept": "application/vnd.github+json", "User-Agent": "fdex-github-network-test"},
                    )
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                result["targets"].append(
                    {
                        "name": label,
                        "ok": response.status_code < 500,
                        "status_code": response.status_code,
                        "elapsed_ms": elapsed_ms,
                        "error": "",
                    }
                )
            except httpx.HTTPError as exc:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                result["targets"].append(
                    {
                        "name": label,
                        "ok": False,
                        "status_code": 0,
                        "elapsed_ms": elapsed_ms,
                        "error": type(exc).__name__,
                    }
                )
        return result

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

    def _client(self, *, follow_redirects: bool = False) -> httpx.Client:
        timeout = httpx.Timeout(
            connect=float(self.settings.fdex_github_connect_timeout_seconds),
            read=float(self.settings.fdex_github_read_timeout_seconds),
            write=float(self.settings.fdex_github_read_timeout_seconds),
            pool=float(self.settings.fdex_github_connect_timeout_seconds),
        )
        proxy = self.settings.fdex_github_http_proxy.strip()
        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "follow_redirects": follow_redirects,
            # Preserve normal HTTP(S)_PROXY environment support when no explicit FDEX proxy is set.
            "trust_env": True,
        }
        if proxy:
            scheme = (urlsplit(proxy).scheme or "").lower()
            if scheme not in {"http", "https"}:
                raise GitHubAppError("GitHub 出站代理仅支持 http:// 或 https://")
            kwargs["proxy"] = proxy
        return httpx.Client(**kwargs)

    def _request(
        self,
        method: str,
        url: str,
        *,
        token: str = "",
        auth: str = "bearer",
        accept: str = "application/vnd.github+json",
        form: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        retry_safe: bool = False,
        operation: str = "GitHub 请求",
    ) -> Any:
        headers = {
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "fdex-github-app",
        }
        if auth == "bearer" and token:
            headers["Authorization"] = f"Bearer {token}"

        attempts = int(self.settings.fdex_github_retry_attempts) if retry_safe else 1
        last_exc: httpx.HTTPError | None = None
        for attempt in range(1, attempts + 1):
            try:
                with self._client(follow_redirects=False) as client:
                    response = client.request(method, url, headers=headers, data=form, json=json_body)
                break
            except httpx.ConnectTimeout as exc:
                last_exc = exc
                # A connection timeout occurs before a usable connection is established; retry is
                # allowed even for OAuth code exchange because the request was not delivered.
                if attempt < max(attempts, 2) and not retry_safe:
                    attempts = min(2, int(self.settings.fdex_github_retry_attempts))
                    time.sleep(0.35 * attempt)
                    continue
                if attempt < attempts:
                    time.sleep(0.35 * attempt)
                    continue
                raise self._transport_error(operation, exc) from exc
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
                last_exc = exc
                # Read/write timeouts on a single-use OAuth code are not replayed. GETs and other
                # explicitly retry-safe GitHub operations get bounded backoff retries.
                if retry_safe and attempt < attempts:
                    time.sleep(0.35 * attempt)
                    continue
                raise self._transport_error(operation, exc) from exc
            except httpx.HTTPError as exc:
                last_exc = exc
                if retry_safe and attempt < attempts:
                    time.sleep(0.35 * attempt)
                    continue
                raise self._transport_error(operation, exc) from exc
        else:  # pragma: no cover - loop always returns or raises
            assert last_exc is not None
            raise self._transport_error(operation, last_exc) from last_exc

        if response.status_code >= 400:
            detail = response.text[:500].strip()
            raise GitHubAppError(f"GitHub HTTP {response.status_code}{': ' + detail if detail else ''}")
        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise GitHubAppError("GitHub 返回了无效 JSON") from exc

    def _transport_error(self, operation: str, exc: httpx.HTTPError) -> GitHubAppError:
        kind = type(exc).__name__
        proxy_hint = "，当前已启用 FDEX GitHub 出站代理" if self.settings.fdex_github_http_proxy.strip() else "，当前使用服务器直连"
        if isinstance(exc, httpx.ReadTimeout):
            detail = f"读取超时 {self.settings.fdex_github_read_timeout_seconds:g} 秒"
        elif isinstance(exc, httpx.ConnectTimeout):
            detail = f"连接超时 {self.settings.fdex_github_connect_timeout_seconds:g} 秒"
        else:
            detail = kind
        return GitHubAppError(
            f"{operation}失败：{detail}{proxy_hint}。请在服务端管理后台 → GitHub App → GitHub 网络出口运行连通性测试；"
            "如服务器到 GitHub 国际网络不稳定，请配置可信 HTTP(S) 出站代理后重试。"
        )
