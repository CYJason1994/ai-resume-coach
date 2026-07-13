# 对抗性审查报告 — M3 模拟面试官（SSE 流式 + 上下文管理 + 逐轮/整体评分）

> 审查日期：2026-07-13 ｜ 审查者：Senior Developer（高级开发工程师）
> 对象：`develop` 上 M3 提交（`7632e16`）+ `docs/M3_IMPLEMENTATION.md` + 前端 `mock-interview/[sessionId]` 等
> 方法：逐行读码 + 运行时复验（pytest 全量 + `import app.main` + `next build`）+ 前端类型/数据流核对
> 结论：**M3 主体可用，架构（滚动窗口/周期摘要/降级不落库/匿名 token 全链路）做对了；但存在 2 个 P1（流式成本未计入预算、流式生成器无终态兜底）+ 4 个 P2 + 6 个 P3，建议修复后再进 M4。**

---

## 0. 先说结论（TL;DR）

M3 的**合规级联擦除、匿名 token 全链路（SSE 也走 `X-Access-Token` header）、降级不落库、滚动窗口/周期摘要防溢出、逐轮评分归属用户本轮**都做对了，质量基线比 M2 好。但对抗审查抓到两个会"上线即失控"的 P1：

1. **P1-1 流式成本未计入预算**：`stream_chat` 没请求 `stream_options.include_usage`，DeepSeek 默认**不在流式末片回传 usage** → `_track_cost` 对流式回复**从不扣减** → 整个 M3 面试功能的日预算护栏**形同虚设**，可被无限轮次刷爆成本。
2. **P1-2 流式生成器无终态兜底**：`stream_session_reply` 只 `except LlmUnavailableError`，其余任何异常（如 `db.commit()` 失败、`_sse` 的 `json.dumps` 异常）在已 yield token 后抛出 → SSE 中途断裂。而全局 `Exception` 处理器对**半开流无效**（响应头已发），客户端拿到残缺回复且**收不到 error 事件**，前端只能靠 fetch 底层报错兜底。

---

## 1. P1 级（上线即故障 / 成本失控）

### P1-1 流式 LLM 成本未计入日预算 → 护栏对 M3 失效
**定位**
- `apps/api/app/core/llm.py:166-175`：`stream_chat` 的 payload 只有 `model/messages/temperature/stream`，**没有 `stream_options`**。
- `apps/api/app/core/llm.py:196-200`：`if chunk.get("usage"): await self._track_cost(...)` —— 依赖末片带 usage。但 DeepSeek（OpenAI 兼容）**默认流式不回传 usage**，必须显式 `stream_options: {"include_usage": true}` 才在末片返回。
- 结果：流式回复的 token 数**永不被记账**，`_daily_spend` 对 M3 始终为 0 → `LLM_DAILY_BUDGET` 护栏**对模拟面试官整体失效**。

**影响**
- 用户可对同一简历发起无限轮次模拟面试，每轮都过 `_ensure_available`（预算判定仍 OK，因为从未扣减），单份熔断 `$0.01` 与日预算都拦不住 → 成本不可控。
- 与 M0/M1 反复强调的"成本护栏"直接冲突。

**修复**：`stream_chat` payload 加 `"stream_options": {"include_usage": True}`（末片 `choices:[]` + `usage` → 现有 `chunk.get("usage")` 分支自然记账；空 choices 的末片 `delta` 为空被正确跳过）。补单测断言 payload 含该字段。

---

### P1-2 流式生成器无顶层异常兜底 → 半开流断裂、客户端无 error 事件
**定位**
- `apps/api/app/services/interview_coach.py:179-230`：`stream_session_reply` 是 `async for` 生成器，仅 `except LlmUnavailableError`（:210）。成功路径末尾 `await db.commit()`（:228）及 `_sse(json.dumps(...))`（:229-230）若出现非 `LlmUnavailableError` 异常（DB 连接断开、JSON 序列化失败等），异常会**逃出生成器**。
- `apps/api/app/core/errors.py:90-96`：全局 `@app.exception_handler(Exception)` 对 `StreamingResponse` **无效**——响应头已随首个 `data:` 发出，Starlette 无法再改写为 500 JSON，只能中断连接。

**影响（真实场景）**
- 流式吐到一半时 commit 失败 → 连接被服务器中止 → 前端 `reader.read()` 抛网络错误 → 用户看到残缺半句 + 连接中断；无 `error` 事件、无友好提示，且**已渲染的助手气泡卡在半截**。
- 与服务"优雅降级"定位不符。

**修复**：生成器体包 `try/except Exception`，异常时 `yield _sse({"type":"error","value":"…"})` 后 `return`，保证**终态事件**；`finally` 释放 per-session 锁。前端 `sendSessionMessage` 在流结束但未收到 `done`/`error` 时主动 reject，页面 `onSend` 清理悬挂的空助手气泡。

