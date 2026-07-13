# 对抗性审查 M1（上传 → 解析 → 岗位匹配 核心链路）

> 审查对象：`develop` 分支 `7d5b3d4`（M1 提交）+ `docs/M1_IMPLEMENTATION.md`
> 审查方式：全量通读 M1 改动（解析/结构化/匹配/worker/路由/限流/合规/前端结果页/测试）+ 运行时复验
> 结论：**M1 链路逻辑正确、可继续推进 M2**；但发现 **3 个 P1 + 5 个 P2 + 1 个 P3**，其中 2 个 P1 是「文档声称的降级/擦除能力实际未落地」。建议先修 P1 再进 M2。

---

## 1. 验证结果（运行时复验）

环境限制说明：执行沙箱对跨命令的文件写入做了丢弃、且禁止执行项目路径下的 `python.exe`，因此采用「单命令内 `pip --target` + 原生 Windows 路径 `PYTHONPATH`」方式运行。

| 验证项 | 结果 |
|--------|------|
| `pytest tests/ -q`（Python 3.13.12） | **18 passed, 2 skipped**（与 M1 文档声明一致） |
| `import app.main` / `app.workers.worker` / `app.models.models` | 通过（P0-1/P0-5 修复生效） |
| `StructuredLogger` 在 3.13 下接受 `logger.info("msg", k=v)` | 通过（P0-4 修复生效，结构化字段正确输出 JSON） |
| `TextParser` 解析 | 通过 |
| 2 skipped 原因 | `test_seed_writes_rows`（需 asyncpg+PostgreSQL）、`test_pdf_missing_dep_or_works`（pdfminer 已装→skip） |

**复验结论**：单测与导入层健康，M0 遗留 P0 已稳固。但「18 passed」多为单测/导入/规则兜底，**未覆盖 M1 真实数据流**（见 §5 测试覆盖缺口）。

---

## 2. 已确认良好项

- ✅ 解析层与 LLM 解耦、确定性、可单测；OCR/.doc 显式 `NotImplementedError`（范围外明确报错，非静默失败）。
- ✅ 匿名 `access_token` 契约：上传签发、DB 仅存 SHA-256、读路径（`/tasks`、`/result`、`/resumes`）均 `verify_token` + 常量时间比对；删除后 hash 置空 → 后续访问 403。
- ✅ 结构化抽取仅上送白名单 `raw_text`（PIPL 最小上送）；LLM 不可用回退规则提取，链路不崩。
- ✅ 无 `DEEPSEEK_API_KEY` 时不含 `Authorization` header（M1 验证中修复的 httpx `InvalidHeader`）。
- ✅ worker 三步失败隔离；arq 0.28 `cron` 每日 04:00 TTL 清理装配成功。
- ✅ 前端结果页轮询、匹配卡片（分数/已具备/建议补充）、删除交互完整；主题切换（浅/深/跟随）就位。

---

## 3. 缺陷清单

### 🔴 P1-1｜合规删除未真正擦除结构化数据（PIPL 删除权缺口 + 前端虚假承诺）

**位置**：`app/routers/resumes.py:33-39`；`app/workers/tasks.py:141-165`（`cleanup_expired`）

**现象**：`delete_resume` 仅做了三件事——删文件、置 `deleted_at`、置 `access_token_hash=""`。但 **`resume_parses` 表的 `raw_text` / `structured_data`，以及 `job_matches` 表的所有行，均未被删除或清空**。`cleanup_expired` 同样只动 `resumes` 行。

**影响**：
1. 用户在前端点删除时，确认框写明「文件与结构化数据将被彻底移除」（`apps/web/app/result/[taskId]/page.tsx:74`）——**与实际行为不符（虚假承诺）**。
2. 原始文本与结构化画像在库中长期留存（直到永不到来的「TTL 清理」也只清 resumes 行），PIPL「删除权/被遗忘权」未真正履行。
3. 孤儿 `job_matches` / `resume_parses` 永久堆积。

**修复**：删除时级联软删（或硬删）`ResumeParse` 与 `JobMatch`（按 `resume_id`）；`cleanup_expired` 对过期简历同样 purge 其 parses/matches。前端确认文案改为「文件与可访问结果将移除」。

---

