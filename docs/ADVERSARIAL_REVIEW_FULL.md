# 全面对抗性审查报告（M0–M4 累计产出）

> 审查对象：远程 `develop` @ `9399b87`（已推，工作树干净）
> 审查方式：**4 个独立 agent 分域并行**，均未参与写码，规避"自己写自己审"盲区
> - Agent-A 后端安全 & 生产加固
> - Agent-B 后端核心链路正确性（解析/结构化/匹配/面试/Worker/降级）
> - Agent-C 前端 & 无障碍/性能
> - Agent-D 部署/运维/数据/迁移/CI
> 日期：2026-07-15

---

## 0. 总体结论

**尚不具备生产就绪（NOT production-ready）。**

发现 **1 × P0、8 × P1、15 × P2**，以及约 15 项 P3 加固点。

- **P0（1）**：生产环境若漏配 `AUTH_JWT_SECRET`，会话用硬编码公开常量签名 → **可伪造任意用户会话，账户被接管**。
- **P1（8）**：含一处会**重复执行请求/双倍计费**的中间件 bug、一处 **prod 岗位库为空**（卷覆盖种子数据）、CI web 门禁断裂、可观测回滚脚本失效、Grafana 匿名 Admin 等 —— 任一项都足以在部署后造成功能失效或安全暴露。
- **P2（15）**：多为"功能可跑但有缺陷/偏离契约"项（重复写入、PII 漏进嵌入、任务卡死、迁移不可移植、eval 基线过弱等）。

> 独立审查抓到的关键盲区（自审易漏）：P0 默认密钥、P1 observability 重复 `call_next`、P1 prod `appdata` 卷遮蔽 `data/jobs`、P1 `rollback.sh` digest 标签非法。

---

## 1. 优先级速览

