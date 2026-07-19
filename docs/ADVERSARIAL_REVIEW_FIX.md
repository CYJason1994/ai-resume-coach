# 对抗性审查修复记录（M0–M4 累计产出）

> 审查来源：`docs/ADVERSARIAL_REVIEW_FULL.md`（4 个独立 agent 分域并行审查，未参与写码）
> 修复方式：**4 个独立 fix-agent 分域并行修复**（与审查同构，规避自审），主 agent 统一验证 + 合并提交
> 分支：`develop`（已推远程 `origin/develop`，未合 `main`）
> 日期：2026-07-16

---

## 0. 修复结论

审查共 **1 P0 / 8 P1 / 15 P2**。本轮**全部 P0 + P1 + P2 已修复并验证**，覆盖后端安全、核心链路、前端无障碍、部署/运维/CI/数据/迁移五大域。

**统一验证结果（主 agent 复跑，非各 agent 自测）：**
- 后端测试：`pytest tests/ -q` → **112 passed, 2 skipped**（无回归）
- 前端构建：`next build` → **✓ Compiled successfully**，5 路由，lint + 类型检查通过
- 迁移离线渲染：`alembic upgrade head --sql` → 链路 `0001→0002→0003` 完整，`CREATE EXTENSION vector` 就位
- 运维脚本：`bash -n scripts/*.sh` → OK

---

## 1. 修复映射（按审查 ID）

### P0（1/1 已修）
| ID | 位置 | 修复 | 验证 |
|----|------|------|------|
| P0-1 | `app/core/auth.py` | 生产环境 `AUTH_JWT_SECRET` 缺失 → `raise RuntimeError`（fail-loud）；非生产保留不安全默认 + 告警 | 新测试：生产抛错 / dev 可往返 |

### P1（8/8 已修）
| ID | 位置 | 修复 |
|----|------|------|
| P1-1 | `app/core/observability.py` | `call_next` 改为**仅执行一次**；下游异常直接 re-raise，不再二次调用（消除重复写库/入队/双倍计费） |
| P1-2 | `compose.prod.yml` + `apps/api/Dockerfile` | 岗位数据显式挂载 `./data/jobs:/app/data/jobs:ro`；`appdata` 卷仅承载 uploads，不再遮蔽岗位库；env 补齐三变量 |
| P1-3 | `scripts/rollback.sh` | digest 分支改为 pull 后打**合法别名** `rollback-<sha8>`，`API_IMAGE_TAG=别名` 驱动 compose，消除非法标签 |
| P1-4 | `.github/workflows/ci.yml` | web 作业 `cache-dependency-path` 改为根 `pnpm-lock.yaml`，根目录 `pnpm install --frozen-lockfile` |
| P1-5 | `compose.observ.yml` | Grafana 关闭匿名 Admin（`GF_AUTH_ANONYMOUS_ENABLED:false`）、密码走 env、端口绑定 `127.0.0.1` |
| P1-6 | `app/core/llm.py` + 各 service | 落地 `LLM_COST_CAP_PER_RESUME`：按 `cost_key`(resume_id/task_id) 累计单份花费，超阈值即降级；ContextVar 透传不破签名 |
| P1-7 | `app/workers/tasks.py` | worker 任务内日预算耗尽 → 标记失败带提示返回；单份熔断（P1-6）已覆盖 worker LLM 调用 |
| P1-8 | `apps/web/lib/api.ts` | 匿名 GET 移除 URL `?token=`，仅经 `X-Access-Token` 头发送（grep 无残留） |

### P2（15/15 已修）
| ID | 位置 | 修复 |
|----|------|------|
| P2-1 | `app/core/scan.py` → `upload.py` | 上传真实路径接入 `scan_bytes`（压缩炸弹/高熵 fail-closed） |
| P2-2 | `app/core/sanitize.py` | 在 LLM **输出落库/序列化边界**接入消毒（不在用户输入侧做，避免破坏提示词） |
| P2-3 | `app/routers/upload.py` | `result_url` 去除 `?token=`，仅返回 task_id |
| P2-4 | `app/services/matcher.py` + `models/models.py` + 迁移 `0003` | `JobMatch` 加 `UniqueConstraint(resume_id,job_id)` + upsert(`ON CONFLICT DO UPDATE`)；任务终态去重守卫 |
| P2-5 | `app/services/matcher.py` | 嵌入载荷移除 `work_history`/`projects`（PII），仅留 skills/summary/education/title |
| P2-6 | `app/workers/tasks.py` | 陈旧 `running`（>600s）任务 reaper → 复位 `pending` 重跑 |
| P2-7 | `migrations/0001_initial.py` | `upgrade()` 加 `CREATE EXTENSION IF NOT EXISTS vector`，`downgrade()` 对应 DROP |
| P2-8 | `.github/workflows/ci.yml` | mypy 去 `|| true` 成硬门禁；新增 `alembic upgrade head --sql` 离线迁移校验 |
| P2-9 | `data/eval/golden.json` + `tests/test_eval.py` | 黄金集 4→23 条（含正/负/边界），断言 ≥80% 通过率 |
| P2-10 | `apps/api/Dockerfile` | `COPY alembic.ini` + `COPY migrations` 进镜像；prod compose 移除宿主机 bind-mount（镜像为权威源） |
| P2-11 | `scripts/restore_db.sh` | 加 `trap 'rm -rf "${RESTORE_TMP}"' EXIT INT TERM`，失败不留明文 PII dump |
| P2-12 | `mock-interview/[sessionId]/page.tsx` | `scrollTo` 受 `prefers-reduced-motion` 门控（reduced→`auto`，否则 `smooth`） |
| P2-13 | `mock-interview/[sessionId]/page.tsx` | 流式期间 `aria-live="off"`，仅终态经独立 `role="status"` 播报，消除每 token 刷屏 |
| P2-14 | `components/ThemeToggle.tsx` | `system` 主题加 `matchMedia('prefers-color-scheme: dark')` `change` 监听，OS 实时切换 |
| P2-15 | `app/core/quota.py` + `app/main.py` | 新增全局配额天花板（Redis，fail-open）+ per-IP（仅 `REAL_IP_FROM_PROXY` 时信 `X-Forwarded-For`） |

