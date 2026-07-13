# 对抗性审查修复记录 — M3 模拟面试官

> 日期：2026-07-13 ｜ 修复者：Senior Developer（高级开发工程师）
> 对应审查：`docs/ADVERSARIAL_REVIEW_M3.md`
> 分支：`develop` ｜ 提交策略：审查文档 / 修复 / 修复记录 三提交（不推送远程）
> 验证：**后端 pytest 44 passed, 2 skipped（无回归，基线 36 + 新增 8）** ｜ **前端 `next build` 通过（4 路由类型检查 OK）**

---

## 1. 修复总览

| 编号 | 级别 | 修复点 | 文件 | 验证方式 |
|---|---|---|---|---|
| P1-1 | P1 | `stream_chat` 加 `stream_options.include_usage`，流式成本计入日预算 | `app/core/llm.py` | 单测断言 payload 含 `stream_options` + `daily_spend` 增长 |
| P1-2 | P1 | `stream_session_reply` 顶层 `try/except Exception` 保证 `error` 终态事件 | `app/services/interview_coach.py` | 代码审查 + 路由集成测试（owner 403 / 流终态） |
| P2-1 | P2 | 路由层集成测试（鉴权/owner/SSE 接线/幂等） | `tests/test_interview_sessions.py`（新） | 7 例全绿 |
| P2-2 | P2 | `SessionMessageRequest.message` 加 `max_length=4000` + 前端 `maxLength` | `app/schemas/schemas.py` / `app/mock-interview/[sessionId]/page.tsx` | 类型检查 + 单测边界 |
| P2-3 | P2 | 单会话 `MAX_TURNS=30` 上限，入口超限 yield error | `app/services/interview_coach.py` | 逻辑审查 |
| P2-4 | P2 | `finish_session` 幂等：`status==finished and overall_score` 直接返回，不再调 LLM | `app/routers/interview_sessions.py` | 单测 `test_finish_idempotent` |
| P2-5 | P2 | 删除 `stream_session_reply` 中未使用的 `resume` 查询（死代码） | `app/services/interview_coach.py` | 逻辑审查 |
| P2-6 | P2 | 前端流终态兜底：无 `done/error` 时主动 reject；异常时清理悬挂空助手气泡 | `app/web/lib/api.ts` / `app/mock-interview/[sessionId]/page.tsx` | `next build` + 逻辑审查 |
| P3-1 | P3 | `dimension_focus`/`mode` 改为 `Literal[...]`，边界拒绝非法值 | `app/schemas/schemas.py` | 类型 + 单测 |
| P3-2 | P3 | `_load_script` 增加 `InterviewQuestion.resume_id == sess.resume_id` 过滤 | `app/services/interview_coach.py` | 逻辑审查 |
| P3-3 | P3 | `overall_evaluate` 对 `len(transcript) < 2` 短路，免调 LLM | `app/services/interview_coach.py` | 单测 `test_finish_short_transcript_no_llm` |
| P3-5 | P3 | `stream_chat` 仅在不 `stream` 时挂 `response_format`，二者互斥 | `app/core/llm.py` | 逻辑审查 |

> 未改项（记录，按需处理）：**P3-4** `job_title` 表达式虽冗余但无害、模型层 `Job.title` 非空保证恒为 str，保持现状；**P3-6** `summary_text` 重复 system 段为已知设计权衡，记录即可。

---

## 2. 关键修复细节

### 2.1 P1-1 流式成本记账（最高优先级）
`app/core/llm.py` 的 `stream_chat` payload 现包含：
```python
payload: dict = {
    "model": settings.LLM_CHAT_MODEL,
    "messages": messages,
    "temperature": temperature,
    "stream": True,
    "stream_options": {"include_usage": True},   # ← 新增：DeepSeek 默认流式不回传 usage
}
if response_format and not payload.get("stream"):  # ← P3-5：流式禁用 json_object
    payload["response_format"] = response_format
```
解析器同步加固：末片 `choices:[]` + `usage` 时 `chunk.get("choices") or [{}]` 避免 `IndexError` 误触发降级。新增单测 `test_stream_chat_tracks_usage_with_stream_options` 用 `_CaptureStreamClient` 回放含 usage 末片的流，断言 payload 含 `stream_options == {"include_usage": True}` 且 `daily_spend` 增长。