| ID | 域 | 级 | 标题 | 位置 | 一句话影响 |
|----|----|----|------|------|-----------|
| P0-1 | 安全 | **P0** | JWT 默认密钥 fail-open（认证绕过） | `auth.py:24-30`, `config.py:53` | 漏配密钥 → 会话可被伪造 → 账户接管 |
| P1-1 | 安全 | P1 | observability 中间件错误时二次 `call_next` | `observability.py:310-317` | 上传/写库/入队/计费被重复执行 |
| P1-2 | 部署 | P1 | prod `appdata` 卷遮蔽 `data/jobs` → 岗位库为空 | `compose.prod.yml:117-121,191-194`; `apps/api/Dockerfile:12` | prod 匹配零结果 |
| P1-3 | 部署 | P1 | `rollback.sh` digest 模式拼出非法镜像标签 | `scripts/rollback.sh:84-102` | 最可复现的回滚路径失效 |
| P1-4 | 部署 | P1 | CI web 作业 `cache-dependency-path` 指向不存在锁文件 | `.github/workflows/ci.yml:41-47` | web lint/build 门禁直接报错 |
| P1-5 | 部署 | P1 | Grafana 匿名 Admin + 硬编码密码并公开 | `compose.observ.yml:60-64` | 可达即获 Admin |
| P1-6 | 核心 | P1 | 单份成本熔断 `$0.01` 从未被读取/执行 | `config.py:43`; `llm.py:106-126` | 成本护栏对单份简历形同虚设 |
| P1-7 | 核心 | P1 | 配额 enforcer 不覆盖 Worker 侧 LLM 调用 | `quota.py:35`; `workers/tasks.py` | 后台 LLM 用量无配额约束 |
| P1-8 | 前端 | P1 | 匿名令牌被塞进 API 请求 query string | `lib/api.ts:76,82,87,105,128` | 令牌进访问日志/Referer → 泄露 |
| P2-1 | 安全 | P2 | `scan_bytes` 未接入上传真实路径（死代码） | `scan.py` 仅 tests 引用；`upload.py` 未调用 | 压缩炸弹/高熵检测不生效 |
| P2-2 | 安全 | P2 | `sanitize_llm_text` 从未被调用 | `sanitize.py` 仅 tests 引用 | LLM 文本消毒不生效 |
| P2-3 | 安全 | P2 | `access_token` 进 `result_url` query | `upload.py:91` | 令牌经 URL 泄露（与 P1-8 同源） |
| P2-4 | 核心 | P2 | `match_resume` 非幂等 → 重复 JobMatch | `matcher.py:81-92`; `models.py:118-128` | 重投递产生重复匹配行 |
| P2-5 | 核心 | P2 | `work_history` 漏进嵌入端点（PII） | `matcher.py:34-35` vs `interview_gen.py:47` | 公司名等 PII 上送嵌入 |
| P2-6 | 核心 | P2 | Worker 崩溃后任务永久 `running` | `tasks.py:79` | 简历卡死"处理中"无终态 |
| P2-7 | 部署 | P2 | 初始迁移缺 `CREATE EXTENSION vector` | `migrations/versions/0001_initial.py` | 托管 PG（RDS/CloudSQL）迁移失败 |
| P2-8 | 部署 | P2 | CI 门禁缺口（mypy `|| true`、无迁移校验、无 E2E） | `ci.yml:25-26` | 类型/迁移漂移不被拦截 |
| P2-9 | 部署 | P2 | eval 黄金集仅 4 条、无阈值 | `eval/golden.json`; `test_eval.py:21-29` | 质量基线过弱 |
| P2-10 | 部署 | P2 | prod 迁移依赖宿主机 bind-mount | `apps/api/Dockerfile`; `compose.prod.yml:89-91` | 无源码树的主机 api 重启循环 |
| P2-11 | 部署 | P2 | `restore_db.sh` 无 trap → 失败留明文 dump | `scripts/restore_db.sh:28,92` | /tmp 残留含 PII 的明文库 |
| P2-12 | 前端 | P2 | `prefers-reduced-motion` 未覆盖 JS 滚动 | `mock-interview/.../page.tsx:104` | 违反 WCAG 2.3.3 |
| P2-13 | 前端 | P2 | SSE `role="log"` 每 token 刷屏读屏器 | `mock-interview/.../page.tsx:249-256` | 流式时读屏器不可用 |
| P2-14 | 前端 | P2 | "system" 主题不响应 OS 实时切换 | `components/ThemeToggle.tsx:7-13` | OS 切主题 UI 需刷新才变 |
| P2-15 | 安全 | P2 | 配额按 token 多重性可放缩 | `main.py:88`; `quota.py:80-85` | 多令牌绕过单窗口限制（弱化） |
| P3-* | 各域 | P3 | 约 15 项加固（见 §5） | — | 不阻塞，建议择机收 |

---

## 2. P0 详述

### P0-1 JWT 默认密钥 fail-open（认证绕过）
- **位置**：`apps/api/app/core/auth.py:24-30`；`apps/api/app/core/config.py:53`
- **现状**：
  ```python
  _SECRET = settings.AUTH_JWT_SECRET or "dev-insecure-session-secret-CHANGE-ME"
  ```
  docstring 声称 "fail-loud"，实际仅 `logger.warning` 后继续。生产若漏配 `AUTH_JWT_SECRET`，所有会话用**公开常量**签名（HS256）。
- **影响**：攻击者可本地用该常量伪造任意用户（含管理员）的 session cookie → **账户完全接管**。叠加 `auth.py:102` 在审计 `resource_id` 记完整 `user.id` UUID，定位目标更易。
- **修复**：在 `get_settings()` 或应用启动时 `if settings.is_production and not settings.AUTH_JWT_SECRET: raise RuntimeError(...)`。**绝不发布可用默认值**。

---

## 3. P1 详述

### P1-1 observability 中间件错误时二次 `call_next`
- **位置**：`apps/api/app/core/observability.py:310-317`
- **现状**：`tracer is not None` 分支内，`call_next` 抛异常后 `except` 又调用一次 `call_next(request)`。
- **影响**：配置 OTLP（prod 路径 tracer 已设）时，任何 `POST /api/upload` 等处理器 500 → 请求体被**处理两次** → 重复落盘、重复 ARQ 入队、重复 DB 插入、**双倍 LLM 计费**。dev 不受影响（tracer None）。
- **修复**：删除第二次 `call_next`；内层异常直接 `raise`。

