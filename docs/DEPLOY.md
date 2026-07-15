# 部署 Runbook（Production Deployment Runbook）

> 模块：M4 W6 —— 部署 / 回滚 / 备份
> 适用环境：生产（对应 `compose.prod.yml`）
> 维护者：DevOps / 平台工程

本文件是生产部署、迁移、回滚、备份/恢复的操作手册。开发环境见 `docker-compose.dev.yml`，二者配置隔离。

---

## 0. 架构与服务拓扑

| 服务 | 镜像 | 依赖 | 端口（默认） | 说明 |
|---|---|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | — | 5432 | Postgres + pgvector，卷 `pgdata` 持久化 |
| `redis` | `redis:7-alpine` | — | 6379 | ARQ 队列 + 缓存，AOF 持久化，卷 `redisdata` |
| `migrate` | 同 api | postgres+redis 健康 | — | 一次性 `alembic upgrade head` job |
| `api` | `${DOCKER_REGISTRY:-ai-resume-coach}/api:${API_IMAGE_TAG:-latest}` | postgres+redis 健康 | 3001 | FastAPI/uvicorn，启动先跑迁移 |
| `web` | `${DOCKER_REGISTRY:-ai-resume-coach}/web:${WEB_IMAGE_TAG:-latest}` | api | 3000 | Next.js 14 生产构建 |
| `worker` | 同 api | postgres+redis 健康 + api 健康 | — | ARQ 后台任务 |
| `minio` | `minio/minio:latest` | — | 9000/9001 | **可选**（profile `optional`） |

> 所有常驻服务均配置 `restart: unless-stopped` + `healthcheck`。
> 镜像 tag 全部变量化，便于按 tag / digest 回滚（见 §4）。

---

## 1. 首次部署

### 1.1 准备
```bash
# 1) 获取代码（生产分支/Tag）
git clone <repo-url> ai-resume-coach
cd ai-resume-coach
git checkout develop          # 或指定的 release tag

# 2) 配置环境变量（.env 不入库！）
cp .env.example .env
# 然后编辑 .env，至少覆盖 §2 的“生产必须设置”清单
```

### 1.2 配置镜像仓库与 tag（回滚需要）
在部署机 shell 或 CI 中导出：
```bash
export DOCKER_REGISTRY=your-registry.example.com/ai-resume-coach
export API_IMAGE_TAG=1.4.2
export WEB_IMAGE_TAG=1.4.2
```
> 若暂不推送镜像、仅在单机构建：`DOCKER_REGISTRY` 与 `*_IMAGE_TAG` 可留默认，compose 会基于 `build` 上下文本地构建。

### 1.3 构建 + 迁移 + 启动
```bash
# 构建镜像（api/web/worker）
docker compose -f compose.prod.yml build

# 显式跑一次数据库迁移（幂等；api 启动时也会再跑一次）
docker compose -f compose.prod.yml run --rm migrate

# 启动全部服务
docker compose -f compose.prod.yml --env-file .env up -d

# 如需启用可选对象存储（minio）
docker compose -f compose.prod.yml --profile optional up -d minio
```

### 1.4 验证
```bash
# 存活探针
curl -f http://localhost:3001/health      # 期望 {"status":"ok",...}
# 就绪探针（含 DB/Redis 探活）
curl -f http://localhost:3001/ready       # 期望 database/redis=ok
# Web
curl -f http://localhost:3000             # 期望 Next.js 页面
```
确认 `docker compose -f compose.prod.yml ps` 各服务 STATE=running 且 STATUS 含 `healthy`。

---

## 2. 密钥管理

- **`.env` 严禁提交进版本库**（已在 `.gitignore` 忽略 `*.env`）。
- 生产密钥建议走 **密钥管理器 / CI Secret**（如 Vault、云厂商 Secret Manager），运行时注入为环境变量或挂载文件，避免明文落盘。
- `compose.prod.yml` 通过 `env_file: .env` 注入；其中 `DATABASE_URL` / `REDIS_URL` 在 compose 内被覆盖为服务名（`postgres`/`redis`），无需在 `.env` 中写为内网地址。