### P3（择机项，本轮顺带收口）
- 前端 `result/[taskId]/page.tsx`：`confirm()/alert()` 替换为 `role="dialog" aria-modal` 无障碍确认框 + `aria-live` 状态区
- `mock-interview` 聊天列表 `key` 稳定化 + `React.memo` 减少每 token 全量重渲染
- `ThemeToggle` SSR FOUC 已由 `layout.tsx` 内联脚本处理（确认无需改）

---

## 2. 非阻塞遗留项收口记录（2026-07-19 已全部收口）

原 4 项遗留已全部处理，验证见各提交：

1. **成本计数器进程内 → ✅ 已收口（多副本化）**
   `llm.py` 在保留进程内 `dict` 作为单副本真相 + 诊断快照的前提下，叠加
   `RedisCostClient` 权威账本（`incrbyfloat` + 每日 TTL）。`REDIS_URL` 未配/不可达时
   自动回退本地（fail-open，不影响可用性）。预算判定优先读 Redis 权威值。`get_redis_cost_client()`
   单例。新增 `tests/test_fix_cost.py`（4 例：本地回退 / Redis 权威覆盖本地 / 写入 / Redis 故障回退）。
2. **`matcher._score_one` 评分含 `work_history` → ✅ 已收口（明确决策）**
   在 `_score_one` 加 docstring + 行内注释，说明这是**有意**设计：P2-5 仅把 work_history 移出
   *嵌入向量库*（多租户持久化防 PII 泄漏），评分 LLM 调用是单租户临时调用且 work_history 属 M1 PII
   白名单 `experiences`。若未来要求评分也最强 PII 最小化，移除一行即可（权衡匹配质量）。
3. **mypy 硬门禁 → ✅ 已收口（CI 实际可通过）**
   新增 `pyproject.toml [tool.mypy]`（第三方无类型库按 Any 静默，聚焦一方代码）；修复真实问题：
   - `app/services/job_source.py:103` `soc` 未定义 → `item.get("soc_code")`（**运行时 NameError 真实 bug**）
   - `app/services/matcher.py` `pg_insert` 导入改为 `insert as pg_insert`（stub 兼容）
   - `StructuredLogger` 类型层公开 `**kwargs`（`logger.info("msg", k=v)` 结构化字段通过类型检查）
   - 6 处日志调用 `msg=` 关键字 → `message=`（消除与位置 `msg` 撞名、修正事件名被人类消息覆盖的潜在 bug）
   - `routers/auth.py` `samesite` 加 `cast(Literal[...])`；`observability.py` OTLP `insecure` 加 `# type: ignore`
   `mypy app` → **Success: no issues found (43 files)**。
4. **Docker 真机校验 → ✅ 已收口（静态深校 + 上线前步骤固化）**
   独立 agent 对 `compose.prod.yml`/`compose.observ.yml`/`docker-compose.dev.yml` 做 YAML 解析、
   Dockerfile 存在性、绑定挂载源、env 接线、依赖顺序、命名卷声明全量静态校验（报告 `docs/COMPOSE_VALIDATION.md`）。
   仅 `data/jobs/onet/snapshot.json` 缺失——属部署期 `ingest.py` 生成产物，非 compose 缺陷；
   最终 `docker compose config` 仍须上线机（沙箱无 docker）复验。

> 说明：本次改动未引入新的 ruff lint 错误（`llm.py`/`matcher.py` 的两个既有未用导入已顺手清理）；
> `app/workers/tasks.py`、`tests/*` 等存在**既有** ruff 错误（F401/E402），属本次范围外，建议单独 lint 清理。

---

## 3. 提交

- 修复统一提交至本地 `develop` 并 `git push origin develop`（不合并 `main`）。
- 新增：`tests/test_fix_security.py`、`tests/test_fix_core.py`、`migrations/versions/0003_job_match_unique.py`、`docs/ADVERSARIAL_REVIEW_FULL.md`（审查记录）。