---

## 2. P2 级（质量 / 健壮性 / 测试盲区）

### P2-1 路由层零集成测试（对照 M2 P2-B 同类漏测）
- `apps/api/tests/test_interview_coach.py` 5 例**全部用 FakeSession/FakeLLM**，无真实 HTTP、无真实鉴权、无 `StreamingResponse` 接线。
- 因此以下逻辑完全在测试盲区：`require_access_token`+`verify_token` 的 403/404 路径、`create_session`/`finish_session`/`get_session` 的业务、SSE 帧经真实 `StreamingResponse` 的序列化、owner 校验。
- 与 M2 P2-B 同源缺陷（只测服务层、不测路由）。
- 修复：补一组 `fastapi.testclient.TestClient` 集成测试，monkeypatch `interview_sessions.SessionLocal` 与 `interview_coach.get_llm`（`[taskId]` 页已证明此手法可用），覆盖 create 成功/缺 token 403/job 404、message 流式 + owner 错 403、finish、get。

### P2-2 `SessionMessageRequest.message` 无长度上限（滥用/成本）
- `apps/api/app/schemas/schemas.py:126-128`：`message: str`，无 `max_length`。
- 用户可发数万字符 → 存入 JSONB transcript + 原样上送 LLM（成本 + 上下文）。
- 修复：`Field(max_length=4000)`（或 2000），并在前端输入框加 `maxLength` 同值。

### P2-3 单会话无轮次上限（MAX_TURNS 缺失）
- `interview_coach.py` 有 `MAX_WINDOW_TURNS=8`（上下文窗口）与 `SUMMARY_EVERY=6`（摘要），但**没有总轮次上限** → 会话可无限增长（DB transcript 与 LLM 调用数都无界）。
- 修复：加 `MAX_TURNS`（如 30）；`stream_session_reply` 入口若 `len(transcript) >= MAX_TURNS` → `yield error` 并返回，提示"已达本轮模拟面试上限"。

### P2-4 `finish_session` 非幂等 → 重复评估双倍成本
- `apps/api/app/routers/interview_sessions.py:119-139`：`overall_evaluate` 每次都调 LLM 并 `sess.status="finished"`。
- 用户连点"结束并评估"或前端重试 → 多次 LLM 调用、多次写库，且第二次返回的是**重复生成**的整体评估（可能分数不同，体验诡异）。
- 修复：`if sess.status == "finished": return 现有整体评估`（从 `sess.overall_score` 直接组装 `SessionOverall`），不再调 LLM。

### P2-5 `stream_session_reply` 取了 `resume` 却从未使用（死代码 + 多余查询）
- `interview_coach.py:195`：`resume = await db.get(Resume, sess.resume_id)` 之后**没有任何引用**（实际只用 `job`/`parse`）。
- 每轮多一次无谓 DB 查询；且 `resume` 的"是否 None"判断缺失（虽 FK 保证存在，但死代码易误导）。
- 修复：删除该行。

### P2-6 前端流异常终止无专门处理（悬挂半截助手气泡）
- `apps/web/lib/api.ts:157-174`：`sendSessionMessage` 的 `while(true)` 在 `reader` 结束时直接退出，**不检查是否收到 `done`/`error`**。
- `apps/web/app/mock-interview/[sessionId]/page.tsx:129-133`：`onSend` 的 `catch` 仅 `setError("发送失败")`，但已 push 的空助手气泡（`assistantMsg`）残留。
- 与 P1-2 配合：后端修复后总会发终态事件，但前端仍应对"流结束却无终态"主动兜底。
- 修复：`sendSessionMessage` 记录是否见到 terminal 事件，循环结束后若无则 `throw new Error("stream_closed")`；`onSend` 在 error 事件**及**流异常时，移除末尾 content 为空的助手气泡。

---

## 3. P3 级（细节 / 纵深防御）

### P3-1 `dimension_focus` / `mode` 未校验取值集合
- `schemas.py:118-123` 为 `str | None`，可传任意串；虽 `build_context` 用 `.get(...,"综合")` 兜底、仅进 prompt 文本，但落库值不可控。
- 修复：schema 用 `Literal["behavioral","technical","role","stress","mixed"]` 与 `Literal["freeform","scripted"]`，由 Pydantic 在边界拒绝非法值。

### P3-2 `interview_task_id` 可跨简历引用他人题库
- `interview_sessions.py:72-76`：`create_session` 接收 `interview_task_id` 但**不校验它归属同一 resume** → 传其他简历的 task_id 会把别人的面试题作为脚本提示注入本会话 prompt（仅题面，非强 PII，但属信息越界）。
- 修复（轻）：`_load_script` 增加 `InterviewQuestion.resume_id == sess.resume_id` 过滤；或 `create_session` 校验 task 归属。

