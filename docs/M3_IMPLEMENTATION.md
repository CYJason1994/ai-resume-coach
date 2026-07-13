# M3 实现说明 — 模拟面试官（SSE 流式 + 上下文管理 + 评分）

> 版本：v0.3 延续 ｜ 日期：2026-07-13 ｜ 角色：Senior Developer（高级开发工程师）
> 分支：`develop`（基于 M2Fix `affb2dc` + 遗留修复 `2ea0eed` + M3 提交）
> 范围：**对话式模拟面试官**：SSE 流式追问 + 滚动窗口/周期摘要上下文管理 + 逐轮评分 + 整体评估（不含 M4 鉴权/BFF）

---

## 1. 目标与边界

- 用户在已有简历 + 匹配岗位（可选叠加 M2 生成题）基础上，与 AI 模拟面试官实时对话。
- 每轮获得**结构化评分**（分数 / 亮点 / 改进点，归属用户本轮回答）+ 结束时**整体评估**。
- 上下文窗口管理：滚动窗口（最近 8 条）+ 每 6 条周期摘要，防超上下文、控成本。
- 降级：无 DeepSeek Key / 降级冷却时，SSE 首事件即 `error`，前端清晰提示，不崩不白屏。
- **不在本期**：M4 鉴权（BFF/same-site）、多副本共享预算（仍进程内）。

---

## 2. 架构与数据流

```
结果页/面试题页 ──创建会话 POST /api/interview-sessions──► 建 InterviewSession(token 门控) → session_id
                                                              │
   POST /api/interview-sessions/{id}/message（SSE text/event-stream）
                                                              │
       ├─ 校验 session 归属（resume token）
       ├─ 追加 user 消息 → build_context（系统提示 + 摘要 + 滚动窗口）
       ├─ get_llm().stream_chat(...) ──► SSE event: token（逐片）
       ├─ _score_answer(非流式 json_object) ──► SSE event: feedback（评分）
       ├─ 持久化 transcript + 周期摘要 → SSE event: done
       └─ 异常(LlmUnavailableError) ──► SSE event: error
                                                              │
   POST /api/interview-sessions/{id}/finish ──► overall_evaluate → 持久化 overall_score
   GET  /api/interview-sessions/{id}（token 门控）→ transcript + 评分
```

---

## 3. 关键改动清单

### 3.1 `app/core/llm.py` — 流式接口（向后兼容）
- 新增 `async def stream_chat(messages, *, temperature, response_format=None) -> AsyncIterator[str]`
  - DeepSeek `stream:true`；`httpx` 流式读取 `data:` 行，yield `choices[0].delta.content`，遇 `[DONE]` 结束。
  - 复用 信号量(6) + 预算护栏 + 降级状态机（失败 `note_failure` → `LlmUnavailableError`）。
  - 现有 `chat()` 完全不动。

### 3.2 `app/services/interview_coach.py`（新增）
- `build_context`：系统提示（面试官人设 + 岗位 + 简历画像 + 维度聚焦 + 题库种子）+ 周期摘要 + 滚动窗口（`MAX_WINDOW_TURNS=8`）。
- `stream_session_reply(db, sess, user_message)`：**SSE 友好异步生成器**，逐片 yield `token` 事件；末尾非流式 `json_object` 评分 → `feedback` 事件；评分归属用户本轮回答；周期摘要；`done` 事件。异常转 `error` 事件。
- `_score_answer`：非流式结构化评分 `{score,strengths,improvements,dimension}`，失败返回空评分（不阻断）。
- `_summarize`：周期压缩摘要（防上下文溢出）。
- `overall_evaluate`：结束时整体评估 `{overall_score,summary,top_strengths,top_gaps,suggestion}`。

### 3.3 `app/models/models.py` — 新增 `InterviewSession`
- `id, resume_id(FK), job_id(FK), interview_task_id(NULLABLE, 关联 M2 题集), dimension_focus, mode, status(active|finished), transcript(JSONB), summary_text, overall_score(JSONB NULLABLE), 时间戳`。

### 3.4 `app/services/compliance.py`
- `purge_resume_personal_data` 增加硬删 `interview_sessions`（PIPL 删除权覆盖会话，返回计数含 `interview_sessions_deleted`）。

### 3.5 路由 `app/routers/interview_sessions.py`（新增）+ `main.py`
- `POST /api/interview-sessions`（建会话）
- `POST /api/interview-sessions/{id}/message`（SSE 流式；先校验归属再返回 StreamingResponse，流内独立 session 驱动）
- `POST /api/interview-sessions/{id}/finish`（整体评估）
- `GET /api/interview-sessions/{id}`（查询）
- 全部读/写路径 `require_access_token` + `verify_token`（R2-C2），token 不进日志。
- `main.py` 注册 `interview_sessions.router`。

### 3.6 Schema `app/schemas/schemas.py`
- 新增 `SessionCreateRequest / SessionMessageRequest / FeedbackItem / ChatMessage / SessionView / SessionOverall`。

### 3.7 前端
- `lib/api.ts`：类型 `FeedbackItem/ChatMessage/InterviewSession/SessionOverall` + `createSession/getSession/finishSession/sendSessionMessage`（原生 `fetch` + `ReadableStream` 读取 SSE 帧）。
- `app/mock-interview/[sessionId]/page.tsx`（新增）：对话 UI（玻璃拟态、磁吸按钮、明暗主题），逐轮评分卡，结束 → 整体报告（复制/下载 .md）。
- 入口：面试题页「开始模拟面试 →」（带 interview_task_id 种子）、结果页每岗位「模拟面试 →」。

---

## 4. 安全 / 合规 / 韧性

- 所有读/写路径统一 token 门控（R2-C2），token 不进日志。
- 复用 `get_llm()` 信号量/预算/降级全链路；无 Key → `error` 事件，前端明确降级。
- 删除/留存 TTL 级联擦除会话（PIPL）。
- 上下文滚动窗口 + 周期摘要，约束 token 上限、控成本。

---

## 5. 验证

- 单元（无 DB，FakeSession/FakeLLM）：
  - `LlmProvider.stream_chat` 用伪 httpx 客户端验证逐片 yield + 解析。
  - `build_context` 滚动窗口裁剪 + 摘要注入。
  - `stream_session_reply` 产出 token/feedback/done 事件；评分归属用户本轮；transcript 写入正确。
  - 无 LLM → `error` 事件，不落库助手消息。
  - `overall_evaluate` 返回整体评估并置 `finished`。
- 全量：`pytest` **36 passed, 2 skipped**（原 31 + M3 新增 5）。
- 前端：`next build` / `tsc` 由用户环境验证（沙箱未装 node_modules；已按 strict 模式人工审查并修正 `ChatMessage.feedback` 缺省）。

---

## 6. 已知限制 / 留待 M4+

- 会话持久化已 DB-backed（满足 M3 决策 A），但**生产迁移 Alembic 尚未补**（dev 经 `create_all`）。
- 预算护栏仍进程内（M2/M4 TODO），多副本不可见。
- 前端 SSE 在沙箱未做 `next build` 实测（依赖缺失）；类型已人工核对。
- 模拟面试官为单人对话；多轮/多面试官/语音等未做。
