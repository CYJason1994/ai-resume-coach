# M0 对抗性审查 + 代码 Review

> 审查时间：2026-07-12 22:32（develop 分支，已推送远程）
> 审查范围：M0 全部后端（29 个 .py）+ 前端（13 个 ts/tsx/css/json）+ 基础设施（docker-compose / Dockerfile / CI）
> 方法：全量源码通读 + 关键假设运行时复现（建隔离 venv 装 sqlalchemy/pgvector 实测）
> 结论：**M0 当前无法启动，存在 3 个 P0 阻断 + 5 个 P1 严重缺陷，必须修复后才能进 M1。**

---

## 🔴 P0 — 阻断级（M0 完全不可用）

### P0-1　`models.py` 的 `list` 字段缺类型声明 → 应用无法 import（已运行时复现）

**位置**：`apps/api/app/models/models.py`
- `Job.required_skills` / `Job.required_skills_zh` / `JobMatch.matched_skills` / `JobMatch.missing_skills` 均为
  `Mapped[list[str]] = mapped_column(default=list)`，**没有声明 SQLAlchemy 列类型**。

**复现**（隔离 venv，SQLAlchemy 2.x）：
```python
class T(Base):
    __tablename__ = "t"
    id: Mapped[int] = mapped_column(primary_key=True)
    skills: Mapped[list[str]] = mapped_column(default=list)
# sqlalchemy.orm.exc.MappedAnnotationError:
#   Could not locate SQLAlchemy Core type when resolving for Python type
#   indicated by 'list[str]' inside the Mapped[] annotation for the 'skills' attribute
```

**影响**：错误发生在**类定义阶段（模块 import 时）**，不是运行时。`app.main` 导入链触发 `import app.models.models` → 抛 `MappedAnnotationError` → **uvicorn 启动失败、`TestClient(app)` 构造失败、所有端点不可用**。`init_db()` 的 `try/except` 救不了（错误在 import 阶段，不在 `init_db` 内）。

**修复**：声明类型（PgSQL 用 JSONB 最合适，保留结构且可索引）：
```python
from sqlalchemy.dialects.postgresql import JSONB
required_skills: Mapped[list[str]] = mapped_column(JSONB, default=list)
required_skills_zh: Mapped[list[str] | None] = mapped_column(JSONB, default=None)
# JobMatch.matched_skills / missing_skills 同理
```

---

### P0-2　`routers/jobs.py` 违反 v0.3「ARQ 锁死」决策，且种子摄入实际不执行

**位置**：`apps/api/app/routers/jobs.py`
```python
from fastapi import BackgroundTasks   # ❌ v0.3 已锁死「禁用 BackgroundTasks，仅 ARQ」
...
def _run():
    result = provider.seed()          # ❌ 协程未 await，seed() 永不执行
background.add_task(_run)
```
1. **违反架构决策**：v0.3 明确「ARQ 锁死，禁用 BackgroundTasks」。此处又引入 `BackgroundTasks`，且函数内注释自相矛盾（"生产建议改为 ARQ" / "M0 先同步+后台二选一"）。
2. **逻辑 bug**：`_run` 是同步函数，`provider.seed()` 是 `async` 协程，未 `await` → 只创建协程对象**从不执行**。`POST /jobs/seed` 返回「已启动」，但后台什么都不做 → **岗位库永远空 → M1 匹配无数据**。
3. 即便补 `await`，`BackgroundTasks` 在进程内、重启丢失、阻塞事件循环，仍违背 ARQ 决策。

**修复**：迁移到 ARQ：
```python
from app.workers.tasks import enqueue_seed_jobs
@router.post("/seed")
async def seed_jobs():
    await enqueue_seed_jobs()   # ARQ 任务内 await provider.seed()
    return SeedResponse(...)
```
（`enqueue_seed_jobs` 在 `workers/tasks.py` 加 `redis.enqueue_job("seed_jobs_task")`；`worker.py` 的 `functions` 注册。）

---

### P0-3　`docker-compose.dev.yml` 前端代理指向自身（容器网络错误）

