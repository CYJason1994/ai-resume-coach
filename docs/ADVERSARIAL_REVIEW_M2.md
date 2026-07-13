# 对抗性审查报告 — M2 面试题目生成

> 审查日期：2026-07-12 ｜ 审查者：Senior Developer（高级开发工程师）
> 对象：`develop` 上 M2 提交（`5fcef90`、`99d00e2`） + `docs/M2_IMPLEMENTATION.md`
> 方法：逐行读码 + 运行时复验（pytest 全量 + `import app.main`） + 前端类型核对
> 结论：**M2 主体可用，但存在 2 个 P1（1 个数据串味、1 个生产构建失败）+ 5 个 P2 + 2 个 P3，建议进 M3 前修复。**

---

## 0. 先说结论（TL;DR）

M2 的**合规级联擦除、白名单主体、JSON 容错、规则兜底、幂等写入**都做对了，质量基线比 M0/M1 好。但对抗审查抓到两个会"上线即翻车"的 P1：

1. **P1-A 跨岗位题目串味**：`GET /interviews/{task_id}` 只按 `resume_id` 过滤题目，**完全忽略生成任务的 `job_id`**。同一份简历对多个岗位生成面试题时，所有岗位的题目混在一起返回，且 `job_title`/`job_id` 取首行（顺序任意）→ 用户看到"岗位 A"的页面里混着"岗位 B"的题目，导出 Markdown 也被污染。根因：`Task` 模型没有 `job_id` 列，worker 收到 job_id 却没持久化，GET 无从过滤。
2. **P1-B 前端生产构建失败**：`InterviewList` 接口**漏了 `error_text` 字段**，但 `interview/[taskId]/page.tsx` 两处访问 `d.error_text`/`data.error_text`。`tsconfig.json` 是 `strict: true` 且无 `ignoreBuildErrors` → `next build` 类型检查直接报 TS2339 **挂掉**。`next dev` 不做类型检查，所以本地"跑得动"掩盖了它。

---

## 1. P1 级（上线即故障）

### P1-A 跨岗位面试题目串味（数据完整性 / 正确性问题）
**定位**
- `apps/api/app/routers/interviews.py:71-81`：`select(InterviewQuestion).where(InterviewQuestion.resume_id == resume.id)` —— 只过滤 `resume_id`，**没有 `job_id` 过滤，也没关联生成任务的 job**。
- `apps/api/app/models/models.py:58-70`：`Task` 模型只有 `resume_id/type/status/...`，**没有 `job_id` 列**。worker `generate_interview_task`（`tasks.py:140-203`）收到 `job_id` 作为入参，但**从未写回 `Task` 行**，故 GET 无法据 task 还原 job。
- 写入侧 `generate_questions`（`interview_gen.py:148-153`）按 `(resume_id, job_id)` 清旧+插新 —— 即题目在存储里是**按岗位隔离**的，但读取侧把它当"整份简历"读出来，读写不对称。

**影响（真实用户场景）**
- 场景一：简历 R 对岗位 A、岗位 B 各生成一次。打开"A 的面试题"页，GET 返回 R 的全部题目（A+B 混排），`job_title` 取 `rows[0].job_title`（任意），`job_id` 取 `rows[0].job_id` → **展示岗位与内容错位**。
- 场景二：先对 B 生成完成（有题目），再对 A 点"生成"（此时 task 处于 pending/running）。GET 不判 `status`，**在生成窗口内立刻把 B 的旧题当成 A 的"已就绪"结果展示**，用户以为 A 已出题。
- 场景三：复制/下载 Markdown 时，混入其他岗位题目，导出物失真。

**修复方案（二选一，推荐方案一）**
- 方案一（最干净）：`InterviewQuestion` 增加 `task_id` 列（FK→tasks.id，可空兼容旧行），`generate_questions` 写入时填 `task_id`；GET 改为 `WHERE task_id == :tid`。重生成创建新 task → 旧题随 `delete(resume,job)` 清除，新页只显示自己 task 的题目。彻底消除串味，且语义最贴合"这个任务产出的题目"。
- 方案二（改动更小）：`Task` 增加可空 `job_id` 列，POST generate 与 worker 写入时填值；GET 改为 `WHERE resume_id==r AND job_id==task.job_id`。副作用：同一 job 的旧 task 页会显示新 task 产出的题目（按 job 过滤而非 task），不串味但略失真，可接受。