### 🔴 P1-2｜`result_url` 路径与真实路由不一致（前端展示坏链 + API 消费者 404）

**位置**：`app/routers/upload.py:82` 返回 `result_url = f"/r/{task_id}?token={access_token}"`；真实路由为 `apps/web/app/result/[taskId]/page.tsx`（即 `/result/{taskId}`）。

**现象**：前端 `UploadDropzone` 实际跳转到 `/result/...`（`components/UploadDropzone.tsx:22`），**绕过了 `result_url`**，故主流程侥幸可用；但：
- 上传完成页展示的「结果链接」（`UploadDropzone.tsx:87`）显示的是**错误的 `/r/...` 死链**。
- 任何直接消费 `result_url` 的客户端/后端对端调用会拿到 404。

**修复**：`upload.py` 改为 `result_url = f"/result/{task_id}?token={access_token}"`（与前端路由、前端实际跳转保持一致）。

---

### 🔴 P1-3｜「规则兜底匹配」在无 API Key 时根本不可达（与文档宣称的降级能力矛盾）

**位置**：`app/services/matcher.py:45-49`（嵌入门禁）、`:112-123`（`_rule_score` 仅在向量检索成功后调用）

**现象**：`match_resume` 把**整个匹配步骤**门禁在 `await get_llm().embed(...)` 之后。无 `DEEPSEEK_API_KEY` 时，`embed` 会真实请求 DeepSeek 拿 401 → 抛错 → **直接 `return []`**，一份匹配都没有。而规则评分 `_rule_score` 只在「嵌入成功 + 向量检索到 top_jobs」之后才可能触发——但向量检索又要求岗位有嵌入向量，而岗位嵌入同样依赖 LLM。

**影响**：
1. 在 MVP「无 Key 先跑通」场景下，匹配功能对每份简历都返回空——与 `M1_IMPLEMENTATION.md`「LLM 不可用 → 匹配回退规则/空结果」「整条链路不崩，Task 仍 done」的健壮叙事**自相矛盾**：规则匹配是死代码路径，必须付 Key 才能出匹配。
2. 每次无 Key 匹配还会发一次真实网络请求到 DeepSeek（401），浪费且可能触发限流。

**修复**：实现**不依赖嵌入的纯规则匹配路径**（简历技能/文本与岗位 `required_skills`/描述做关键词重叠度），仅当配置了嵌入 Key 时才走向量+LLM 精排；`embed` 失败应降级到规则匹配而非整段放弃。

---

### 🟠 P2-1｜岗位种子非幂等（重复 seed 产生大量重复岗位）

**位置**：`app/services/job_source.py:79-87`（仅按 `soc_code` 去重）；`data/jobs/curated/seed_v1.json`（12 条**均无 `soc_code`**）

**现象**：`seed()` 用 `soc_code` 判重；但 curated 样本没有一个带 `soc_code` → `existing` 永远为 `None` → 每调一次 `/jobs/seed` 就重新插入全部 12 条。调 N 次 → 12×N 条重复岗位。

**影响**：向量检索被重复岗位稀释、重复消耗嵌入调用、匹配结果重复。

**修复**：curated 数据补 `soc_code`（或稳定 `source_key`）；去重键改为 `(source_code, soc_code or title)` 或改 upsert。

---

### 🟠 P2-2｜规则抽取技能用子串匹配导致大量假阳性

**位置**：`app/services/extractor.py:83-94`（`s in lowered` 子串判定）

**现象**：词典含 `"go"`，会命中 `google`/`good`/`goal`/`agog`；`"go"` 也是 `golang` 子串导致重复；其他短词同理。golden eval 只断言 `expect_skills_contain`，假阳性不会让测试失败。

**影响**：无 Key 走规则兜底时，技能列表被注水，进而污染匹配分数。

**修复**：改为词边界/分词匹配（`\b{skill}\b` 或按非字母数字切词后精确比对）；词典区分大小写/同义词组。

---

### 🟠 P2-3｜`access_token` 经 URL query 暴露（历史/代理日志泄露）

**位置**：`upload.py:82`、`UploadDropzone.tsx:22`（`?token=`）、`result` 页从 `window.location.search` 读取

