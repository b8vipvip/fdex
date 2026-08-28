from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

import httpx

from app.config import SERVER_DIR, Settings, fresh_settings
from app.env_manager import read_env

logger = logging.getLogger(__name__)

_UNIT_NAME = "fdex-github-egress.service"
_XRAY_DIR = SERVER_DIR / "data" / "github-egress"
_XRAY_CONFIG = _XRAY_DIR / "xray.json"
_SAFE_BINARY = re.compile(r"^[A-Za-z0-9_./-]{1,240}$")
_GITHUB_DOMAINS = [
    "domain:github.com",
    "domain:githubusercontent.com",
    "domain:githubassets.com",
]


class GitHubEgressError(RuntimeError):
    pass


def _env() -> dict[str, str]:
    return read_env()


def egress_mode(values: dict[str, str] | None = None, settings: Settings | None = None) -> str:
    values = values or _env()
    configured = (values.get("FDEX_GITHUB_EGRESS_MODE") or "").strip().lower()
    if configured in {"managed_vless", "http_proxy", "direct"}:
        return configured
    cfg = settings or fresh_settings()
    return "http_proxy" if cfg.fdex_github_http_proxy.strip() else "direct"


def _query_values(uri: str) -> tuple[Any, dict[str, str]]:
    parsed = urlsplit(uri)
    query = {
        str(key).lower(): str(items[-1])
        for key, items in parse_qs(parsed.query, keep_blank_values=True).items()
        if items
    }
    return parsed, query