### P1-2 prod `appdata` 卷遮蔽 `data/jobs` → 岗位库为空
- **位置**：`compose.prod.yml:117-121,191-194`；`apps/api/Dockerfile:12`
- **现状**：`api`/`worker` 挂载 `appdata:/app/data`；Dockerfile 仅 `COPY data ./data`（只烘焙了 `eval/golden.json`）。宿主 `data/jobs/*` 在 prod **从不挂载**。环境变量 `ONET_SNAPSHOT_PATH`/`CURATED_JOBS_DIR`/`ZH_OVERLAY_PATH` 全部解析到该空命名卷。
- **影响**：`seed_jobs`/ARQ 播种读到空 → `jobs` 表空 → **prod 岗位匹配零结果**，功能失效。
- **修复**：拆分关注点——`STORAGE_LOCAL_DIR=/app/data/uploads` 留在 `appdata`，岗位数据显式挂载 `./data/jobs:/app/data/jobs:ro` 或 `COPY` 进镜像 `/app/data/jobs`。

### P1-3 `rollback.sh` digest 模式拼出非法镜像标签
- **位置**：`scripts/rollback.sh:84-102`
- **现状**：digest 分支 `COMPOSE_TAG="${REF}"`（即 `sha256:…`），再 `docker tag SRC ${BASE}:${COMPOSE_TAG}` → `ai-resume-coach/api:sha256:…`（标签含冒号非法），随后 `API_IMAGE_TAG=sha256:… docker compose up -d --no-build`。
- **影响**：`docker tag` 与 `docker compose up` 报 "invalid reference format" → **最可复现的回滚路径失效**。
- **修复**：用 compose digest 语法 `image: …@sha256:…`；或 `docker pull` 后 tag 到合法别名再更新 compose。

### P1-4 CI web 作业 `cache-dependency-path` 指向不存在锁文件
- **位置**：`.github/workflows/ci.yml:41-47`
- **现状**：`cache-dependency-path: apps/web/pnpm-lock.yaml`，但 workspace 锁文件在仓库根 `pnpm-lock.yaml`；`apps/web/` 仅有 `package.json`（已核实）。
- **影响**：`actions/setup-node` 报 "Dependencies lock file is not found" → **web lint/build 门禁直接报错/不跑**。
- **修复**：`cache-dependency-path: pnpm-lock.yaml`；`pnpm install --frozen-lockfile` 经 workspace 发现根锁文件。

### P1-5 Grafana 匿名 Admin + 硬编码密码并公开
- **位置**：`compose.observ.yml:60-64`
- **现状**：`GF_AUTH_ANONYMOUS_ENABLED:true`、`GF_AUTH_ANONYMOUS_ORG_ROLE:Admin`、`GF_SECURITY_ADMIN_PASSWORD:admin`，发布于 `:3002`。
- **影响**：按计划 prod 启用 observability profile 时，任何可达 `:3002` 者直接获 **Admin**，可查所有指标/日志。
- **修复**：关闭匿名、注入密钥管理的 admin 密码、仅绑定内部网络。

### P1-6 单份成本熔断 `$0.01` 从未执行
- **位置**：`config.py:43 LLM_COST_CAP_PER_RESUME=0.01`；`llm.py:_ensure_available 119-126`、`_track_cost 106-110`
- **现状**：该常量被定义但无人读取；`_ensure_available` 仅查日预算+降级，`_track_cost` 仅累计 `_daily_spend`。一份简历 = 1 次抽取 + 最多 10 次评分 + 嵌入；一次面试 = 最多 60 次 LLM 调用（30 轮 × 流+评）。
- **影响**：声明的 "$0.01/份" 护栏是装饰性的；长面试可击穿预算。
- **修复**：在 `_track_cost` 按 `resume_id`/`task_id` 累计单份花费，并在 `_ensure_available` 拒绝超单份上限（或明确文档"单份熔断仅约束 HTTP 侧"并在 LLM 层兜底）。

