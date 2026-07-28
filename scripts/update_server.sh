#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/fdex}"
REPO_URL="${REPO_URL:-https://github.com/b8vipvip/fdex.git}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="fdex"
ENV_BACKUP=""

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
  echo "已创建 ${APP_DIR}/server/.env，请填写 AI_API_KEY 等配置。"
fi

python3 -m venv "${APP_DIR}/server/.venv"
"${APP_DIR}/server/.venv/bin/pip" install --upgrade pip
"${APP_DIR}/server/.venv/bin/pip" install -r "${APP_DIR}/server/requirements.txt"

install -m 0644 "${APP_DIR}/deploy/systemd/fdex.service" "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
systemctl restart "${SERVICE_NAME}.service"

for _ in {1..20}; do
  if curl --silent --fail "http://127.0.0.1:8000/api/health" >/dev/null; then
    echo "FDEX 服务端更新成功。"
    curl --silent "http://127.0.0.1:8000/api/health"
    echo
    exit 0
  fi
  sleep 1
done

systemctl status "${SERVICE_NAME}.service" --no-pager || true
journalctl -u "${SERVICE_NAME}.service" -n 80 --no-pager || true
echo "服务启动失败，请查看上方日志。" >&2
exit 1
