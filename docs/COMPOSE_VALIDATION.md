# ai-resume-coach — Compose 静态验证报告 (M4)

**范围**：`compose.prod.yml`、`compose.observ.yml`、`docker-compose.dev.yml` 静态校验。
**环境**：沙箱无 Docker，未执行 `docker compose config`，仅为只读静态验证。

| # | Check | Result | Evidence |
|---|-------|--------|----------|
| 1 | YAML 解析（3 个文件） | **PASS** | `python -c yaml.safe_load(...)` → `YAML_OK`，无解析错误（用 `C:/Users/admin/.workbuddy/binaries/python/versions/3.13.12/python.exe`）。 |
| 2 | Build 上下文 / Dockerfile | **PASS** | `apps/api/Dockerfile`（FROM python:3.12-slim）与 `apps/web/Dockerfile`（FROM node:20-alpine）均存在；compose 中 `build.context: ./apps/api|./apps/web` + `dockerfile: Dockerfile` 均能命中。无缺失引用。 |
| 3 | `./...` 绑定挂载源存在性 | **PARTIAL** | 存在的：`data/jobs`、`data/jobs/curated`、`data/jobs/zh_overlay.json`、`observ/otel-collector-config.yaml`、`observ/tempo-config.yaml`、`observ/prometheus.yml`、`observ/grafana/datasources.yaml`、`observ/grafana/dashboards.yaml`、`observ/grafana-dashboard.json`。**缺失**：`data/jobs/onet/snapshot.json`（部署时由 `python data/jobs/onet/ingest.py --out data/jobs/onet/snapshot.json` 生成，非 compose 缺陷）。 |
| 4 | 环境变量接线正确性 | **PASS** | api/worker/migrate 均 `REDIS_URL=redis://redis:6379/0`；DATABASE_URL 均用服务名 `postgres:5432`（L81/106/186）。无硬编码密钥：POSTGRES_PASSWORD、MINIO_ROOT_PASSWORD、GRAFANA 口令均为 `${...}`（L34/222/64），仅含弱默认。Grafana：`GF_AUTH_ANONYMOUS_ENABLED: "false"`（L63）；`GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:-change-me-in-prod}`（L64）；端口 `127.0.0.1:3002:3000`（L70）。api/worker 的 `ONET_SNAPSHOT_PATH=/app/data/jobs/onet/snapshot.json`、`CURATED_JOBS_DIR=/app/data/jobs/curated`、`ZH_OVERLAY_PATH=/app/data/jobs/zh_overlay.json`（L112-114/190-192）均指向挂载路径。 |
| 5 | 依赖启动顺序 | **PASS** | api `depends_on` postgres+redis healthy（L116-119）；worker `depends_on` postgres+redis healthy **且** api healthy（L194-200，规避迁移竞态）；migrate `depends_on` postgres+redis healthy（L84-87）。无缺口。 |
| 6 | 命名卷声明 | **PASS** | 引用的 `pgdata`、`redisdata`、`appdata`、`miniodata` 均在 `compose.prod.yml` 顶层 `volumes:` 声明（L235-239）；`compose.observ.yml` 仅用绑定挂载，无命名卷未声明问题。 |

**发现的观察项（非 compose 缺陷）**
- `data/jobs/onet/snapshot.json` 部署前需执行 ingest 生成；缺失不会使 `config` 失败，但 api/worker 启动后 seed 会读不到 O*NET 快照（回退 curated）。
- 弱默认口令：`postgres` / `minioadmin` / `change-me-in-prod`，需在 `.env` 中以强值覆盖（已用 `${...}`，符合约定）。

Final `docker compose -f compose.prod.yml config` and `docker compose -f compose.observ.yml config` must be run on the deploy machine (sandbox has no docker) before production launch.