def parse_vless_uri(uri: str) -> dict[str, Any]:
    clean = (uri or "").strip()
    if not clean:
        raise ValueError("VLESS 分享链接不能为空")
    if len(clean) > 8192:
        raise ValueError("VLESS 分享链接过长")
    parsed, query = _query_values(clean)
    if parsed.scheme.lower() != "vless":
        raise ValueError("仅支持 vless:// 分享链接")
    client_id = unquote(parsed.username or "").strip()
    try:
        uuid.UUID(client_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError("VLESS 用户 ID 不是有效 UUID") from exc
    host = (parsed.hostname or "").strip()
    try:
        port = int(parsed.port or 0)
    except ValueError as exc:
        raise ValueError("VLESS 节点端口无效") from exc
    if not host or not 1 <= port <= 65535:
        raise ValueError("VLESS 节点必须包含有效主机和端口")

    encryption = query.get("encryption", "none").strip().lower()
    if encryption not in {"", "none"}:
        raise ValueError("FDEX 托管 VLESS 仅支持 encryption=none")

    network_raw = query.get("type", "tcp").strip().lower()
    network_aliases = {
        "tcp": "tcp",
        "raw": "tcp",
        "ws": "ws",
        "grpc": "grpc",
        "httpupgrade": "httpupgrade",
        "xhttp": "xhttp",
        "splithttp": "xhttp",
    }
    network = network_aliases.get(network_raw)
    if not network:
        raise ValueError(f"暂不支持 VLESS 传输类型：{network_raw or '未知'}")

    security = query.get("security", "none").strip().lower() or "none"
    if security not in {"none", "tls", "reality"}:
        raise ValueError(f"暂不支持 VLESS 安全层：{security}")

    user: dict[str, Any] = {"id": client_id, "encryption": "none"}
    flow = query.get("flow", "").strip()
    if flow:
        user["flow"] = flow[:120]

    outbound_settings: dict[str, Any] = {
        "vnext": [
            {
                "address": host,
                "port": port,
                "users": [user],
            }
        ]
    }
    packet_encoding = query.get("packetencoding", "").strip()
    if packet_encoding:
        outbound_settings["packetEncoding"] = packet_encoding[:40]

    stream: dict[str, Any] = {"network": network, "security": security}
    server_name = (query.get("sni") or query.get("servername") or host).strip()
    fingerprint = query.get("fp", "").strip()
    alpn_raw = query.get("alpn", "").strip()
    alpn = [item.strip() for item in alpn_raw.split(",") if item.strip()]

    if security == "tls":
        tls: dict[str, Any] = {"serverName": server_name}
        if fingerprint:
            tls["fingerprint"] = fingerprint[:80]
        if alpn:
            tls["alpn"] = alpn[:8]
        allow_insecure = query.get("allowinsecure") or query.get("insecure") or ""
        if allow_insecure.strip().lower() in {"1", "true", "yes", "on"}:
            tls["allowInsecure"] = True
        stream["tlsSettings"] = tls
    elif security == "reality":
        public_key = (query.get("pbk") or query.get("publickey") or "").strip()
        if not public_key or not server_name:
            raise ValueError("Reality VLESS 必须包含 SNI 和 public key（pbk）")
        reality: dict[str, Any] = {
            "serverName": server_name,
            "publicKey": public_key,
            "fingerprint": fingerprint or "chrome",
        }
        short_id = (query.get("sid") or query.get("shortid") or "").strip()
        spider_x = (query.get("spx") or query.get("spiderx") or "").strip()
        if short_id:
            reality["shortId"] = short_id
        if spider_x:
            reality["spiderX"] = spider_x
        stream["realitySettings"] = reality

    path = query.get("path", "").strip() or "/"
    host_header = query.get("host", "").strip()
    if network == "ws":
        ws: dict[str, Any] = {"path": path}
        if host_header:
            ws["headers"] = {"Host": host_header}
        early_data = query.get("ed", "").strip()
        if early_data.isdigit():
            ws["maxEarlyData"] = min(int(early_data), 4096)
        early_header = (query.get("eh") or query.get("earlydataheadername") or "").strip()
        if early_header:
            ws["earlyDataHeaderName"] = early_header[:120]
        stream["wsSettings"] = ws
    elif network == "grpc":
        grpc: dict[str, Any] = {"serviceName": (query.get("servicename") or "").strip()}
        if query.get("mode", "").strip().lower() == "multi":
            grpc["multiMode"] = True
        authority = query.get("authority", "").strip()
        if authority:
            grpc["authority"] = authority
        stream["grpcSettings"] = grpc
    elif network == "httpupgrade":
        upgrade: dict[str, Any] = {"path": path}
        if host_header:
            upgrade["host"] = host_header
        stream["httpupgradeSettings"] = upgrade
    elif network == "xhttp":
        xhttp: dict[str, Any] = {"path": path}
        if host_header:
            xhttp["host"] = host_header
        mode = query.get("mode", "").strip()
        if mode:
            xhttp["mode"] = mode
        extra = query.get("extra", "").strip()
        if extra:
            try:
                parsed_extra = json.loads(extra)
            except json.JSONDecodeError as exc:
                raise ValueError("VLESS xhttp extra 不是有效 JSON") from exc
            if isinstance(parsed_extra, dict):
                xhttp["extra"] = parsed_extra
        stream["xhttpSettings"] = xhttp
    elif network == "tcp":
        header_type = query.get("headertype", "").strip().lower()
        if header_type not in {"", "none"}:
            raise ValueError("托管 VLESS 的 TCP 模式暂不支持自定义 headerType")

    return {
        "tag": "fdex-vless",
        "protocol": "vless",
        "settings": outbound_settings,
        "streamSettings": stream,
    }


def vless_summary(uri: str) -> str:
    clean = (uri or "").strip()
    if not clean:
        return "未配置"
    try:
        parsed, query = _query_values(clean)
        host = parsed.hostname or "未知主机"
        port = parsed.port or 0
        network = (query.get("type") or "tcp").upper()
        security = (query.get("security") or "none").upper()
        return f"{host}:{port} · {network}/{security}"
    except (ValueError, TypeError):
        return "已配置，但格式无效"


def managed_proxy_url(port: int, username: str, password: str) -> str:
    if not 1 <= int(port) <= 65535:
        raise ValueError("本地 Xray 端口无效")
    user = quote((username or "").strip(), safe="")
    secret = quote((password or "").strip(), safe="")
    if not user or not secret:
        raise ValueError("FDEX 专用代理认证信息缺失")
    return f"http://{user}:{secret}@127.0.0.1:{int(port)}"


def build_xray_config(uri: str, port: int, username: str, password: str) -> dict[str, Any]:
    outbound = parse_vless_uri(uri)
    return {
        "log": {
            "access": "none",
            "error": "none",
            "loglevel": "warning",
        },
        "inbounds": [
            {
                "tag": "fdex-github-http",
                "listen": "127.0.0.1",
                "port": int(port),
                "protocol": "http",
                "settings": {
                    "accounts": [{"user": username, "pass": password}],
                    "allowTransparent": False,
                },
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls"],
                },
            }
        ],
        "outbounds": [
            outbound,
            {"tag": "blocked", "protocol": "blackhole", "settings": {}},
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["fdex-github-http"],
                    "domain": list(_GITHUB_DOMAINS),
                    "outboundTag": "fdex-vless",
                },
                {
                    "type": "field",
                    "inboundTag": ["fdex-github-http"],
                    "outboundTag": "blocked",
                },
            ],
        },
    }


