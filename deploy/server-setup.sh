#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "请使用 sudo 执行: sudo bash deploy/server-setup.sh" >&2
    exit 1
fi

DEPLOY_USER="${DEPLOY_USER:-user}"
APP_ROOT="${APP_ROOT:-/opt/campus-relay}"
SERVICE_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/campusrelay-api.service"
SUDOERS_FILE="/etc/sudoers.d/campusrelay-deploy"

id "${DEPLOY_USER}" >/dev/null 2>&1 || {
    echo "系统用户不存在: ${DEPLOY_USER}" >&2
    exit 1
}
[[ -f "${SERVICE_FILE}" ]] || {
    echo "找不到服务文件: ${SERVICE_FILE}" >&2
    exit 1
}
command -v visudo >/dev/null
command -v systemctl >/dev/null

install -d -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" -m 755 \
    "${APP_ROOT}" "${APP_ROOT}/incoming" "${APP_ROOT}/releases"

install -o root -g root -m 644 "${SERVICE_FILE}" \
    /etc/systemd/system/campusrelay-api.service

SYSTEMCTL="$(command -v systemctl)"
cat > "${SUDOERS_FILE}" <<EOF
${DEPLOY_USER} ALL=(root) NOPASSWD: ${SYSTEMCTL} restart campusrelay-api.service, ${SYSTEMCTL} stop campusrelay-api.service
EOF
chmod 440 "${SUDOERS_FILE}"
visudo -cf "${SUDOERS_FILE}"
systemctl daemon-reload

echo "CampusRelay 服务器目录和 systemd unit 已准备完成。"
echo "下一步请创建 ${APP_ROOT}/server.env 对应的 /etc/campus-relay/server.env，再部署应用。"
