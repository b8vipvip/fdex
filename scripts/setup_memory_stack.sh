#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/fdex}"
ENV_FILE="${APP_DIR}/server/.env"
COMPOSE_FILE="${APP_DIR}/docker-compose.memory.yml"

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 root 执行：sudo bash scripts/setup_memory_stack.sh" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "未安装 Docker，无法启动 MemPalace(Qdrant) + Letta 记忆栈。" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 不可用。" >&2
  exit 1
fi
if [[ ! -f "${ENV_FILE}" || ! -f "${COMPOSE_FILE}" ]]; then
  echo "缺少 ${ENV_FILE} 或 ${COMPOSE_FILE}。" >&2
  exit 1
fi

read_env() {
  local key="$1"
  local value
  value="$(grep -E "^${key}=" "${ENV_FILE}" 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
  value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
  printf '%s' "${value}"
}

PROXY_PORT="$(read_env FDEX_MEMORY_PROXY_PORT)"; PROXY_PORT="${PROXY_PORT:-18100}"
QDRANT_PORT="$(read_env FDEX_MEMORY_QDRANT_PORT)"; QDRANT_PORT="${QDRANT_PORT:-6333}"
LETTA_PORT="$(read_env FDEX_LETTA_PORT)"; LETTA_PORT="${LETTA_PORT:-8283}"
SETUP_TIMEOUT="$(read_env FDEX_MEMORY_SETUP_TIMEOUT_SECONDS)"; SETUP_TIMEOUT="${SETUP_TIMEOUT:-900}"

for pair in "proxy:${PROXY_PORT}" "qdrant:${QDRANT_PORT}" "letta:${LETTA_PORT}"; do
  name="${pair%%:*}"; port="${pair##*:}"
  if ! [[ "${port}" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
    echo "${name} 端口 ${port} 无效。" >&2; exit 1
  fi
done
if ! [[ "${SETUP_TIMEOUT}" =~ ^[0-9]+$ ]] || (( SETUP_TIMEOUT < 60 || SETUP_TIMEOUT > 3600 )); then
  echo "FDEX_MEMORY_SETUP_TIMEOUT_SECONDS=${SETUP_TIMEOUT} 无效，必须是 60-3600 秒。" >&2
  exit 1
fi

mkdir -p "${APP_DIR}/server/data/memory"
chmod 700 "${APP_DIR}/server/data" "${APP_DIR}/server/data/memory" 2>/dev/null || true

echo "正在构建/启动长期记忆栈；memory-provider-proxy 使用轻量专用依赖，不再安装 Codex/完整 FDEX 依赖。"
echo "Docker Compose 启动阶段最多等待 ${SETUP_TIMEOUT} 秒；超时后由上层部署策略决定 fail-open 或停止更新。"
COMPOSE_ARGS=(docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d --build)
if command -v timeout >/dev/null 2>&1; then
  if timeout --signal=TERM --kill-after=30s "${SETUP_TIMEOUT}s" "${COMPOSE_ARGS[@]}"; then
    :
  else
    rc=$?
    if (( rc == 124 || rc == 137 )); then
      echo "长期记忆栈 Docker 构建/启动超过 ${SETUP_TIMEOUT} 秒，已终止本阶段。" >&2
    else
      echo "长期记忆栈 Docker 构建/启动失败（退出码 ${rc}）。" >&2
    fi
    exit "${rc}"
  fi
else
  echo "警告：系统缺少 timeout 命令，Docker Compose 启动阶段无法施加总超时。" >&2
  "${COMPOSE_ARGS[@]}"
fi

wait_url() {
  local name="$1" url="$2" attempts="${3:-60}"
  for ((i=1;i<=attempts;i++)); do
    if curl --silent --fail "${url}" >/dev/null 2>&1; then
      echo "${name} 已就绪。"
      return 0
    fi
    sleep 2
  done
  echo "${name} 健康检查超时：${url}" >&2
  return 1
}

wait_url "FDEX Memory Provider Proxy" "http://127.0.0.1:${PROXY_PORT}/health" 45
wait_url "Qdrant" "http://127.0.0.1:${QDRANT_PORT}/readyz" 45
# Letta health requires auth; rely on Docker health status rather than exposing the password to curl history.
for _ in {1..60}; do
  cid="$(docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps -q letta)"
  status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${cid}" 2>/dev/null || true)"
  if [[ "${status}" == "healthy" ]]; then
    echo "Letta 已就绪。"
    exit 0
  fi
  sleep 2
done

docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps
echo "Letta 健康检查超时。" >&2
exit 1
