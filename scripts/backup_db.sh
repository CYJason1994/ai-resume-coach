#!/usr/bin/env bash
# =============================================================================
# scripts/backup_db.sh —— 生产数据库全量备份 + 加密归档 + 保留策略
#
# 流程：pg_dump 全量导出（自定义格式 -Fc）→ openssl aes-256-cbc 加密 →
#       按日期命名存入 backups/ → 仅保留最近 N 份 → 输出日志。
#
# 前置：
#   - 生产数据库通过 compose 的 postgres 服务运行（默认）。
#   - 加密口令从环境变量 BACKUP_ENCRYPT_PASS 读取（必填，缺失则退出）。
#   - 需要 docker（用于 exec 进 postgres 容器执行 pg_dump）。
#     若想直连宿主 pg_dump，可设置 PG_DUMP_BIN 与 DATABASE_URL 走“本地模式”。
#
# 用法：
#   BACKUP_ENCRYPT_PASS='***' ./scripts/backup_db.sh
#   BACKUP_ENCRYPT_PASS='***' BACKUP_KEEP_COUNT=7 ./scripts/backup_db.sh
# =============================================================================

set -euo pipefail

# ── 可配置项（环境变量覆盖）─────────────────────────────────────────────────
COMPOSE_FILE="${COMPOSE_FILE:-compose.prod.yml}"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BACKUP_DIR="${BACKUP_DIR:-${PROJECT_DIR}/backups}"
PG_SERVICE="${PG_SERVICE:-postgres}"
PG_USER="${PG_USER:-${POSTGRES_USER:-postgres}}"
PG_DB="${PG_DB:-${POSTGRES_DB:-resume_coach}}"
# 保留份数（默认 7）
BACKUP_KEEP_COUNT="${BACKUP_KEEP_COUNT:-7}"
# openssl 迭代次数（提高暴力破解成本）
OPENSSL_ITER="${OPENSSL_ITER:-100000}"

# ── 校验必填项 ──────────────────────────────────────────────────────────────
if [[ -z "${BACKUP_ENCRYPT_PASS:-}" ]]; then
  echo "[ERROR] 必须设置环境变量 BACKUP_ENCRYPT_PASS（备份加密口令）。" >&2
  exit 1
fi

# ── 准备目录与时间戳 ─────────────────────────────────────────────────────────
mkdir -p "${BACKUP_DIR}"
TS="$(date +%Y%m%d_%H%M%S)"
DUMP_FILE="${BACKUP_DIR}/db_${PG_DB}_${TS}.sqlc"          # pg_dump 自定义格式（未加密）
ENC_FILE="${BACKUP_DIR}/db_${PG_DB}_${TS}.sqlc.enc"       # 加密归档（最终产物）
LOG_FILE="${BACKUP_DIR}/backup.log"

log() {
  local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
  echo "${msg}"
  echo "${msg}" >> "${LOG_FILE}"
}

# ── 1) pg_dump 全量导出 ─────────────────────────────────────────────────────
# 优先走 compose 容器内的 pg_dump（无需宿主安装 postgres 客户端）。
# 若设置 PG_DUMP_BIN，则使用本地 pg_dump（需 DATABASE_URL 指向可达实例）。
log "开始备份数据库 ${PG_DB} (service=${PG_SERVICE}) ..."

if [[ -n "${PG_DUMP_BIN:-}" ]]; then
  if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "[ERROR] 使用本地 PG_DUMP_BIN 时必须提供 DATABASE_URL。" >&2
    exit 1
  fi
  "${PG_DUMP_BIN}" --format=custom --no-owner --clean --if-exists \
    --dbname="${DATABASE_URL}" > "${DUMP_FILE}"
else
  # 容器内 pg_dump：通过 docker compose exec 执行（生产推荐路径）
  docker compose -f "${COMPOSE_FILE}" exec -T "${PG_SERVICE}" \
    pg_dump --format=custom --no-owner --clean --if-exists \
    -U "${PG_USER}" -d "${PG_DB}" > "${DUMP_FILE}"
fi

# 简单校验导出文件非空
if [[ ! -s "${DUMP_FILE}" ]]; then
  log "[ERROR] pg_dump 输出为空，备份中止。"
  rm -f "${DUMP_FILE}"
  exit 1
fi

# ── 2) 加密归档（aes-256-cbc + pbkdf2 + salt）─────────────────────────────
log "导出完成，开始加密为 ${ENC_FILE} ..."
openssl enc -aes-256-cbc -salt -pbkdf2 -iter "${OPENSSL_ITER}" \
  -in "${DUMP_FILE}" -out "${ENC_FILE}" \
  -pass "env:BACKUP_ENCRYPT_PASS"

# 删除明文 dump，仅保留加密归档
rm -f "${DUMP_FILE}"

if [[ ! -s "${ENC_FILE}" ]]; then
  log "[ERROR] 加密产物为空，备份失败。"
  exit 1
fi
log "加密归档生成成功: ${ENC_FILE} ($(du -h "${ENC_FILE}" | cut -f1))"

# ── 3) 保留策略：仅保留最近 N 份加密归档 ───────────────────────────────────
# 按文件名时间戳排序，删除超出保留份数的最旧文件。
mapfile -t OLD_FILES < <(ls -1t "${BACKUP_DIR}"/db_${PG_DB}_*.sqlc.enc 2>/dev/null | tail -n +$((BACKUP_KEEP_COUNT + 1)))
if [[ ${#OLD_FILES[@]} -gt 0 ]]; then
  log "清理超出保留份数(${BACKUP_KEEP_COUNT})的旧备份: ${#OLD_FILES[@]} 个"
  for f in "${OLD_FILES[@]}"; do
    rm -f "${f}"
    log "  已删除旧备份: ${f}"
  done
else
  log "无需清理旧备份（保留份数=${BACKUP_KEEP_COUNT}）。"
fi

log "备份完成。当前保留归档:"
ls -1ht "${BACKUP_DIR}"/db_${PG_DB}_*.sqlc.enc 2>/dev/null | head -n "${BACKUP_KEEP_COUNT}" | sed 's/^/  - /'