### 生产必须设置的变量清单（`.env`）
| 变量 | 必填 | 说明 |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | ✅ | 数据库凭据；**密码必须强随机** |
| `DATABASE_URL` | ✅ | 应用连接串（compose 内会被覆盖为 `postgres` 主机，但仍需保留字段） |
| `REDIS_URL` | ✅ | 同上前（compose 内覆盖为 `redis` 主机） |
| `DEEPSEEK_API_KEY` | ✅ | LLM 调用密钥 |
| `AUTH_JWT_SECRET` | ✅ | JWT 签名密钥（若 M4 W1 启用账号体系；强随机 ≥32B） |
| `SENTRY_DSN` | 推荐 | 错误上报；留空则静默 |
| `CORS_ORIGINS` | ✅ | 显式来源列表（**禁止 `*`**），如 `https://app.example.com` |
| `STORAGE_PROVIDER` / `STORAGE_MINIO_*` | 条件 | `local`（默认）或 `minio`（启用 minio profile 时） |
| `LLM_*` | 推荐 | 模型/超时/预算护栏（见 `.env.example`） |
| `ACCESS_TOKEN_BYTES` / `ACCESS_TOKEN_HASH_ALGO` | ✅ | 匿名访问控制参数 |
| `MAX_UPLOAD_BYTES` | ✅ | 上传大小上限 |

> 完整清单与默认值见 `.env.example`。**切勿把 `.env` 当构建参数 `ARG` 写进镜像**。

---

## 3. 数据库迁移流程

迁移基于 **Alembic**（`apps/api/migrations`），连接串统一从 `app.core.config.get_settings().DATABASE_URL` 读取。

```bash
# 方式 A：利用 compose 的 migrate 服务（推荐，自动等 DB 健康）
docker compose -f compose.prod.yml run --rm migrate

# 方式 B：进入 api 容器执行
docker compose -f compose.prod.yml exec api alembic upgrade head

# 生成新迁移（开发期；生产通常由 CI 生成并提交）
docker compose -f compose.prod.yml exec api alembic revision -m "desc" --autogenerate
```

- `api` 服务启动命令已包含 `alembic upgrade head && uvicorn ...`，**启动即自动对齐 schema**（幂等）。
- `worker` 通过 `depends_on: api(healthy)` 串行等待，避免与 api 同时迁移产生竞态。
- 注意：`apps/api` 的 Dockerfile **未打包 `alembic.ini` / `migrations`**，`compose.prod.yml` 通过 bind mount 从宿主机挂载这两个目录到容器，因此部署机必须保留源码树中的这两个路径（git checkout 即满足）。

---

## 4. 回滚流程

脚本：`scripts/rollback.sh`。支持两种模式（详见脚本内 `usage`）。

### 4.1 镜像回滚（推荐，秒级）
需目标镜像已推送到 `${DOCKER_REGISTRY}` 指向的仓库。
```bash
# 按 tag 回滚 api
./scripts/rollback.sh image api v1.4.1
# 按 digest 精确回滚（最可复现）
./scripts/rollback.sh image api sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08
# 回滚 web
./scripts/rollback.sh image web v1.4.1
```
> 原理：拉取历史镜像 → 重新 `docker tag` 对齐 compose 的 `image` 引用 → `docker compose up -d --no-build <service>`。仅影响指定服务。

### 4.2 Git 标签回滚（重建，最可信）
```bash
./scripts/rollback.sh git v1.4.1
```
> 原理：`git checkout <tag>` → `docker compose build` → `up -d`。会全量重建，耗时较长；适合镜像供应链不可信或需连带代码回滚的场景。

### 4.3 数据库 schema 回滚
代码/镜像回滚后若涉及表结构变化，需同步回退迁移：
```bash
# 回退到指定版本（<rev> 为 alembic 版本号或 -1 表示上一版）
docker compose -f compose.prod.yml exec api alembic downgrade <rev>
```
> ⚠️ `downgrade` 会丢弃新增列/表数据，务必先按 §5 备份。

---

## 5. 备份与恢复

脚本：`scripts/backup_db.sh`、`scripts/restore_db.sh`。

