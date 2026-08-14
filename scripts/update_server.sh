#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/fdex}"
REPO_URL="${REPO_URL:-https://github.com/b8vipvip/fdex.git}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="fdex"
ENV_BACKUP=""
GENERATED_ADMIN_PASSWORD=""

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 root 执行：sudo bash scripts/update_server.sh" >&2
  exit 1
fi

if [[ -f "${APP_DIR}/server/.env" ]]; then
  ENV_BACKUP="$(mktemp)"
  cp "${APP_DIR}/server/.env" "${ENV_BACKUP}"
fi

if [[ ! -d "${APP_DIR}/.git" ]]; then
  mkdir -p "$(dirname "${APP_DIR}")"
  git clone --branch "${BRANCH}" "${REPO_URL}" "${APP_DIR}"
else
  git -C "${APP_DIR}" fetch origin "${BRANCH}"
  git -C "${APP_DIR}" checkout "${BRANCH}"
  git -C "${APP_DIR}" reset --hard "origin/${BRANCH}"
fi

if [[ -n "${ENV_BACKUP}" ]]; then
  cp "${ENV_BACKUP}" "${APP_DIR}/server/.env"
  rm -f "${ENV_BACKUP}"
elif [[ ! -f "${APP_DIR}/server/.env" ]]; then
  cp "${APP_DIR}/server/.env.example" "${APP_DIR}/server/.env"
  echo "已创建 ${APP_DIR}/server/.env。"
fi

# Initialize secure dashboard credentials without overwriting existing values.
GENERATED_ADMIN_PASSWORD="$(python3 - "${APP_DIR}/server/.env" <<'PY'
from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
values: dict[str, str] = {}
for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        continue
    key, raw = stripped.split("=", 1)
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        try:
            value = json.loads(value) if value[0] == '"' else value[1:-1]
        except json.JSONDecodeError:
            value = value[1:-1]
    values[key.strip()] = value

updates: dict[str, str] = {}
if not values.get("ADMIN_USERNAME", "").strip():
    updates["ADMIN_USERNAME"] = "admin"
if len(values.get("ADMIN_PASSWORD", "")) < 12:
    updates["ADMIN_PASSWORD"] = secrets.token_urlsafe(18)
if len(values.get("ADMIN_SESSION_SECRET", "")) < 32:
    updates["ADMIN_SESSION_SECRET"] = secrets.token_urlsafe(48)
if not values.get("ADMIN_COOKIE_SECURE", "").strip():
    updates["ADMIN_COOKIE_SECURE"] = "true"
if not values.get("ADMIN_SESSION_HOURS", "").strip():
    updates["ADMIN_SESSION_HOURS"] = "12"
if not values.get("ADMIN_LOG_LINES", "").strip():
    updates["ADMIN_LOG_LINES"] = "300"
if not values.get("RELEASE_CACHE_DIR", "").strip():
    updates["RELEASE_CACHE_DIR"] = "/opt/fdex/server/data/releases"

remaining = dict(updates)
output: list[str] = []
for line in lines:
    stripped = line.strip()
    if stripped and not stripped.startswith("#") and "=" in stripped:
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            output.append(f"{key}={remaining.pop(key)}")
            continue
    output.append(line)
if remaining:
    if output and output[-1].strip():
        output.append("")
    output.append("# Generated securely by scripts/update_server.sh")
    output.extend(f"{key}={value}" for key, value in remaining.items())

content = "\n".join(output).rstrip() + "\n"
fd, name = tempfile.mkstemp(prefix=".env.", dir=str(path.parent), text=True)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(name, 0o600)
    os.replace(name, path)
finally:
    if os.path.exists(name):
        os.unlink(name)

print(updates.get("ADMIN_PASSWORD", ""))
PY
)"
chmod 600 "${APP_DIR}/server/.env"
mkdir -p "${APP_DIR}/server/data/backups"
chmod 700 "${APP_DIR}/server/data" "${APP_DIR}/server/data/backups"

read_env_value() {
  local key="$1"
  local value
  value="$(grep -E "^${key}=" "${APP_DIR}/server/.env" 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '\r' || true)"
  value="${value%\"}"
  value="${value#\"}"
  value="${value%\'}"
  value="${value#\'}"
  printf '%s' "${value}"
}

