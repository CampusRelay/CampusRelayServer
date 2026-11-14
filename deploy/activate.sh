#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="${APP_ROOT:-/opt/campus-relay}"
INCOMING_DIR="${INCOMING_DIR:-${APP_ROOT}/incoming}"
RELEASES_DIR="${RELEASES_DIR:-${APP_ROOT}/releases}"
CURRENT_LINK="${CURRENT_LINK:-${APP_ROOT}/current}"
ENV_FILE="${ENV_FILE:-/etc/campus-relay/server.env}"
SERVICE_NAME="${SERVICE_NAME:-campusrelay-api.service}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8101/health}"
DATABASE_HEALTH_URL="${DATABASE_HEALTH_URL:-http://127.0.0.1:8101/health/database}"
HEALTH_ATTEMPTS="${HEALTH_ATTEMPTS:-15}"
HEALTH_DELAY="${HEALTH_DELAY:-2}"

DEPLOY_SHA="${1:-}"
if [[ ! "${DEPLOY_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
    echo "用法: $0 <40位Git提交SHA>" >&2
    exit 2
fi

if [[ ! -d "${INCOMING_DIR}" ]]; then
    echo "上传目录不存在: ${INCOMING_DIR}" >&2
    exit 1
fi
for required in app/main.py requirements.txt alembic.ini migrations/env.py deploy/campusrelay-api.service; do
    if [[ ! -e "${INCOMING_DIR}/${required}" ]]; then
        echo "上传内容缺少必要文件: ${required}" >&2
        exit 1
    fi
done

if [[ ! -r "${ENV_FILE}" ]]; then
    echo "生产环境变量文件不存在或当前用户不可读: ${ENV_FILE}" >&2
    exit 1
fi

mkdir -p "${RELEASES_DIR}"
NEW_RELEASE="${RELEASES_DIR}/${DEPLOY_SHA}"
PREVIOUS_RELEASE="$(readlink -f "${CURRENT_LINK}" 2>/dev/null || true)"

# 同一提交重试时允许重新创建未激活的 release。
if [[ "${PREVIOUS_RELEASE}" == "${NEW_RELEASE}" ]]; then
    echo "目标提交已经是当前版本: ${DEPLOY_SHA}"
    exit 0
fi
rm -rf -- "${NEW_RELEASE}"
mkdir -p "${NEW_RELEASE}"
cp -a "${INCOMING_DIR}/." "${NEW_RELEASE}/"

python3.14 -m venv "${NEW_RELEASE}/.venv"
"${NEW_RELEASE}/.venv/bin/pip" install --disable-pip-version-check --upgrade pip
"${NEW_RELEASE}/.venv/bin/pip" install --disable-pip-version-check -r "${NEW_RELEASE}/requirements.txt"

# server.env 是受管理员控制的 shell-compatible KEY=VALUE 文件。
set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

if [[ -z "${DATABASE_URL:-}" || "${DATABASE_URL}" != postgresql+psycopg://* ]]; then
    echo "DATABASE_URL 未配置或不是 postgresql+psycopg:// 连接串" >&2
    exit 1
fi

"${NEW_RELEASE}/.venv/bin/alembic" -c "${NEW_RELEASE}/alembic.ini" upgrade head

NEXT_LINK="${CURRENT_LINK}.next"
rm -f -- "${NEXT_LINK}"
ln -s "${NEW_RELEASE}" "${NEXT_LINK}"
mv -Tf "${NEXT_LINK}" "${CURRENT_LINK}"

rollback() {
    echo "健康检查失败，开始回滚..." >&2
    if [[ -n "${PREVIOUS_RELEASE}" && -d "${PREVIOUS_RELEASE}" ]]; then
        local rollback_link="${CURRENT_LINK}.rollback"
        rm -f -- "${rollback_link}"
        ln -s "${PREVIOUS_RELEASE}" "${rollback_link}"
        mv -Tf "${rollback_link}" "${CURRENT_LINK}"
        sudo -n systemctl restart "${SERVICE_NAME}" || true
        echo "已恢复版本: ${PREVIOUS_RELEASE}" >&2
    else
        rm -f -- "${CURRENT_LINK}"
        sudo -n systemctl stop "${SERVICE_NAME}" || true
        echo "这是首次部署，没有可恢复的旧版本；服务已停止。" >&2
    fi
}

if ! sudo -n systemctl restart "${SERVICE_NAME}"; then
    rollback
    exit 1
fi

for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++)); do
    if curl -fsS --max-time 5 "${HEALTH_URL}" >/dev/null && curl -fsS --max-time 5 "${DATABASE_HEALTH_URL}" >/dev/null; then
        echo "部署成功: ${DEPLOY_SHA}"
        exit 0
    fi
    sleep "${HEALTH_DELAY}"
done

rollback
exit 1