### 5.1 备份（建议每日 cron）
```bash
# 必须提供加密口令；默认保留最近 7 份
BACKUP_ENCRYPT_PASS='<强口令>' ./scripts/backup_db.sh
# 自定义保留份数
BACKUP_ENCRYPT_PASS='<强口令>' BACKUP_KEEP_COUNT=14 ./scripts/backup_db.sh
```
- 产物：`backups/db_<db>_<YYYYMMDD_HHMMSS>.sqlc.enc`（pg_dump 自定义格式 + `aes-256-cbc` + `pbkdf2` 加密）。
- 明文 dump 在加密后立即删除，磁盘上只留加密归档。
- 本地模式（宿主已装 pg 客户端）：设 `PG_DUMP_BIN=pg_dump` 并传 `DATABASE_URL` 直连。
- **加密口令务必单独保管**（如密钥管理器），丢失则备份不可恢复。

### 5.2 恢复（高危，需人工确认）
```bash
BACKUP_ENCRYPT_PASS='<强口令>' ./scripts/restore_db.sh backups/db_xxx.sqlc.enc
# 仅解密校验、不真正恢复：
BACKUP_ENCRYPT_PASS='<强口令>' DRY_RUN=1 ./scripts/restore_db.sh backups/db_xxx.sqlc.enc
```
- 执行前打印警告并要求输入 `YES` 确认。
- 采用 `--clean --if-exists` 先清后恢复，**会覆盖目标库全部现有数据**。
- 强烈建议：恢复前先执行一次 `backup_db.sh` 做当前态快照。

---

## 6. 监控接入

- **错误上报**：配置 `.env` 的 `SENTRY_DSN`，应用层自动上报（无 DSN 静默）。
- **可观测栈（OTel + Grafana）**：若 M4 W3 已实现独立的 `compose.observ.yml`（OTel Collector + Tempo/Prometheus + Grafana），在部署机叠加启动：
  ```bash
  docker compose -f compose.observ.yml up -d
  ```
  并将 api 的 OTel 导出端点指向该栈（通过 `.env` 中的相关导出变量配置）。
- **健康检查探针**：外部 LB / 探针应打 `/health`（存活）与 `/ready`（就绪，含 DB/Redis 探活，503 表示依赖未就绪）。
- **日志**：api 已输出结构化 JSON 日志（含 request_id），建议采集到集中日志系统。

---

## 7. 常见故障排查

| 现象 | 可能原因 | 排查 / 处理 |
|---|---|---|
| `api` 一直 `unhealthy` | DB/Redis 未就绪，或迁移卡住 | `docker compose logs api`；确认 `/ready` 的 `checks` 字段；手动 `run --rm migrate` |
| 启动报 `alembic: command not found` | 容器内缺少 alembic.ini/migrations | 确认宿主机 `apps/api/alembic.ini`、`apps/api/migrations` 存在（bind mount 来源） |
| 迁移竞态 / 锁等待 | api 与 worker 同时迁移 | 已通过 `worker depends_on api(healthy)` 规避；若仍发生，先 `up api` 跑完迁移再 `up worker` |
| `web` 构建慢 / 启动超时 | `npm run build` 在容器启动期执行 | 属预期；如需更快，改为预先 build 进镜像（生产 Dockerfile 多阶段 + `output: standalone`） |
| 备份报 `BACKUP_ENCRYPT_PASS` 缺失 | 未导出加密口令 | 恢复/备份前 `export BACKUP_ENCRYPT_PASS=...` |
| 恢复后数据异常 | schema 与代码版本不匹配 | 回滚时同步执行 `alembic downgrade <rev>`（见 §4.3），并优先从备份恢复 |
| 回滚 `docker pull` 失败 | 镜像未推送 / registry 不可达 | 确认 `DOCKER_REGISTRY` 与目标 tag/digest；或改用 `git` 模式重建 |
| CORS 报错 | `CORS_ORIGINS` 含 `*` 或未包含前端域名 | 改为显式来源列表，重启 `api` |

---

## 附：快速命令速查

```bash
# 配置校验（无需起容器）
docker compose -f compose.prod.yml config

# 启停
docker compose -f compose.prod.yml up -d
docker compose -f compose.prod.yml down

# 状态 / 日志
docker compose -f compose.prod.yml ps
docker compose -f compose.prod.yml logs -f api

# 迁移
docker compose -f compose.prod.yml run --rm migrate

# 回滚
./scripts/rollback.sh image api v1.4.1
./scripts/rollback.sh git v1.4.1

# 备份 / 恢复
BACKUP_ENCRYPT_PASS='***' ./scripts/backup_db.sh
BACKUP_ENCRYPT_PASS='***' ./scripts/restore_db.sh backups/db_xxx.sqlc.enc
```
