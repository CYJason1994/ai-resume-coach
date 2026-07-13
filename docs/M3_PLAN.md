# M3 实施计划 — 模拟面试官（SSE 流式 + 上下文管理 + 评分）

> 版本：v0.3 延续 ｜ 日期：2026-07-13 ｜ 角色：Senior Developer（高级开发工程师）
> 分支：`develop`（基于 M2Fix `affb2dc` + 遗留修复 `2ea0eed`）
> 范围：**对话式模拟面试官**：SSE 流式追问 + 上下文窗口管理 + 逐轮评分 + 整体评估（不含 M4 鉴权/BFF）

---

## 1. 目标 / 场景 / 成功标准（§7.5 / §8）

- **目标**：用户在「简历 + 匹配岗位（可叠加 M2 生成题）」基础上，与 AI 模拟面试官实时对话，获得分维度追问与**每轮结构化评分/改进建议**，结束时获得**整体评估**。
- **场景**：结果页 / 面试题页 → 「开始模拟面试」→ 对话界面（流式输出、逐轮评分卡片、明暗主题）→ 结束 → 整体评分报告（复制 / 下载）。
- **成功标准**：
  1. SSE 稳定流式输出（token 级），前端无卡顿；
  2. 上下文窗口管理生效，长对话不溢出 / 不膨胀成本；
  3. 每轮有结构化评分（分数 / 亮点 / 改进点），结束有整体评估；
  4. 无 DeepSeek Key 时**清晰降级**（明确提示，不白屏不崩）；
  5. 读路径统一 token 门控（R2-C2），token 不进日志；
  6. 全链路单测覆盖（含无 Key 降级路径）。

---

## 2. 架构与数据流

```
前端对话页
  │ POST /api/interview-sessions {resume_id, job_id, interview_task_id?, dimension_focus?, mode}
  ▼ 建 session（token 门控）→ 返回 session_id
  │ POST /api/interview-sessions/{id}/message {message}
  ▼ StreamingResponse(text/event-stream)
       ├─ 校验 session 归属（resume token）
       ├─ 追加 user 消息 → build_context（滚动窗口 + 周期摘要）
       ├─ get_llm().stream_chat(...)  ──► SSE event: token（逐片）
       ├─ score_answer(...) 非流式 json_object ──► SSE event: feedback（评分）
       ├─ 持久化 transcript + feedback
       └─ SSE event: done
  │ POST /api/interview-sessions/{id}/finish
  ▼ LLM 整体评估 → 持久化 overall_score → 返回报告
  │ GET /api/interview-sessions/{id}（token 门控）→  transcript + 评分
```

---

## 3. 关键改动清单（影响范围）

### 3.1 `app/core/llm.py` — 新增流式接口（向后兼容）
- `async def stream_chat(messages, *, temperature, response_format=None) -> AsyncIterator[str]`
  - DeepSeek `stream:true`；`httpx` 流式读取 `data:` 行，yield `choices[0].delta.content`；遇 `[DONE]` 结束。
  - **复用** 信号量(6) + 预算护栏 + 降级状态机（失败 `note_failure` → `LlmUnavailableError`）。
  - 现有 `chat()` 不动。

### 3.2 `app/services/interview_coach.py`（新增）
- `build_context(session) -> list[dict]`：系统提示（面试官人设 + 岗位 + 简历摘要 + 维度聚焦 + 评分规则）+ 周期摘要 `summary_text` + 滚动窗口（最近 `MAX_WINDOW_TURNS` 轮）。**上下文管理核心**。
- `stream_reply(...)`：包装 `stream_chat`，yield token。
- `score_answer(user_msg, coach_reply, ctx) -> dict`：非流式 `json_object` 调用，返回 `{score:0-100, strengths:[], improvements:[], dimension}`。
- `summarize(transcript)`：每 `SUMMARY_EVERY` 轮调用一次，压缩为 `summary_text`，丢弃旧消息（保留窗口）。
- `overall_evaluate(transcript)`：结束时整体评分 + 综述。

### 3.3 `app/models/models.py` — 新增 `InterviewSession`
- `id, resume_id(FK), job_id, interview_task_id(NULLABLE), mode, status, transcript(JSONB), summary_text(Text), overall_score(JSONB NULLABLE), created_at, updated_at`。
- 合规：删除/留存 TTL 级联擦除（扩展 `compliance.purge_resume_personal_data` 删 `interview_sessions`）。