**现象**：令牌出现在浏览器历史、跨站 `Referer`、以及任何记录 query string 的反向代理/CDN 访问日志。安全设计只保证「app 日志不记 token」，但 URL query 令牌会落到基础设施日志。

**说明**：此为 M0 审查已标记的 P2，M1 未处理，依然成立。匿名一次性令牌可接受该权衡，但应**书面记录该风险**，并优先考虑：结果页通过 `sessionStorage`/postMessage 传递令牌避免 URL 持久化，或令牌一次性使用后失效。

---

### 🟠 P2-4｜嵌入维度硬编码 `Vector(1536)`，但嵌入模型已配置化

**位置**：`app/models/models.py:108`（`Vector(1536)`）；`app/core/config.py:36`（`LLM_EMBED_MODEL` 可配）

**现象**：v0.3 已把嵌入模型 ID 做成可配置，但向量列维度写死 1536。若运维换成非 1536 维的嵌入模型，建表/检索会在运行时报 pgvector 维度不匹配。

**修复**：维度从配置派生（或启动时校验 `len(embed("probe"))`），并在文档中明确「当前仅支持 1536 维模型」。

---

### 🟠 P2-5｜日预算「M1 改 Redis」未落地（仍进程内内存计数）

**位置**：`app/core/llm.py:8`（docstring 称「日预算（M0 内存计数，M1 改 Redis）」）、`:94-112`（`_daily_spend` 为进程内 `dict` + `asyncio.Lock`）

**现象**：M1 对 `llm.py` 的改动仅为「无 Key 不含 header」，**日预算仍是单进程内存**。在 api 与 worker 多进程/多副本部署下，各进程预算互不可见，护栏形同虚设；且文档声称已改 Redis，属实现/文档不符。

**修复**：日预算迁 Redis（如 `redis.incrbyfloat` + TTL），或至少在文档中更正为「未做（M2/M4 加固项）」。

---

### 🟡 P3-1｜冗余的 `except (LlmUnavailableError, Exception)` 元组

**位置**：`app/services/matcher.py:47`、`:70`

**现象**：`Exception` 已是 `LlmUnavailableError` 的父类，元组首项永不独立命中，属复制粘贴痕迹，掩盖了「先捕获特定异常」的意图。

**修复**：改为 `except Exception`（并在分支内显式区分 `LlmUnavailableError` 与其他错误）。

---

## 4. 测试覆盖缺口（P1 级信心问题）

「18 passed」未覆盖 M1 的真实数据流与新增路由：

- ❌ 无 `result` / `resumes` 路由的集成测试（需 DB）——P1-1 的「删除不擦除」正是在这类端到端测试才会暴露的缺口。
- ❌ 无 worker `process_resume_task` 的端到端测试（落盘→解析→结构化→匹配→写库），仅单测了各纯函数。
- ❌ 无「无 API Key 时匹配返回空」的明确断言（P1-3 的退化行为未被任何测试约束，未来改对也不会被保护）。
- ❌ 无 seed 幂等性测试（P2-1）。

**建议**：M2 前补一组需 DB 的集成测试（用 testcontainers/本地 Postgres+Redis，或 CI service），覆盖上传→worker→result→delete 全链路与「无 Key 降级」契约。

---

## 5. 修复优先级建议

| 优先级 | 项 | 阻塞 M2？ |
|--------|----|-----------|
| P1-1 | 删除真正擦除结构化数据 + 修正前端文案 | 建议先修（合规） |
| P1-2 | `result_url` 改 `/result/` | 顺手修（成本低） |
| P1-3 | 无 Key 时的纯规则匹配路径 | 建议先修（否则 MVP 无 Key 无法演示匹配） |
| P2-1~P2-5 | 种子幂等 / 抽取精度 / token URL / 维度配置 / 预算 Redis | M2/M4 加固 |
| P3-1 | except 元组清理 | 随手 |

**总体判定**：M1 完成了 v0.3 规划的「上传→解析→岗位匹配」核心链路，代码结构清晰、失败隔离到位、匿名访问控制契约严谨，测试底座健康。**3 个 P1 均为「能力声明与实现不一致」（擦除/降级/链接）而非崩溃级缺陷**，不阻断 M2 启动，但 P1-1（合规）与 P1-3（无 Key 不可演示）建议在进入 M2 前花半天修掉。
