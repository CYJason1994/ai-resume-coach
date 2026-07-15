#!/usr/bin/env bash
# =============================================================================
# scripts/rollback.sh —— 生产回滚
#
# 支持两种回滚模式：
#
#   [1] 镜像回滚（推荐，秒级）
#       按 服务 + tag 或 digest 拉取历史镜像并重新部署，无需重新构建。
#       用法:
#         ./scripts/rollback.sh image <service> <tag>
#         ./scripts/rollback.sh image <service> <sha256:xxxx>     # 按 digest
#       例:
#         ./scripts/rollback.sh image api v1.4.2
#         ./scripts/rollback.sh image api sha256:9f86d08...
#         ./scripts/rollback.sh image web v1.4.2
#
#   [2] Git 标签回滚（重建，慢但最可信）
#       切到指定 git tag → 重新构建镜像 → 重新部署。
#       用法:
#         ./scripts/rollback.sh git <tag>
#       例:
#         ./scripts/rollback.sh git v1.4.2
#
# 通用参数:
#   -f, --file <compose>   指定 compose 文件（默认 compose.prod.yml）
#   -h, --help             显示帮助
#
# 注意：
#   - 镜像模式要求目标镜像已推送到 ${DOCKER_REGISTRY} 指向的仓库。
#   - 回滚只影响指定服务；如需数据库 schema 回滚，请结合迁移版本管理（见 DEPLOY.md）。
# =============================================================================

set -euo pipefail

COMPOSE_FILE="compose.prod.yml"
REGISTRY="${DOCKER_REGISTRY:-ai-resume-coach}"

usage() {
  sed -n '1,40p' "${BASH_SOURCE[0]}" | grep '^#' | sed 's/^# \{0,1\}//'
  exit 0
}

# ── 解析参数 ────────────────────────────────────────────────────────────────
MODE=""
SERVICE=""
REF=""
GIT_TAG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -f|--file) COMPOSE_FILE="$2"; shift 2 ;;
    -h|--help) usage ;;
    image) MODE="image"; SERVICE="${2:-}"; REF="${3:-}"; shift 3 ;;
    git)   MODE="git";   GIT_TAG="${2:-}"; shift 2 ;;
    *) echo "[ERROR] 未知参数: $1" >&2; usage ;;
  esac
done

if [[ -z "${MODE}" ]]; then
  echo "[ERROR] 未指定回滚模式 (image | git)。" >&2
  usage
fi

# 服务 -> compose 镜像基名（与 compose.prod.yml 中 image 字段保持一致）
image_base() {
  case "$1" in
    api|worker) echo "${REGISTRY}/api" ;;
    web)        echo "${REGISTRY}/web" ;;
    *) echo "[ERROR] 不支持的服务: $1（可选: api | worker | web）" >&2; exit 1 ;;
  esac
}

# ── 模式 1：镜像回滚 ───────────────────────────────────────────────────────
rollback_image() {
  if [[ -z "${SERVICE}" || -z "${REF}" ]]; then
    echo "[ERROR] 镜像回滚需提供: <service> <tag|digest>" >&2
    exit 1
  fi
  local BASE
  BASE="$(image_base "${SERVICE}")"

  # 拼出远程引用：digest 用 @，tag 用 :
  local REMOTE_REF COMPOSE_TAG
  if [[ "${REF}" == sha256:* ]]; then
    REMOTE_REF="${BASE}@${REF}"
    COMPOSE_TAG="${REF}"          # 仅用于给 compose 的 image 打 tag 对齐
  else
    REMOTE_REF="${BASE}:${REF}"
    COMPOSE_TAG="${REF}"
  fi

  echo "[INFO] 拉取历史镜像: ${REMOTE_REF}"
  docker pull "${REMOTE_REF}"

  # 将拉取的镜像重新打 tag，使其与 compose 的 image 字段一致（便于 --no-build 复用）
  local COMPOSE_IMAGE="${BASE}:${COMPOSE_TAG}"
  docker tag "${REMOTE_REF}" "${COMPOSE_IMAGE}"

  # 用对齐后的 tag 重新部署该服务（--no-build 避免本地重新构建覆盖）
  echo "[INFO] 重新部署服务 ${SERVICE} (image=${COMPOSE_IMAGE}) ..."
  API_IMAGE_TAG="${COMPOSE_TAG}" WEB_IMAGE_TAG="${COMPOSE_TAG}" \
    docker compose -f "${COMPOSE_FILE}" up -d --no-build "${SERVICE}"

  echo "[OK] 已回滚 ${SERVICE} -> ${REMOTE_REF}"
}

# ── 模式 2：Git 标签回滚（重建）────────────────────────────────────────────
rollback_git() {
  if [[ -z "${GIT_TAG}" ]]; then
    echo "[ERROR] Git 回滚需提供: <tag>" >&2
    exit 1
  fi
  echo "[INFO] 切换到 git tag: ${GIT_TAG}"
  git fetch --tags
  git checkout "${GIT_TAG}"

  echo "[INFO] 重新构建并部署全部服务 ..."
  docker compose -f "${COMPOSE_FILE}" build
  docker compose -f "${COMPOSE_FILE}" up -d

  echo "[OK] 已按 git tag ${GIT_TAG} 重建并部署。"
}

# ── 执行 ───────────────────────────────────────────────────────────────────
case "${MODE}" in
  image) rollback_image ;;
  git)   rollback_git ;;
esac