### 3.4 `app/routers/interview_sessions.py`（新增）+ `main.py`
- `POST /api/interview-sessions`（token 门控，建 session）
- `POST /api/interview-sessions/{id}/message`（token 门控 + SSE 流式）
- `POST /api/interview-sessions/{id}/finish`
- `GET /api/interview-sessions/{id}`（token 门控）
- `main.py` 注册 `interview_sessions.router`。

### 3.5 `app/schemas/schemas.py` — 新增
- `SessionCreateRequest / SessionMessageRequest / FeedbackItem / SessionOverall / SessionView`。

### 3.6 前端
- `lib/api.ts`：`InterviewSession / ChatMessage / FeedbackItem / SessionOverall` 类型 + `createSession / postSessionMessage(读流) / getSession / finishSession`。
- `app/mock-interview/[sessionId]/page.tsx`（新增）：开始表单（选岗位 + 可选 M2 题集 + 维度聚焦）→ 流式对话 UI（玻璃拟态、磁吸、明暗主题）→ 逐轮评分卡 → 结束整体报告（复制/下载）。复用现有 token 处理与主题系统。

---

## 4. 关键设计决策（≥2 方案比较，已标注推荐）

### 决策 A — 会话持久化方式
| 方案 | 说明 | 取舍 |
|---|---|---|
| **A（推荐）DB-backed** | `interview_sessions` 表存 transcript(JSONB)，token 门控，刷新不丢、可回看、合规可擦除，对齐 §5 数据模型 | 代码稍多，但符合"生产级"+PIPL 删除权 |
| B 纯内存 | 会话状态存 FastAPI 进程内存，随连接/刷新丢失，无回看 | 简单，但不持久、多副本不可靠、不合 PIPL 删除权 |

### 决策 B — 评分粒度
| 方案 | 说明 | 取舍 |
|---|---|---|
| **B（推荐）逐轮 + 整体** | 每轮给分数/亮点/改进点（实时价值高），结束给整体评估 | 每轮多一次结构化 LLM 调用（成本可控，有护栏） |
| B2 仅整体 | 仅结束时评分 | 省调用，但失去"模拟面试"即时反馈的核心价值 |

### 决策 C（默认，不另询）— 与 M2 题集关系
- 支持可选 `interview_task_id` 将 M2 生成题作为"访谈脚本"种子；同时支持 freeform（仅岗位+维度聚焦）。两者复用同一 SSE 通道。

### 决策 D（默认）— SSE 传输
- 采用 **POST + StreamingResponse**（`/message` 带 body），token 走 `X-Access-Token` header；比 GET+query 更适合长消息且契合现有依赖。

---

## 5. 降级与韧性（沿用 M1/M2 哲学）
- 无 Key / 降级冷却中：`stream_chat` 抛 `LlmUnavailableError` → SSE 首事件即 `error`（"AI 面试官暂不可用，请稍后再试"），前端明确提示，不崩。
- 复用 `get_llm()` 信号量/预算/降级全链路。

---

## 6. 验证（Verification）
- **单元（无 DB）**：
  - `LlmProvider.stream_chat` 用 `FakeLLM.stream_chat` 验证逐片 yield + 错误转 `LlmUnavailableError`；
  - `build_context` 滚动窗口截断 + 周期摘要触发；
  - `score_answer` JSON 解析容错；
  - SSE 端点用 `TestClient` 流式读取，断言 `token`/`feedback`/`done` 事件顺序；
  - 无 Key 路径返回 `error` 事件。
- **全量回归**：目标 **31（现有）+ M3 新增** passed / 2 skipped；确认 M0/M1/M2 链路无回退。
- **手动联调**：无 DeepSeek Key 环境下仅能验证降级路径；真实流式需 Key（记录在案）。

---

## 7. 可能破坏 / 注意
- 新增表 `interview_sessions`：dev 经 `Base.metadata.create_all`（init_db）；**生产需补 migration**（标注 TODO，M4 前处理）。
- `LlmProvider` 纯增量（新增方法，不改 `chat`）。
- 新增 router 前缀 `/api/interview-sessions`，与现有 `/api/interviews` 不冲突。
- 前端仅新增页面 + `api.ts` 扩展，不动既有页面。

---

## 8. 下一步
1. 待确认决策 A（持久化）与决策 B（评分粒度）。
2. 确认后实施：`llm.stream_chat` → `interview_coach` → `models` → `routers` → `schemas` → 前端 → 单测 → 全量回归 → 提交 develop（不推远程）。
3. 交付后由你做「M3 对抗性审查 → 修复」闭环。