FDEX_PORT="$(read_env_value FDEX_PORT)"
FDEX_PORT="${FDEX_PORT:-18080}"
ADMIN_USERNAME="$(read_env_value ADMIN_USERNAME)"
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
PUBLIC_BASE_URL="$(read_env_value PUBLIC_BASE_URL)"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-https://fdex.k2n.cn}"
RELEASE_CACHE_DIR="$(read_env_value RELEASE_CACHE_DIR)"
RELEASE_CACHE_DIR="${RELEASE_CACHE_DIR:-${APP_DIR}/server/data/releases}"

if ! [[ "${FDEX_PORT}" =~ ^[0-9]+$ ]] || (( FDEX_PORT < 1 || FDEX_PORT > 65535 )); then
  echo "server/.env 中 FDEX_PORT=${FDEX_PORT} 无效，必须是 1-65535。" >&2
  exit 1
fi

mkdir -p "${RELEASE_CACHE_DIR}"
chmod 755 "${RELEASE_CACHE_DIR}"

# Stop only our own service. Never terminate an unrelated process occupying the selected port.
systemctl stop "${SERVICE_NAME}.service" 2>/dev/null || true

if command -v ss >/dev/null 2>&1 && ss -H -ltn "sport = :${FDEX_PORT}" | grep -q .; then
  echo "端口 ${FDEX_PORT} 已被其他服务占用，FDEX 未启动，也没有结束占用进程。" >&2
  ss -ltnp "sport = :${FDEX_PORT}" || true
  echo "请在 ${APP_DIR}/server/.env 中设置一个空闲的 FDEX_PORT，并同步修改宝塔反向代理。" >&2
  exit 1
fi

python3 -m venv "${APP_DIR}/server/.venv"
"${APP_DIR}/server/.venv/bin/pip" install --upgrade pip
"${APP_DIR}/server/.venv/bin/pip" install -r "${APP_DIR}/server/requirements.txt"

install -m 0644 "${APP_DIR}/deploy/systemd/fdex.service" "/etc/systemd/system/${SERVICE_NAME}.service"
install -m 0644 "${APP_DIR}/deploy/systemd/fdex-release-sync.service" "/etc/systemd/system/fdex-release-sync.service"
install -m 0644 "${APP_DIR}/deploy/systemd/fdex-release-sync.timer" "/etc/systemd/system/fdex-release-sync.timer"
install -m 0644 "${APP_DIR}/deploy/systemd/fdex-provider-probe.service" "/etc/systemd/system/fdex-provider-probe.service"
install -m 0644 "${APP_DIR}/deploy/systemd/fdex-provider-probe.timer" "/etc/systemd/system/fdex-provider-probe.timer"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
systemctl enable --now fdex-release-sync.timer
systemctl enable --now fdex-provider-probe.timer
systemctl restart "${SERVICE_NAME}.service"

# Try release sync once immediately. Failure is non-fatal because the timer keeps retrying every minute.
systemctl start fdex-release-sync.service 2>/dev/null || true

for _ in {1..30}; do
  if curl --silent --fail "http://127.0.0.1:${FDEX_PORT}/api/health" >/dev/null; then
    echo "FDEX 服务端更新成功，监听 127.0.0.1:${FDEX_PORT}。"
    curl --silent "http://127.0.0.1:${FDEX_PORT}/api/health"
    echo
    echo "管理后台：${PUBLIC_BASE_URL}/admin"
    echo "AI 供应商管理：${PUBLIC_BASE_URL}/admin/providers"
    echo "AI 供应商自动深测：每 15 分钟检查一次到期线路"
    echo "APK 更新接口：${PUBLIC_BASE_URL}/api/client/update"
    echo "APK 缓存目录：${RELEASE_CACHE_DIR}"
    echo "GitHub Release 轮询：每 60 秒自动检查一次"
    echo "管理员用户名：${ADMIN_USERNAME}"
    if [[ -n "${GENERATED_ADMIN_PASSWORD}" ]]; then
      echo "首次生成的管理员密码：${GENERATED_ADMIN_PASSWORD}"
      echo "请立即登录后台并修改密码；该密码只在本次终端输出。"
    fi
    exit 0
  fi
  sleep 1
done

systemctl status "${SERVICE_NAME}.service" --no-pager || true
journalctl -u "${SERVICE_NAME}.service" -n 120 --no-pager || true
echo "服务启动失败，请查看上方日志。" >&2
exit 1