def _run(args: list[str], timeout: float = 8.0) -> tuple[int, str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return result.returncode, ((result.stdout or "") + (result.stderr or "")).strip()


def resolve_xray_binary(value: str) -> str:
    clean = (value or "xray").strip()
    if not _SAFE_BINARY.fullmatch(clean):
        raise ValueError("Xray 可执行文件路径格式无效")
    if "/" in clean:
        path = Path(clean).expanduser().resolve()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError(f"找不到可执行的 Xray：{path}")
        return str(path)
    found = shutil.which(clean)
    if not found:
        raise ValueError(f"找不到 Xray 可执行文件：{clean}")
    return str(Path(found).resolve())


def _systemd_ready() -> bool:
    return bool(shutil.which("systemctl") and shutil.which("systemd-run"))


def _unit_state() -> str:
    if not shutil.which("systemctl"):
        return "systemd unavailable"
    code, output = _run(["systemctl", "is-active", _UNIT_NAME], timeout=3)
    return "active" if code == 0 else (output.strip() or "inactive")


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.35):
            return True
    except OSError:
        return False


def _write_xray_config(config: dict[str, Any]) -> bool:
    _XRAY_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(_XRAY_DIR, 0o700)
    except OSError:
        pass
    content = json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    previous = ""
    try:
        previous = _XRAY_CONFIG.read_text(encoding="utf-8")
    except OSError:
        pass
    if previous == content:
        try:
            os.chmod(_XRAY_CONFIG, 0o600)
        except OSError:
            pass
        return False
    fd, temp_name = tempfile.mkstemp(prefix=".xray.", dir=str(_XRAY_DIR), text=True)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, _XRAY_CONFIG)
    finally:
        temp_path.unlink(missing_ok=True)
    return True


def _stop_unit() -> None:
    if not shutil.which("systemctl"):
        return
    _run(["systemctl", "stop", _UNIT_NAME], timeout=10)
    for _ in range(20):
        if not _port_open(int((_env().get("FDEX_GITHUB_XRAY_LOCAL_PORT") or "18188"))):
            break
        time.sleep(0.1)


def stop_managed_egress() -> None:
    _stop_unit()


def _redact(text: str, values: dict[str, str]) -> str:
    clean = text or ""
    for key in (
        "FDEX_GITHUB_VLESS_URI",
        "FDEX_GITHUB_XRAY_PROXY_USER",
        "FDEX_GITHUB_XRAY_PROXY_PASSWORD",
        "FDEX_GITHUB_HTTP_PROXY",
    ):
        secret = (values.get(key) or "").strip()
        if secret:
            clean = clean.replace(secret, "***")
    return clean[:2000]


def apply_managed_egress(*, force_restart: bool = False) -> dict[str, Any]:
    values = _env()
    if egress_mode(values) != "managed_vless":
        raise GitHubEgressError("当前 GitHub 出站模式不是托管 VLESS")
    uri = (values.get("FDEX_GITHUB_VLESS_URI") or "").strip()
    binary_setting = (values.get("FDEX_GITHUB_XRAY_BINARY") or "xray").strip()
    try:
        port = int(values.get("FDEX_GITHUB_XRAY_LOCAL_PORT") or "18188")
    except ValueError as exc:
        raise GitHubEgressError("本地 Xray 端口无效") from exc
    username = (values.get("FDEX_GITHUB_XRAY_PROXY_USER") or "").strip()
    password = (values.get("FDEX_GITHUB_XRAY_PROXY_PASSWORD") or "").strip()
    expected_proxy = managed_proxy_url(port, username, password)
    if fresh_settings().fdex_github_http_proxy.strip() != expected_proxy:
        raise GitHubEgressError("FDEX 专用 HTTP 代理地址与托管 VLESS 配置不一致")
    if not _systemd_ready():
        raise GitHubEgressError("当前服务器缺少 systemd-run/systemctl，无法启动 FDEX 专用 Xray")
    try:
        binary = resolve_xray_binary(binary_setting)
        config = build_xray_config(uri, port, username, password)
    except ValueError as exc:
        raise GitHubEgressError(str(exc)) from exc

    changed = _write_xray_config(config)
    if _unit_state() == "active" and not changed and not force_restart and _port_open(port):
        return managed_egress_status()

    _stop_unit()
    if _port_open(port):
        raise GitHubEgressError(f"127.0.0.1:{port} 已被其它进程占用，拒绝覆盖")

    args = [
        "systemd-run",
        "--unit=fdex-github-egress",
        "--collect",
        "--quiet",
        "--property=Restart=on-failure",
        "--property=RestartSec=3s",
        "--property=NoNewPrivileges=yes",
        "--property=PrivateTmp=yes",
        "--property=PrivateDevices=yes",
        "--property=ProtectKernelTunables=yes",
        "--property=ProtectKernelModules=yes",
        "--property=ProtectControlGroups=yes",
        "--property=RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "--property=MemoryMax=192M",
        binary,
        "run",
        "-config",
        str(_XRAY_CONFIG),
    ]
    code, output = _run(args, timeout=12)
    if code != 0:
        if _unit_state() == "active" and _port_open(port):
            return managed_egress_status()
        raise GitHubEgressError(_redact(output or "无法启动 FDEX 专用 Xray", values))

    for _ in range(40):
        if _unit_state() == "active" and _port_open(port):
            return managed_egress_status()
        time.sleep(0.1)
    state = _unit_state()
    code, logs = _run(["journalctl", "-u", _UNIT_NAME, "-n", "12", "--no-pager"], timeout=5)
    detail = _redact(logs, values) if code == 0 else state
    raise GitHubEgressError(f"FDEX 专用 Xray 未能监听 127.0.0.1:{port}：{detail}")


