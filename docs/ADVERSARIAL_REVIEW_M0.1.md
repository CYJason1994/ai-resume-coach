# M0.1 收口记录（M0 对抗性审查修复 + 验证中新增的 P0）

> 关联：`docs/ADVERSARIAL_REVIEW_M0.md`
> 分支：`develop`（M0.1 修复全部提交到 develop，未推远程）
> 日期：2026-07-12

## 一、原审查 P0/P1 修复状态

| 项 | 描述 | 状态 | 修复方式 |
|----|------|------|----------|
| P0-1 | `models.py` list 字段缺类型 → `MappedAnnotationError` 致 app 无法 import | ✅ 已修+复现验证 | `mapped_column(JSONB, default=list)`（4 处） |
| P0-2 | `jobs.py` 用 `BackgroundTasks` 违反 ARQ 锁死；`provider.seed()` 未 `await` | ✅ 已修 | `/jobs/seed` 改为 `enqueue_seed_jobs()`（ARQ）；新增 `seed_jobs_task` 并注册到 `WorkerSettings`；`scripts/seed_jobs.py` 仍作同步兜底 |
| P0-3 | compose web `NEXT_PUBLIC_API_URL=localhost:3001` → 容器内代理打到自身 | ✅ 已修 | 改为 `http://api:3001`（`next dev` 运行时读 env，覆盖生效） |
| P1-LLM | 降级状态机 `try_recover` 入口即清零降级，冷却形同虚设 | ✅ 已修 | 重写 `DegradationState`：`degraded` 期间 fail-fast；`maybe_recover(probe)` 仅在冷却到期节流探测，连续成功才 `note_success` 退出 |
| P1-license | O*NET 无 snapshot 静默回退 curated 却标 `CC BY 4.0` | ✅ 已修 | `OnetProvider` 追踪 `_used_snapshot`，`effective_source_code`/`effective_license` 实际回退时改标 `curated`/`proprietary` |
| P1-init_db | `init_db` 用 `print` 静默吞异常；worker 不建表；`/ready` Redis 挂仍 200 | ✅ 已修 | `db.py` 改用 `logger.warning` 且生产 fail-fast；`worker.py` 加 `on_startup` 调 `init_db`；`/ready` 要求 DB+Redis 均 ok 才返 200 |

## 二、验证过程中新增的两个 P0（静态审查无法发现，运行必崩）

> 这恰说明：对抗性审查 + 运行时验证必须成对出现。以下两个缺陷若不实测 import/启动，永远不会暴露。

### P0-4 结构化日志 kwargs 运行时崩溃（地基级）
- **现象**：`test_health` 实测抛 `TypeError: Logger._log() got an unexpected keyword argument 'error'`。
- **根因**：全 M0 的日志写法为 `logger.info("msg", key=value)`（结构化字段），但 `get_logger` 返回的是**裸 `logging.Logger`**，标准库 `_log` 不接受任意 kwargs。
- **影响**：任何一次打结构化日志的调用路径（如 `init_db` 失败告警、`upload_accepted`、seed 进度）都会直接抛异常——等于"第一次写日志就崩"。原审查漏掉是因为纯读代码不会触发执行。
- **修复**（集中、调用点零改动）：`logging.py` 新增 `StructuredLogger(logging.Logger)` 子类，重载 `_log` 将 kwargs 注入 `extra["struct"]`；`JsonFormatter.format` 读取并展开 `struct`；模块导入时 `logging.setLoggerClass(StructuredLogger)`。

### P0-5 arq 0.28 API 断裂（生产级 P0）
- **现象**：`from arq import WorkerSettings` → `ImportError: cannot import name 'WorkerSettings'`。
- **根因**：pyproject 写 `arq>=0.25`，Docker 会自动装到最新 **0.28**，该版本已移除顶层 `WorkerSettings`，改为 `Worker(functions=..., redis_settings=..., on_startup=...).run()` + `run_worker(settings_cls)`。
- **影响**：worker 容器 `arq app.workers.worker.WorkerSettings` 启动即失败 → 全部异步任务（解析/匹配/seed）无法执行，**生产环境直接瘫痪**。
- **修复**：`worker.py` 改用 `Worker(functions=[...], redis_settings=..., on_startup=on_startup).run()`；compose worker `command` 改为 `python -m app.workers.worker`；pyproject 下限提到 `arq>=0.28`，明确新 API 约束。

## 三、验证结果

- **语法**：`python -m compileall apps/api` → 0 错误。
- **决定性复现（P0-1）**：隔离 venv 装 sqlalchemy/pgvector/pydantic 等，`import app.models.models` → `Job.required_skills 类型: JSONB`，不再抛 `MappedAnnotationError`。
- **测试**：`pytest tests/` → **6 passed, 1 skipped**
  - `test_app_imports`：验证 app 全模块可 import（覆盖 P0-1 + P0-5 修复）
  - `test_health`：验证 `/health` 200（覆盖 P0-4 日志不再崩）
  - `test_config` / `test_security`：原回归用例仍通过
  - `test_seed_writes_rows`：集成测试，需可达 PostgreSQL（CI 配 services:postgres）；本地无 DB **自动 skip**

## 四、遗留（P2，非阻塞，记入下一轮）
- token 经 `?token=` query 传输有泄露风险（改 header 或短时一次性）
- seed 摄入在主事件循环同步 `await` 大模型嵌入，长文本可能阻塞 worker（M1 改流水线批处理）
- `embedding` 匹配当前 O(n²) 赋值，岗位量大时需改批量映射
- `/ready` 每次重建 Redis 连接（连接泄漏），应复用连接池
- 轮询 120s 超时；ThemeToggle 未监听系统主题变更；少量死代码

## 五、结论
M0 从"无法启动"变为"可启动 + 核心链路 import/health 通过"，并已配齐回归测试。
可进入 **M1**（解析/结构化 + O*NET 摄入 + 向量匹配 + 合规/限流/成本护栏 + eval）。
