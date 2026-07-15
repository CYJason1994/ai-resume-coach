#!/usr/bin/env bash
# =============================================================================
# scripts/restore_db.sh —— 从加密归档恢复生产数据库
#
# 流程：解密（openssl）→ pg_restore 全量恢复 → 完成。
# 默认采用“先清后恢复”策略（--clean --if-exists），会覆盖目标库现有数据！
#
# ⚠️ 高危操作：恢复会覆盖线上数据库。执行前会打印警告并要求人工确认。
#
# 前置：
#   - BACKUP_ENCRYPT_PASS：解密口令（必填）。
#   - 目标数据库通过 compose 的 postgres 服务运行（默认）。
#   - 需要 docker（用于 exec 进 postgres 容器执行 pg_restore）。
#
# 用法：
#   BACKUP_ENCRYPT_PASS='***' ./scripts/restore_db.sh backups/db_xxx.sqlc.enc
#   BACKUP_ENCRYPT_PASS='***' DRY_RUN=1 ./scripts/restore_db.sh <file>   # 仅解密校验，不恢复
# =============================================================================

set -euo pipefail

# ── 可配置项 ───────────────────────────────────────────────────────────────
COMPOSE_FILE="${COMPOSE_FILE:-compose.prod.yml}"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PG_SERVICE="${PG_SERVICE:-postgres}"
PG_USER="${PG_USER:-${POSTGRES_USER:-postgres}}"
PG_DB="${PG_DB:-${POSTGRES_DB:-resume_coach}}"
RESTORE_TMP="${RESTORE_TMP:-/tmp/resume_coach_restore_$$}"   # 解密后的明文临时目录

# ── 校验参数与必填项 ───────────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
  echo "用法: BACKUP_ENCRYPT_PASS='***' $0 <加密归档文件>" >&2
  exit 1
fi
ENC_FILE="$1"
if [[ ! -f "${ENC_FILE}" ]]; then
  echo "[ERROR] 归档文件不存在: ${ENC_FILE}" >&2
  exit 1
fi
if [[ -z "${BACKUP_ENCRYPT_PASS:-}" ]]; then
  echo "[ERROR] 必须设置环境变量 BACKUP_ENCRYPT_PASS（解密口令）。" >&2
  exit 1
fi

# ── 高危警告 + 人工确认 ─────────────────────────────────────────────────────
echo "==================================================================="
echo "  ⚠️  高危操作：数据库恢复"
echo "  目标服务 : ${PG_SERVICE}"
echo "  目标库   : ${PG_DB} (用户: ${PG_USER})"
echo "  归档文件 : ${ENC_FILE}"
echo "  策略     : 先 --clean 清空再全量恢复（将覆盖现有全部数据）"
echo "==================================================================="
read -r -p "确认要恢复以上数据库吗？输入 'YES' 继续: " CONFIRM
if [[ "${CONFIRM}" != "YES" ]]; then
  echo "已取消恢复操作。"
  exit 0
fi

# ── 1) 解密 ─────────────────────────────────────────────────────────────────
mkdir -p "${RESTORE_TMP}"
PLAIN_FILE="${RESTORE_TMP}/db_restore.sqlc"
echo "[INFO] 正在解密归档 ..."
openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 \
  -in "${ENC_FILE}" -out "${PLAIN_FILE}" \
  -pass "env:BACKUP_ENCRYPT_PASS"

if [[ ! -s "${PLAIN_FILE}" ]]; then
  echo "[ERROR] 解密失败（口令错误或非法的归档文件）。" >&2
  rm -rf "${RESTORE_TMP}"
  exit 1
fi
echo "[INFO] 解密成功: ${PLAIN_FILE}"

# 试运行：仅解密校验，不真正恢复
if [[ "${DRY_RUN:-0}" == "1" ]]; then
  echo "[INFO] DRY_RUN=1，仅校验解密，不执行恢复。"
  rm -rf "${RESTORE_TMP}"
  exit 0
fi

# ── 2) 恢复（pg_restore 自定义格式）────────────────────────────────────────
# 通过 docker compose exec 将明文 dump 管道喂给容器内的 pg_restore。
# 使用 --clean --if-exists 先清后恢复，保证幂等；--no-owner 避免属主问题。
echo "[INFO] 开始恢复数据库 ${PG_DB} ..."
docker compose -f "${COMPOSE_FILE}" exec -T "${PG_SERVICE}" \
  pg_restore --format=custom --clean --if-exists --no-owner --verbose \
  -U "${PG_USER}" -d "${PG_DB}" < "${PLAIN_FILE}"

echo "[INFO] 恢复完成。"

# ── 清理明文临时文件 ────────────────────────────────────────────────────────
rm -rf "${RESTORE_TMP}"
echo "[INFO] 已清理临时明文文件。"