### 2.2 P1-2 流式生成器终态兜底
`stream_session_reply` 现结构：
- 入口 `MAX_TURNS` 守卫（P2-3）；
- per-session `asyncio.Lock` 串行化（防并发双发 lost update，键为 `str(sess.id)`）；
- `try` 包住 `stream_chat → _score_answer → 写回 sess.transcript → db.commit()`；任意异常转 `error` 事件并 `return`；
- `feedback`/`done` 事件在**锁外** yield，避免持锁期间阻塞流。

### 2.3 P2-4 幂等细化（相对审查建议的小改进）
审查建议 `if sess.status == "finished": return`。实际实现收紧为：
```python
if sess.status == "finished" and sess.overall_score:
    o = sess.overall_score
    return SessionOverall(...)  # 直接组装，不调 LLM
```
原因：理论上存在"已置 finished 但评估未落库"的竞态残留，仅在确有 `overall_score` 时才幂等返回，更安全。

### 2.4 测试脚手架注意点（供后续复用）
`test_interview_sessions.py` 用 `TestClient(app)` + monkeypatch `interview_sessions.SessionLocal` 与 `interview_coach.get_llm`：
- `interview_sessions` 须从 **`app.routers`** 导入（非 `app.api` —— `app.api` 下无该模块，曾因导入路径错误导致整文件收集失败）；
- `_FakeDB.add` 模拟真实 Postgres 在 `flush()` 时由 server default 填充 `id`（否则 `created` 会话 `id` 为 `None`）；
- `_RouterFakeLLM.chat` 的返回 payload **同时**含 `score`（供 `_score_answer`）与 `overall_score`（供 `overall_evaluate`），因两条真实调用路径读取的字段名不同；
- "job 不存在"用例用 `job_none=True` 哨兵（不能传 `job=None`，`_install` 默认值会覆盖成默认 job）。

---

## 3. 验证结果

### 3.1 后端（运行时）
```
pytest tests/ -q
44 passed, 2 skipped, 1 warning in 5.24s
```
- 基线 36 passed（M3 既有 5 例未回归）；
- 新增 8 例：路由集成 7（`test_interview_sessions.py`）+ 流式成本 1（`test_interview_coach.py::test_stream_chat_tracks_usage_with_stream_options`）；
- 旧测试 0 回归，证明 P1-1 parser 加固与 P1-2/P2-3/P2-5 改动未破坏既有行为。

### 3.2 前端（构建时）
```
./node_modules/.bin/next build
✓ Compiled successfully
Linting and checking validity of types ... ✓
Routes: / · /_not-found · /interview/[taskId] · /mock-interview/[sessionId] · /result/[taskId]
```
`api.ts`（`sendSessionMessage` 终态标记 + 异常 reject）与 `page.tsx`（清理悬挂气泡 + `maxLength`）类型检查通过。

---

## 4. 结论
M3 对抗审查发现的 2 个 P1 + 6 个 P2 + 6 个 P3 中，**功能性缺陷（P1-1/P1-2/P2-2~P2-6/P3-1~P3-3/P3-5）已全部修复并验证**；仅 P3-4/P3-6 属无害/设计权衡，按审查建议留记录不改动。成本护栏（P1-1）与流式健壮性（P1-2）两大"上线即失控"风险已闭环，可进行 M4。

---

*附：本记录与 `ADVERSARIAL_REVIEW_M3.md` 配套。三提交顺序 —— (1) 审查文档、(2) 修复代码+测试、(3) 本修复记录。均仅落地本地 `develop`，未推送远程（依既定协作约定）。*