> 注：无论哪种，`generate_questions` 现有的 `(resume_id, job_id)` 幂等清旧逻辑保留即可。

---

### P1-B 前端 `InterviewList` 缺 `error_text` → 生产构建失败（build-breaking）
**定位**
- `apps/web/lib/api.ts:151-159`：`InterviewList` 接口字段为 `task_id/resume_id/job_id/job_title/status/degraded/questions` —— **无 `error_text`**。
- 但 `apps/web/app/interview/[taskId]/page.tsx`：
  - `:75` `setError(d.error_text || "面试题生成失败")`
  - `:185` `{data.error_text || "AI 生成暂不可用…"}`
  两处访问了接口未声明的 `error_text`。
- `apps/web/tsconfig.json:7` `"strict": true`；`next.config.mjs` 无 `typescript.ignoreBuildErrors`（M1 审查时已读，仅 `rewrites`）。

**影响**
- `next dev` 不执行类型检查 → 本地开发"正常"，极易漏过。
- `next build`（CI/生产/部署）会跑 `tsc` 类型检查 → **TS2339 属性不存在 → 构建直接失败，无法出包**。这与"生产级"定位直接冲突。

**修复**：`api.ts` 的 `InterviewList` 补 `error_text: string | null;`（后端 `InterviewListResponse` 本就有该字段，纯属前端契约漏写）。建议同时全局 grep 前端对 `error_text` 的引用，确认无其他遗漏字段。

---

## 2. P2 级（质量 / 合规 / 文档）

### P2-A 合规白名单被突破：work_history / projects 上送 LLM 可能含组织 PII
- `interview_gen.py:49-50` payload 含 `work_history`、`projects`。
- `extractor.py:83` 会从 LLM 结构化结果抽取 `work_history`/`projects`（内容可能含**公司名、项目名**）。
- `docs/M2_IMPLEMENTATION.md` §4 明写"**绝不**带上送 PII"，但 M1 的白名单原则（§7.3）只含 `skills/experience_years/education/summary`。M2 擅自扩到 work_history/projects，**实际扩大了上送面**，组织名属可识别信息，与承诺不一致。
- 修复：面试 payload 仅保留 `skills/experience_years/education/summary`（与匹配一致）；若确需 work_history/projects，先剥离公司名再上送，并在文档如实说明白名单范围。

### P2-B GET 路由零测试覆盖（与 M1 P1-1 同类漏测）
- grep `apps/api/tests/` 无任何 `interviews` 路由测试；8 个测试只覆盖 `interview_gen` **服务层**。
- 结果：上面的 P1-A 跨岗位 bug 完全在测试盲区，未被任何用例捕捉。
- 修复：补一组需 DB 的集成测试 —— `POST /generate`（token 正确/错误、job 不存在）→ `GET /{task_id}`（按 job 隔离、多岗位不串味、degraded 标记、pending 不返回旧题）。这正是 M1 审查强调的"覆盖真实数据流"缺口。

### P2-C 文档数字与实现不符（M1 P2-5 同类"文档≠现实"）
- `docs/M2_IMPLEMENTATION.md` §5 写："**9 例**"、"pytest 目标 **24 passed, 2 skipped**（M1 的 21 + M2 新增 3 规则/解析 + … 含 **6 个 async** 生成测试）"。
- 实际 `tests/test_interview_gen.py` 仅 **8 个测试函数**（解析 4 + 规则 1 + 生成 3；async 仅 3 个，非 6 个）。
- M2 提交记录实测 **29 passed, 2 skipped**（21 + 8），与文档"24"不符。
- 修复：文档数字对齐真实运行结果（建议文档注明"以 CI 实测为准"）。

### P2-D degraded 状态靠 error_text 子串推断（脆弱）
- `interviews.py:84`：`degraded = bool(task.error_text and "规则模板" in (task.error_text or ""))`。
- 属 stringly-typed 反模式：一旦降级提示文案变更（国际化/措辞调整），推断即失效，前端"规则模板兜底"提示会无声消失。
- 修复：`Task`（或 `InterviewQuestion`）增加显式 `degraded: bool` 列，worker 写入时置位，GET 直读，不再解析字符串。