**位置**：`docker-compose.dev.yml` 第 89 行
```yaml
web:
  environment:
    NEXT_PUBLIC_API_URL: http://localhost:3001   # ❌ 容器内 localhost = web 自己
```
- web 容器跑的是 `npm run dev`（见 `apps/web/Dockerfile` CMD），dev 模式下 `next.config.mjs` 的 `rewrites()` 在**请求时**读 `process.env.NEXT_PUBLIC_API_URL`。
- compose 的 `environment` 会**覆盖** Dockerfile 里正确的 `ENV NEXT_PUBLIC_API_URL=http://api:3001`（见 web Dockerfile 第 4 行）。
- 后果：容器内前端把 `/api/*` 代理到 `localhost:3001`（web 容器自身，无此服务）→ **前端永远连不上后端**。

**修复**：compose web 改为
```yaml
NEXT_PUBLIC_API_URL: http://api:3001
```
（或直接删掉该 environment 覆盖，让 Dockerfile 的 `api:3001` 生效。注意：prod `next build` 模式该变量在 build 期内联，需另行处理，M0 先保证 dev 可用。）

---

## 🟠 P1 — 严重级（功能/安全/数据正确性）

### P1-1　LLM 降级状态机形同虚设（R2-C3 核心未实现）

**位置**：`app/core/llm.py`
```python
async def chat(self, ...):
    if self.degradation.degraded or not self._budget_ok():
        self.degradation.try_recover()   # ❌ 每次入口都立即清零降级
        raise LlmUnavailableError(...)
async def embed(self, ...):
    if self.degradation.degraded:
        self.degradation.try_recover()   # ❌ 同上
        # 随后既没 raise 也没真正处理，继续往下发请求
```
`try_recover()` 在 `degraded` 时把 `_degraded_until` 和 `_failures` **立即清零** → 阈值触发后**第一次调用就退出降级**，cooldown 窗口完全没起作用。R2-C3 要求的「连续失败→降级+冷却探测」未实现。

**修复**：降级期间直接 reject（不 try_recover），冷却结束后（且需一次成功探活）才退出降级：
```python
async def chat(self, ...):
    if not self._budget_ok():
        raise LlmUnavailableError("预算耗尽")
    if self.degradation.degraded:
        raise LlmUnavailableError("LLM 降级冷却中，使用规则兜底")
    ... # 正常路径
# 仅在 health_check 成功且已出冷却窗口时调用 note_success / 显式 recover
```

### P1-2　O*NET 默认源无快照 → 静默回退 curated 但 license 标错（合规风险）

**位置**：`app/services/job_source.py`
```python
class OnetProvider(JobSourceProvider):
    source_code = "onet"
    source_license = "CC BY 4.0"
    async def _load_raw(self):
        if os.path.exists(snap): return json.load(...)
        return await JsonProvider()._load_raw()   # ❌ 回退到 curated(proprietary)
```
M0 默认 `JOB_SOURCE=onet`，但 `data/jobs/onet/snapshot.json` **不存在**（只有 `ingest.py` 脚本，需手动跑）。于是 `OnetProvider` 静默回退加载 curated 数据，却仍以 `source_code="onet"`、`source_license="CC BY 4.0"` 写入 `jobs` 行 → **数据来源与许可证标注错配**，违反 v0.3 的版权合规原则。

**修复**：snapshot 缺失时要么（a）显式报错并提示运行 `ingest.py`；要么（b）按实际来源回退时同步设置 `source_code="curated"`、`source_license="proprietary"`。M0 建议给一个最小 `snapshot.json`（或把 curated 作为默认 `JOB_SOURCE` 起名字）避免误导。

### P1-3　`init_db()` 静默吞掉建表异常

**位置**：`app/core/db.py`
```python
except Exception as e:  # noqa
    print(f"[init_db] 跳过（数据库暂不可达）: {e}")
```
若建表真的失败（pgvector 扩展未装、列类型错误、连接问题），仅 `print` 到 stdout → 应用「成功」启动但**无表**，请求时才 500，且错误极易被淹没。开发期容错可接受，但应走 `logger.error` 并标记 readiness degraded。

**修复**：用 `logger.error("init_db_failed", error=str(e))`；M1 改 Alembic + 启动期失败（生产不应静默）。

### P1-4　ARQ Worker 进程不建表

**位置**：`app/workers/worker.py` + `docker-compose.dev.yml`
- `init_db()` 只在 FastAPI lifespan 调用；**arq worker 是独立进程，不运行 FastAPI lifespan → 不建表**。
- compose 里 `worker.depends_on` 只依赖 `postgres`/`redis`，**不依赖 `api`**，二者可能竞争启动；若 worker 先处理任务，表不存在 → 任务失败。