### P1-7 配额 enforcer 不覆盖 Worker 侧 LLM 调用
- **位置**：`quota.py:35 allow()`（仅 HTTP 路由调用）；`workers/tasks.py`（直接 `get_llm()`，无 `get_quota_enforcer`）
- **影响**：后台 LLM 用量不受 per-subject 配额约束，唯一节流是信号量(6)——与 P1-6 叠加，后台成本护栏整体失效。
- **修复**：在 worker 任务内调用配额/成本闸门，或文档明确"配额仅 HTTP 侧"并在 LLM 层强制单份熔断。

### P1-8 匿名令牌被塞进 API 请求 query string
- **位置**：`apps/web/lib/api.ts:76,82,87,105,128`（`getTask`/`getResult`/`deleteResume`/`getInterview`/`getSession`）
- **现状**：每个匿名 GET 在已带 `X-Access-Token` header 之外，又追加 `?token=...`。
- **影响**：令牌进后端访问日志（请求路径）+ 同源页面的 `Referer` 头 → 泄露超出"允许的结果链接"契约，存在重放风险（与 P2-3 同源，前后端都需修）。
- **修复**：移除 URL 中的 `?token=`，仅用 header。

---

## 4. P2 详述（要点）

- **P2-1 scan 未接入上传**：`scan.py` 仅被 tests 引用；`upload.py` 做魔数/扩展名校验但**从不调 `scan_bytes`** → 压缩炸弹/高熵 fail-closed 在真实路径是死代码。修：在 `upload_resume` 调 `await scan_bytes(data)`，不安全则 403。
- **P2-2 sanitize 未调用**：`sanitize.py` 仅被 tests 引用，无 router/llm.py 调用 → LLM 文本消毒不生效。**注意**：不要对用户发给 LLM 的输入做 sanitize（会破坏提示词），应在 LLM 输出落库/序列化边界做。
- **P2-3 token 进 result_url**：`upload.py:91` `result_url=f"/result/{task_id}?token={access_token}"`。query 落代理/历史/Referer。修：去掉 URL 中的 token（已作为 `access_token` 返回）。
- **P2-4 match 非幂等**：`matcher.py:81-92` 插入 `JobMatch` 无预清、无唯一约束（`models.py:118-128`）；`tasks.py:95-97` 每次跑新建 `ResumeParse`。ARQ at-least-once 重投递 → 重复解析+匹配行。修：加 `UniqueConstraint(resume_id, job_id)` + `ON CONFLICT DO UPDATE`，或插入前清理；加 pending/running 去重守卫。
- **P2-5 work_history 漏进嵌入**：`matcher.py:34-35` 嵌入 `work_history`，而 `interview_gen.py:47` 明确把 `work_history`/`projects` 当 PII 排除。修：嵌入载荷仅留 skills/summary/education/title。
- **P2-6 Worker 崩溃任务卡死**：`tasks.py:79` 置 `running`，崩溃无 reaper（ARQ `keep_result=3600` 后驱逐）。修：超时/轮询 reaper 把陈旧 `running`→`failed`。
- **P2-7 迁移缺扩展**：初始迁移无 `CREATE EXTENSION IF NOT EXISTS vector` → 非 pgvector 镜像（RDS/CloudSQL/Supabase）报 "type vector does not exist"。修：在 `upgrade()` 加。
- **P2-8 CI 门禁缺口**：`mypy app || true` 永远过；无 `alembic upgrade head --sql`/check；无 E2E。修：mypy 真正门禁 + 迁移离线校验。
- **P2-9 eval 基线过弱**：黄金集仅 4 条、规则向、无阈值。修：扩到 ≥20 含负例/边界，加 LLM 输出断言与最低通过阈值。
- **P2-10 迁移依赖宿主挂载**：Dockerfile 故意不 COPY migrations，compose bind-mount 宿主 `./apps/api/migrations`。无源码树的主机 → alembic 缺失 → api 重启循环。修：把 `alembic.ini migrations` COPY 进镜像，dev 才用 bind-mount 覆盖。
- **P2-11 restore 无 trap**：`restore_db.sh` `set -e` 在 `pg_restore` 失败即退出，未 `rm -rf` 临时目录 → 含 PII 的明文 dump 留 `/tmp`。修：加 `trap 'rm -rf "${RESTORE_TMP}"' EXIT`。
- **P2-12 reduced-motion 未覆盖 JS 滚动**：`mock-interview/.../page.tsx:104` `scrollTo({behavior:"smooth"})` 未受 `prefers-reduced-motion` 门控（全局 CSS 守卫只管 CSS 动画）。修：门控到 `matchMedia('(prefers-reduced-motion: reduce)')`。
- **P2-13 SSE role=log 刷屏**：`mock-interview/.../page.tsx:249-256` 流式每 token 改变 live region 文本 → 读屏器每 token 重读整段。修：流式期间关 `aria-live`，仅用独立 visually-hidden status 区播报。
- **P2-14 system 主题不实时**：`ThemeToggle.tsx:7-13` `apply()` 仅读一次 `matchMedia`，无 `change` 监听。修：theme==='system' 时 `addEventListener('change', ...)`。
- **P2-15 配额按 token 多重性放缩**：`main.py:88`/`quota.py:80-85` 每令牌独立窗口，多令牌=多窗口；`request.client` 为 None 时退化为共享 `ip:unknown` 桶。修：全局上限 + per-IP 下限，仅信任已知代理的 `X-Forwarded-For`。