### P2-E token 明文落盘 localStorage（放大 M1 P2-3 暴露面）
- `interview/[taskId]/page.tsx:126` 收藏时把 `token` 明文存入 `localStorage`；`api.ts` 的 `Fav` 类型含 `token`。
- M1 P2-3 已指出 token 走 URL query 暴露；M2 又把它**持久化到本机存储**，XSS 或共用设备即可重放访问/删除该简历。
- 修复：收藏仅存 `task_id + 展示信息`（job_title 等），重开收藏时要求重新粘贴 token；或在 M3 做服务端收藏。至少文档标注此风险为已知项。

---

## 3. P3 级（健壮性 / 细节）

### P3-A 降级仅覆盖 `LlmUnavailableError` 单一路径
- `interview_gen.py:166`：`except (LlmUnavailableError, ValueError)`。若 `get_llm().chat` 抛**非** `LlmUnavailableError` 的异常（如 DeepSeek 5xx、超时），不会触发规则兜底，任务直接 `failed`。
- 与 M1 P1-3"无 Key 可演示"哲学略有出入：目前**只有无 Key/401 才兜底**，5xx 不兜底。
- 修复：把 chat 的网络/超时类异常也纳入兜底分类，或对降级做更宽口径。

### P3-B 同 (resume,job) 并发重生成无接口级幂等锁
- 结果页按钮有 `disabled` 防抖（前端），但 API 层无去重/乐观锁。理论上两次极快 POST 会建两个 task，两个 worker 同时 `delete+insert` 同 `(resume,job)` 可能竞态丢题（低概率）。
- 可接受但应明确：要么接口级去重（同 resume+job 进行中则复用/拒绝），要么文档声明"前端已防，后端不保证并发"。

---

## 4. 做得好的地方（对抗审查也需肯定）

- ✅ `purge_resume_personal_data` 已级联硬删 `interview_questions`，PIPL 删除权在 M2 闭环。
- ✅ 白名单主体正确（skills/experience/education/summary），结构化数据不回流 PII 到落库。
- ✅ `_parse_llm_json` 容错到位：去 ```json 围栏、首尾噪声裁剪、坏 JSON 抛错触发兜底。
- ✅ `_rule_questions` 四维度全覆盖，无 Key 仍能出完整题目。
- ✅ `generate_questions` 按 `(resume_id, job_id)` 清旧+插新，幂等可重生成。
- ✅ 所有读/写路径统一 `require_access_token` + `verify_token`，token 不进日志。
- ✅ 前端导出/复制/收藏交互完整，降级有琥珀色提示条。

---

## 5. 修复优先级建议

| 优先级 | 项 | 工作量 | 阻塞 M3？ |
|---|---|---|---|
| P1-A | 跨岗位串味（加 task_id 或 Task.job_id 过滤） | 中 | 是（数据正确性） |
| P1-B | 前端补 `error_text` 字段 | 极小 | 是（构建失败） |
| P2-A | 面试 payload 剔 work_history/projects（或脱敏） | 小 | 否（合规加固） |
| P2-B | GET 路由集成测试 | 中 | 否（防回归） |
| P2-C | 文档数字对齐 | 极小 | 否 |
| P2-D | 显式 `degraded` 列 | 小 | 否 |
| P2-E | 收藏去 token 化 | 小 | 否 |
| P3-A | 拓宽降级异常口径 | 小 | 否 |
| P3-B | 并发幂等（接口级） | 小 | 否 |

**建议**：进 M3（模拟面试官）前，先把两个 P1 修掉（P1-B 一行搞定，P1-A 需改模型+查询），并顺手补 P2-A / P2-C / P2-D。P2-B 测试与 P2-E 可与 M3 一并处理。

---

*附：运行时复验说明 —— pytest 全量（29 passed, 2 skipped）与 `import app.main/worker/models` 通过，证实 M2 服务层与导入无误；P1-A/P1-B 为**逻辑/类型层**缺陷，单测覆盖不到，需靠集成测试与构建流水线捕获（本报告已指出缺口）。*
