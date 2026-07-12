# M2 实现说明 — 面试题目生成（M2，§7.5）

> 版本：v0.3 延续 ｜ 日期：2026-07-12 ｜ 角色：Senior Developer（高级开发工程师）
> 分支：`develop`（基于 M1 修复 `9f5056c`）
> 范围：**岗位选择 → 分维度面试题生成 + 展示 / 导出 / 收藏**（不含 M3 的对话式模拟面试官）

---

## 1. 目标与边界

- 用户在结果页从「匹配岗位」中选择一个岗位 → 触发生成分维度面试题。
- 维度：**behavioral（行为）/ technical（技术）/ role（岗位匹配）/ stress（压力）**。
- 展示：按维度分组、带「考察点 / 难度」标签；支持 **复制 Markdown / 下载 .md / 收藏（localStorage）**。
- 降级：无 DeepSeek Key 或 LLM 不可用时，回退**规则模板**（基于岗位必备技能 + 简历技能），保证 MVP 可演示（与 M1 P1-3 哲学一致）。

**不在本期**：M3 的流式模拟面试官、上下文管理、会话持久化。

---

## 2. 架构与数据流

```
结果页(选岗位) ──POST /api/interviews/generate{resume_id,job_id}──► 建 Task(interview) + 投 ARQ
                                                                      │
                                                               generate_interview_task
                                                                      │ 加载最新 ResumeParse + Job
                                                                      ▼
                                                        interview_gen.generate_questions
                                   ┌──────────────────────────────┴─────────────────────────────┐
                           LLM 可用（白名单上送+json_object）                          LLM 不可用/解析失败
                                   │                                                          │
                           _parse_llm_json → 过滤非法维度                          _rule_questions（模板）
                                   │                                                          │
                                   └──────────► 幂等清空旧题 → 持久化 InterviewQuestion ←───┘
                                                  │
                                           Task.status=done（degraded? 写 error_text 备注）
                                                  │
                  GET /api/interviews/{task_id}（token 校验）◄── 前端轮询 ──┘
```

---

## 3. 关键改动清单

### 3.1 数据模型 `app/models/models.py`
- 新增 `InterviewQuestion` 表：
  `id, resume_id(FK), job_id(FK), job_title(快照), dimension, question, expected_focus, difficulty, order_index, created_at`。

### 3.2 合规 `app/services/compliance.py`
- `purge_resume_personal_data` 增加硬删 `interview_questions`（PIPL 删除权覆盖面试题，返回计数含 `interview_questions_deleted`）。
- `delete_resume` 与 `cleanup_expired`（TTL）均经此路径，自动级联。

### 3.3 生成服务 `app/services/interview_gen.py`（新增）
- `_build_messages`：**白名单 payload**——仅 `skills/experience_years/education/summary/work_history/projects` + 岗位信息，**不带上送 PII**。
- `get_llm().chat(..., response_format={"type":"json_object"})` 调 DeepSeek。
- `_parse_llm_json`：容错解析（去 ```json 围栏、裁剪首尾噪声、缺 `questions` 抛错触发兜底）。
- 仅保留合法 `dimension`，过滤模型乱填。
- `_rule_questions`：行为 3 / 技术 2~3 / 岗位 2~3 / 压力 3，基于 `job.required_skills` + `structured.skills` 注入具体技能名。
- `generate_questions(resume_id, structured, job, session) -> (rows, degraded)`：**先 `delete` 该 (resume_id,job_id) 旧题再写**，幂等可重生成；兼容无 Key 演示。

### 3.4 Worker `app/workers/tasks.py` + `worker.py`
- 新增 `enqueue_generate_interview(task_id, resume_id, job_id)` 与 `generate_interview_task`。
- 校验 resume/job/解析存在；缺失给出明确 `error_text` 并标记 `failed`。
- 降级时 `status=done` 且 `error_text="AI 生成暂不可用，已使用规则模板兜底"`（前端作为信息提示，非错误）。
- 注册进 `Worker(functions=[...])`。

### 3.5 路由 `app/routers/interviews.py`（新增）+ `main.py`
- `POST /api/interviews/generate`：校验 token（比对 resume）+ 岗位存在 → 建 Task + 投 ARQ → 返回 `InterviewListResponse(pending)`。
- `GET /api/interviews/{task_id}`：校验 token → 返回题目（按 `order_index`）+ `status` + `degraded` + `error_text`。
- `main.py` 注册 `interviews.router`。

### 3.6 Schema `app/schemas/schemas.py`
- 新增 `InterviewGenerateRequest / InterviewQuestionItem / InterviewListResponse`（含 `degraded`、`error_text`）。

### 3.7 前端
- `lib/api.ts`：类型 `InterviewQuestion/InterviewList` + `generateInterview/getInterview`。
- `app/result/[taskId]/page.tsx`：每个匹配卡片加「生成针对性面试题 →」按钮（带生成中态），跳转 `/interview/{task_id}?token=...`。
- `app/interview/[taskId]/page.tsx`（新增）：轮询 → 分维度展示（编号卡片 + 考察点/难度标签）→ **复制 Markdown / 下载 .md / 收藏(localStorage)**；`degraded` 时琥珀色信息条。

---

## 4. 安全 / 合规 / 韧性

- 所有读/写路径统一 `require_access_token` + `verify_token`（R2-C2），token 不进日志。
- 上送 LLM 严格白名单（§7.3），面试题不回流任何 PII。
- 生成经 `get_llm()`：并发信号量(6) + 预算护栏 + 降级冷却（§7.8）全部复用。
- 删除/留存 TTL 级联擦除面试题（PIPL）。

---

## 5. 验证

- 单元/规则测试 `tests/test_interview_gen.py`（9 例）：
  - 解析容错（plain / fenced / truncated / invalid→raise）
  - 规则兜底四维度覆盖
  - LLM 路径：persist 4 条（bogus 维度被过滤）、degraded=False
  - 无 Key（boom）→ 规则兜底 degraded=True
  - 坏 JSON → 同样兜底
- 全量：`pytest` 目标 **24 passed, 2 skipped**（M1 的 21 + M2 新增 3 规则/解析 + ...；含 6 个 async 生成测试）。
- `import app.main` / `worker` / `models` 通过（routes 注册无误）。

> 注：「2 skipped」为需 Postgres 的集成测试（seed / 向量匹配），非 M2 阻塞。

---

## 6. 已知限制 / 留待 M3+

- 收藏仅存 localStorage（本机），无跨设备/服务端收藏管理页（如需可后续加）。
- 模拟面试官（SSE 流式对话、上下文窗口管理、评分建议）属 M3，本期未做。
- 岗位源摄入仍受 M1 P2-5 限制（日预算为进程内内存，未迁 Redis）。