def ensure_managed_egress_on_startup() -> None:
    values = _env()
    if egress_mode(values) != "managed_vless":
        return
    try:
        apply_managed_egress(force_restart=False)
    except Exception as exc:  # service startup must remain available for admin repair
        logger.warning("FDEX managed GitHub egress is unavailable: %s", _redact(str(exc), values))


def managed_egress_status() -> dict[str, Any]:
    values = _env()
    cfg = fresh_settings()
    mode = egress_mode(values, cfg)
    try:
        port = int(values.get("FDEX_GITHUB_XRAY_LOCAL_PORT") or "18188")
    except ValueError:
        port = 18188
    uri = (values.get("FDEX_GITHUB_VLESS_URI") or "").strip()
    binary_setting = (values.get("FDEX_GITHUB_XRAY_BINARY") or "xray").strip()
    resolved = ""
    version = ""
    try:
        resolved = resolve_xray_binary(binary_setting)
        code, output = _run([resolved, "version"], timeout=4)
        if code == 0 and output:
            version = output.splitlines()[0][:160]
    except ValueError:
        pass
    username = (values.get("FDEX_GITHUB_XRAY_PROXY_USER") or "").strip()
    password = (values.get("FDEX_GITHUB_XRAY_PROXY_PASSWORD") or "").strip()
    managed_proxy = ""
    try:
        if username and password:
            managed_proxy = managed_proxy_url(port, username, password)
    except ValueError:
        pass
    actual_proxy = cfg.fdex_github_http_proxy.strip()
    return {
        "mode": mode,
        "proxy_configured": bool(actual_proxy),
        "managed_proxy_matches": bool(managed_proxy and actual_proxy == managed_proxy),
        "vless_configured": bool(uri),
        "vless_summary": vless_summary(uri),
        "xray_binary_setting": binary_setting,
        "xray_binary": resolved,
        "xray_version": version,
        "systemd_ready": _systemd_ready(),
        "unit_state": _unit_state(),
        "local_port": port,
        "listener_ready": _port_open(port) if mode == "managed_vless" else False,
        "config_path": str(_XRAY_CONFIG),
        "isolation": "127.0.0.1 + 随机认证 + GitHub 域名白名单",
    }


def probe_managed_proxy_auth() -> dict[str, Any]:
    status = managed_egress_status()
    if status["mode"] != "managed_vless":
        return {"ok": True, "applicable": False, "detail": "非托管 VLESS 模式"}
    port = int(status["local_port"])
    if not status["listener_ready"]:
        return {"ok": False, "applicable": True, "detail": "本地 Xray 未监听"}
    started = time.perf_counter()
    try:
        with httpx.Client(
            proxy=f"http://127.0.0.1:{port}",
            timeout=httpx.Timeout(3.0),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = client.get("http://example.com/")
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        ok = response.status_code == 407
        return {
            "ok": ok,
            "applicable": True,
            "status_code": response.status_code,
            "elapsed_ms": elapsed_ms,
            "detail": "未认证访问被拒绝" if ok else f"未认证访问返回 HTTP {response.status_code}",
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "applicable": True,
            "status_code": 0,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "detail": type(exc).__name__,
        }


def make_managed_credentials(values: dict[str, str] | None = None) -> tuple[str, str]:
    values = values or _env()
    username = (values.get("FDEX_GITHUB_XRAY_PROXY_USER") or "").strip()
    password = (values.get("FDEX_GITHUB_XRAY_PROXY_PASSWORD") or "").strip()
    if not username:
        username = f"fdex-{secrets.token_hex(4)}"
    if not password:
        password = secrets.token_urlsafe(32)
    return username, password
