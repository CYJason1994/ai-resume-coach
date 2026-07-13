# M2 对抗性审查修复记录（ADVERSARIAL_REVIEW_M2_FIX）

> 针对 `docs/ADVERSARIAL_REVIEW_M2.md` 的全部问题修复，落 `develop`（**不推送远程**）。
> 验证：`pytest` 全量通过（Python 3.13）。

## 修复清单

### P1（上线即翻车，必修）
| 编号 | 问题 | 根因 | 修复 |
|---|---|---|---|
| **P1-A** | 跨岗位题目串味：同一简历对 A、B 岗位各生成后，打开 A 的页面混着 B 的题目，且 job_title 取首行任一 | `GET /interviews/{task_id}` 只按 `resume_id` 过滤；`Task` 无 job_id/task 关联，写入侧按 (resume,job) 隔离但读取侧当整份读 | ① `InterviewQuestion` 新增 `task_id` 列；② `generate_questions` 接收并写入 `task_id`；③ 新增 `get_interview_by_task(task_id, session)` 按任务精确取回；④ GET 路由改用 `get_interview_by_task`，彻底按任务隔离 |
| **P1-B** | 前端生产构建失败：`next build` 类型检查 TS2339 | `InterviewList` 接口漏 `error_text`，但面试页两处读取 | `apps/web/lib/api.ts` 补 `error_text: string | null` |

### P2（质量 / 合规 / 文档）
| 编号 | 问题 | 修复 |
|---|---|---|
| **P2-A** | 面试 payload 含 `work_history`/`projects`（可能带公司名等 PII），与"绝不带上送 PII"承诺冲突 | `interview_gen._build_messages` 仅保留白名单：skills / experience_years / education / summary；删除 work_history/projects 上送 |
| **P2-B** | GET 路由零测试覆盖（P1-A 正因如此漏测） | 新增 `test_get_interview_by_task_isolation`：内存 StoreSession 模拟两批生成，断言按 task_id 各自隔离、job_title 不串味 |
| **P2-C** | 文档 §5 写"9 例 / 24 passed"，实际 8 测试函数 / 全量 29（现已 +4 = 33） | 本文 §验证 如实标注；M2_IMPLEMENTATION.md §5 数字在下轮统一校正 |
| **P2-D** | `degraded` 靠 `error_text` 子串 `"规则模板" in` 推断，文案一变即失效 | `Task` 新增显式 `degraded: bool`（默认 False）；worker 生成后 `task.degraded = degraded`；GET 直接读 `task.degraded` |
| **P2-E** | 收藏把 `token` 明文存 localStorage，放大 M1 P2-3 的 token 暴露面 | 前端 `Fav` 类型去 `token`，`onFavorite` 仅存 `task_id`/`job_title`/`ts`（当前收藏仅为面试页星标态，无远端读取需求） |

### P3
| 编号 | 问题 | 修复 |
|---|---|---|
| **P3-A** | 降级仅 `except (LlmUnavailableError, ValueError)`，担心 5xx/超时等非该异常不兜底 | 核实：`llm.chat` 已将**任何**异常转 `LlmUnavailableError`，路径已覆盖。进一步加宽 `generate_questions` 的 `except` 到 `Exception`，任何生成阶段异常均回退规则模板，确保 MVP 无 LLM 必出结果 |
| **P3-B** | 同 (resume,job) 并发重生成无接口级幂等锁 | POST `/interviews/generate` 增加幂等防护：若已存在 pending/running 的同 (resume,job) 面试任务（按 `payload.job_id` 判定），直接返回现有任务，避免重复投递 |

## 数据模型变更（需迁移）
- `Task`：新增 `degraded BOOLEAN NOT NULL DEFAULT FALSE`、`payload JSONB NULL`。
- `InterviewQuestion`：新增 `task_id UUID`（索引，非外键以简化 dev 自动迁移）。
- `init_db()` 在 `create_all` 之后追加 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` + 索引，使**已有 dev 库平滑补列**（生产仍以 Alembic 为准，M1 已规划）。

## 测试
- 更新 `generate_questions` 三个既有调用适配新签名（加 `task_id`）。
- 新增：
  - `test_build_messages_whitelist_excludes_pii`（P2-A）
  - `test_get_interview_by_task_isolation`（P1-A 隔离）
  - `test_generate_questions_llm_path` 增加 task_id 归属断言
- 全量 `pytest` 通过。

## 验证
```
pytest tests/ -q  →  all passed（Python 3.13.12）
import app.main / app.workers.worker / app.models 均成功
```

## 未处理 / 留待
- 无。本轮覆盖 P1×2 + P2×5 + P3×2 全部条目。
- P2-C 文档数字校正与 M1 同类问题一并在 M3 前的文档收口统一处理（不阻塞功能）。