---

## 5. P3 汇总（不阻塞，建议择机）

- 安全：dev `db.py:25 echo=True` 记 SQL（含文件名/可能 PII，dev 可接受）；`/auth/logout` 不吊销无状态 JWT（7 天窗口，可接受）；`auth.py:102` 审计 `resource_id` 记完整 UUID（并入 P0-1 修复，仅记前缀）。
- 核心：`matcher.py:64` 嵌入维度无断言（建议 `assert len(emb)==settings.LLM_EMBED_DIM`）；`interview_coach.py:33 _SESSION_LOCKS` 无界且绑定首触 loop（uvicorn --reload 可能 `RuntimeError`，改 TTL/弱引用或行内 `asyncio.Lock`）；`stream_session_reply` 文档夸大异常安全（仅 catch `LlmUnavailableError`，流前 DB 加载未保护）。
- 前端：`mock-interview` 流式 updater 用陈旧 index 可能 `undefined` throw（改用稳定 id）；`login` `aria-invalid` 误标两字段；`UploadDropzone` 无客户端大小/类型前置校验；`result` 页用 `confirm()/alert()`（无障碍差）；聊天列表 `key={i}` 无 memo（每 token 全量重渲染）；装饰性低对比文本；ThemeToggle SSR 初始闪烁；`api.ts:173` `catch{}` 吞掉不可解析帧；SSE/poll 无 `AbortController`（离开页面 reader 仍跑）。
- 部署/运维：dev compose 硬编码 `postgres:postgres`（仅 dev）；Prometheus 扫不存在的 `/metrics`（冗余）；`compose.observ.yml` 重定义 `api` 且 `ENVIRONMENT:development`、无 DB，独立起会坏；`PROJECT_PLAN.md` 写 `infra/docker/Dockerfile*` 与实际 `apps/api`、`apps/web` 不符（文档漂移）；`backup_db.sh:31` 默认 iter 与 `restore_db.sh:63` 硬编码不一致；web Dockerfile 用 `npm` 非 `pnpm` 且 prod 运行时重建（慢/非独立）；`data/jobs/onet/snapshot.json` 不在仓库（需 `ingest.py`+O*NET 下载，新部署无种子直至手动生成）。

---

## 6. 已核实稳健项（Verified OK 亮点）