### P3-3 `overall_evaluate` 对空/极短 transcript 仍调 LLM
- 用户 0 轮就点"结束" → `convo=""` 仍调 LLM 生成评估（浪费 + 可能给空对话打分）。
- 修复：`if len(transcript or []) < 2: return 友好空评估（不调 LLM）`。

### P3-4 `get_session` 的 `job_title` 取值表达式可读性差
- `interview_sessions.py:154`：`job.title_zh or job.title if job else ""` 依赖 `Job.title` 非空（模型层确为非空），恒为 str，但 `if job` 冗余且易误读。
- 修复：简化为 `job.title_zh or job.title if job else ""` → 由于 `Job.title` 必填，等价于 `job.title_zh or job.title`；保持现状亦可，仅记。

### P3-5 `stream_chat` 误用 `response_format` + `stream:true`
- `llm.py:172-174`：若调用方同时传 `response_format` 与 `stream`，多数 provider 不支持两者开启。当前 M3 不传，但属隐患。
- 修复：加断言/注释明确"流式禁用 json_object"，或在 payload 组装时二选一。

### P3-6 `summary_text` 每轮作为第二个 system message 附加
- `interview_coach.py:102-105`：摘要随会话增长重复占用上下文（设计可接受，但长会话下 persona+summary 两段 system 略冗余）。低优先级，记录即可。

---

## 4. 做得好的地方（对抗审查也需肯定）

- ✅ 匿名 token 全链路：`message` SSE 走 `X-Access-Token` header（未泄漏到 URL）；`verify_token` 用 `hmac.compare_digest`（常量时间，抗时序攻击）。
- ✅ PIPL 合规：`purge_resume_personal_data` 已级联硬删 `InterviewSession`（compliance.py:18-20），删除权在 M3 闭环。
- ✅ 降级不落库：`stream_session_reply` 捕获 `LlmUnavailableError` → `error` 事件且不写助手消息（测试 `test_stream_session_reply_degraded` 覆盖），前端不白屏。
- ✅ 上下文管理到位：滚动窗口 `MAX_WINDOW_TURNS=8` + 周期摘要 `SUMMARY_EVERY=6` 防溢出；`build_context` 取 `transcript` 形参（审查期已修的"首轮漏用户消息"未复发，断言 `any("我是张三" in m...)` 守住）。
- ✅ 评分归属清晰：feedback 挂在用户本轮、降级返回空评分不阻断。
- ✅ 前端 `next build` 通过、strict 类型检查 OK，玻璃拟态/磁性按钮/逐轮评分卡/报告导出等 premium 细节到位。
- ✅ 入口页（`interview/[taskId]`、`result/[taskId]`）`createSession` 接入清晰，token 透传正确。

---

## 5. 修复优先级建议

| 优先级 | 项 | 工作量 | 阻塞 M4？ |
|---|---|---|---|
| P1-1 | stream_chat 加 `stream_options.include_usage` 修复成本记账 | 小 | 是（成本护栏） |
| P1-2 | 流式生成器顶层 try/except 保证终态 error 事件 | 小 | 是（健壮性） |
| P2-1 | 路由集成测试（auth/owner/SSE 接线） | 中 | 否（防回归） |
| P2-2 | `message` 长度上限 | 极小 | 否 |
| P2-3 | 单会话 MAX_TURNS 上限 | 小 | 否 |
| P2-4 | `finish_session` 幂等 | 极小 | 否 |
| P2-5 | 删除死代码 `resume` 查询 | 极小 | 否 |
| P2-6 | 前端流终态/异常兜底 | 小 | 否 |
| P3-1 | dimension_focus/mode 取值校验 | 小 | 否 |
| P3-2 | interview_task_id 跨简历校验 | 小 | 否 |
| P3-3 | 空 transcript 免调 LLM | 极小 | 否 |
| P3-4 | job_title 表达式简化 | 极小 | 否 |
| P3-5 | stream+response_format 互斥断言 | 极小 | 否 |
| P3-6 | summary 重复 system 段（记录） | — | 否 |

**建议**：进 M4 前先修两个 P1（均小工作量），并顺手修 P2-1~P2-6（含补齐路由测试，堵住 M2 P2-B 同源盲区）与若干 P3。

---

*附：运行时复验说明 —— pytest 全量 **36 passed, 2 skipped**（M3 5 例在列）与 `import app.main` 通过、`next build` 通过（4 路由类型检查 OK）。P1-1/P1-2 为**逻辑/护栏层**缺陷，单测覆盖不到（FakeLLM 不触发真实 stream_options/预算记账），需靠"断言 payload 含 stream_options"的单测 + 代码审查捕获（本报告已指出）。*