**修复**：worker 启动时调用 `init_db()`（arq `on_startup` 钩子），或 compose 让 `worker` depends_on `api` 健康。

### P1-5　`/ready` 在 Redis/LLM 失败时仍返回 200

**位置**：`app/routers/health.py`
```python
ok = all(v == "ok" for v in checks.values() if v != "degraded") and checks["database"] == "ok"
status = 200 if checks["database"] == "ok" else 503
```
只要 DB 正常就返回 200，即便 Redis 挂了（ARQ 无法入队/处理）或 LLM 不可用。K8s 用 `/ready` 做就绪探针会误判收流量。

**修复**：Redis fail → 503；LLM degraded 可保留 200 但 `status` 标 `degraded`。

---

## 🟡 P2 — 中等级（健壮性/体验/规范）

| # | 位置 | 问题 | 建议 |
|---|------|------|------|
| P2-1 | `web/lib/api.ts` `getTask` | token 同时走 `?token=` 和 header；query token 进 URL（历史/代理/referer 可能泄露） | 生产仅用 `X-Access-Token` header，弃用 query |
| P2-2 | `job_source.py` `seed()` | async 内同步 `open()` 读 JSON，阻塞事件循环（snapshot 大时明显） | `asyncio.to_thread(open, ...)` 或 aiofiles |
| P2-3 | `job_source.py` `seed()` | embedding 匹配 `id_map` + 嵌套 loop O(n²) 且脆弱 | `for job, vec in zip(rows, vecs)` |
| P2-4 | `health.py` `/ready` | 每次 `import redis.asyncio` 并新建连接不关闭 → 连接泄漏 | 连接池单例 |
| P2-5 | `upload.py` 第 38 行 | 路由内再次 `bind_request_id()` 覆盖 contextvar → 路由日志 request_id 与响应头 `X-Request-ID` 不一致 | 删除路由内调用，仅依赖中间件 |
| P2-6 | `web/UploadDropzone.tsx` | 轮询 60×2s=120s；M1 LLM 解析可能超时 | M1 调大或改 SSE |
| P2-7 | `web/ThemeToggle.tsx` | 「跟随系统」未监听 `matchMedia('(prefers-color-scheme: dark)').addEventListener('change')` | 加 change 监听实时更新 |
| P2-8 | `web/lib/api.ts` `request()` | GET 也带 `Content-Type: application/json`（冗余） | 有 body 才加 |
| P2-9 | `schemas.py` / `errors.py` | `ResumeSummary`、`AppError.ValidationError` 定义未被使用（死代码） | 删除或接好 |
| P2-10 | `api/Dockerfile` | `pip install ".[dev]"` 把 ruff/mypy/pytest 装进生产镜像 | 分离 runtime/dev 依赖 |

---

## ✅ 做得好的地方（应保留）

- 配置 fail-fast + 模型 ID 配置化（避开 deepseek 弃用坑）；CORS 显式来源、禁 `*`。
- 匿名 token：`token_urlsafe(32)` + SHA-256 存储 + `hmac.compare_digest` 常量时间比对 + 不进日志。
- 结构化 JSON 日志 + 请求 ID 中间件；类型化错误层级 + 全局处理器。
- ARQ Worker 框架、`enqueue_process_resume` 与 upload 调用签名一致、arq task `(ctx, resume_id)` 约定正确。
- O*NET(CC BY 4.0) 版权合规思路、可配置岗位源 Provider 抽象、中文 overlay 设计。
- 前端主题无闪烁（inline script + suppressHydrationWarning）、玻璃拟态基线、Tailwind `brand` 色已正确定义。

---

## 修复优先级（建议 M0.1 收口）

1. **P0-1** models list→JSONB（改 4 处）→ 后端才能起
2. **P0-2** jobs seed 迁 ARQ + 补 await
3. **P0-3** compose web `NEXT_PUBLIC_API_URL=http://api:3001`
4. **P1-1** LLM 降级状态机真正冷却
5. **P1-2** O*NET 快照缺失时来源/license 一致
6. **P1-3/4/5** init_db 日志化 + worker 建表 + /ready Redis 判障
7. P2 逐项清理（含死代码、连接泄漏）

> 修复后需补一份 `tests/test_models_import.py`（import app 不抛错）与 `test_seed_actually_runs`（ARQ seed 后 jobs 表有行），作为回归护栏。