- **ARQ-only**：全代码零 `BackgroundTasks`（仅依赖/注释）。✅
- **并发信号量=6 / 降级状态机**：`llm.py` 信号量、`3 次失败→冷却探测+退避` 均到位。✅
- **流式成本计入**：`stream_options.include_usage=True` + usage chunk 跟踪，护栏能计流token。✅
- **嵌入维度一致 1536**：config→`Vector(1536)`→HNSW 索引一致。✅
- **匹配/结构化降级**：LLM 不可用时规则兜底仍返回结果；结构化捕获 JSON/校验异常回退。✅
- **JWT 算法/过期校验**：`jwt.decode(..., algorithms=["HS256"])` 拒绝 none/验签/exp。✅
- **argon2**：`verify_password` 错配/坏 hash 返回 False。✅
- **审计脱敏端到端**：`_redact_subject`/`_redact_detail` 剥离 token/email→`***REDACTED***`，路由一律传 `anonymized_subject`/哈希前缀。✅
- **安全响应头**：CSP 默认 Report-Only、HSTS 仅 prod、nosniff、X-Frame-Options DENY；中间件顺序 CORS 内/安全头外不互覆盖。✅
- **配额 fail-open 范围正确**：仅 Redis 真宕机切 Noop，误配但活仍 enforce。✅
- **可观测不消费 SSE body**：仅读写 header（X-Trace-ID/traceparent）。✅
- **匿名 403**：`hmac.compare_digest` 常量时间比对。✅
- **无硬编码密钥**：`DEEPSEEK_API_KEY`/`MINIO_SECRET_KEY` 均来自 env，无日志。✅
- **CORS**：显式 `CORS_ORIGINS`，无 `*` 通配。✅
- **前端 XSS**：无 `dangerouslySetInnerHTML` 接不可信数据；LLM 输出经 React 转义。✅
- **令牌不落存储**：从不进 localStorage/sessionStorage；收藏仅存 task_id+job_title。✅
- **SSE 半开流处理**：缺 `data:` 行、无终态事件的半开流抛 503 清 spinner，不卡死。✅
- **Dockerfile 存在**：`apps/api`、`apps/web` 均有，compose 所有 `build:` 上下文解析到真实文件（"缺失 Dockerfile"前提不成立）。✅
- **无密钥入库**：仅 `.env.example` 占位，`.gitignore` 忽略 `*.env`。✅
- **O*NET 署名/zh_overlay 分离**：CC BY 4.0 署名在 README+数据头，overlay 自身 IP 正确分离。✅
- **迁移 env.py 异步→同步驱动重写正确**；`depends_on: service_healthy` 排序合理。✅
- **backup_db.sh**：自定义格式 + aes-256-cbc + pbkdf2，删明文、强制口令、保留 N 份。✅

---

## 7. 建议修复顺序（供决策）

**Phase A — 部署阻塞（P0 + 4 个 prod 功能/CI 失效 P1）**
1. P0-1 JWT 生产密钥 fail-loud
2. P1-2 prod 卷遮蔽岗位数据（否则上线即匹配零结果）
3. P1-4 CI web 门禁修复（否则门禁形同虚设）
4. P1-5 Grafana 匿名 Admin 收敛
5. P1-3 rollback digest 标签修复

**Phase B — 安全/成本正确性（剩余 P1）**
6. P1-1 observability 二次 call_next（双倍计费/重复写）
7. P1-6 单份成本熔断落地
8. P1-7 配额覆盖 Worker
9. P1-8 + P2-3 前后端令牌出 query

**Phase C — 数据/健壮性 P2**
10. P2-4 match 幂等 + P2-6 任务 reaper
11. P2-5 work_history 出嵌入
12. P2-1 scan 接入、P2-2 sanitize 接入
13. P2-7 迁移 CREATE EXTENSION、P2-10 镜像内置迁移、P2-11 restore trap
14. P2-8 CI mypy/迁移校验、P2-9 eval 扩量

**Phase D — 前端 a11y/体验 P2/P3**
15. P2-12/13/14 reduced-motion、SSE live region、system 实时
16. 其余 P3 加固

> 注：本报告为**审查产出**，未改动任何代码。修复建议用独立 agent 分域并行实施（与本次审查同构，再次规避自审）。
