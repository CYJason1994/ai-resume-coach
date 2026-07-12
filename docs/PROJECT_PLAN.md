# 简历优化平台 — 整体架构与开发规划（生产级）v0.3

> 版本：v0.3（根据第二轮对抗性审查 + 用户决策定稿）｜ 日期：2026-07-12 ｜ 角色：Senior Developer（高级开发工程师）
> 状态：**v0.3 已关闭全部 blocker，进入 M0 实施**
> 品牌 / 仓库：`ai-resume-coach`（GitHub: CYJason1994/ai-resume-coach）

---

## 0. 修订记录（相对 v0.2）

| 项 | v0.2 问题 | v0.3 修订（决策依据） |
|---|---|---|
| 岗位数据来源 (R2-C1) | 数据从哪来/版权未定 → M1 必卡死 | **采用 O*NET 30.3 (CC BY 4.0, 美国劳工部)** 为规范化职业/技能源；实现 `JobSourceProvider` 可配置（`onet`｜`json`｜未来 `kaggle_api`）；叠加可配置中文标题/技能 overlay；MVP 先摄入 ~120 高频职业 → 后续 300+。来源与许可全程标注（署名）。 |
| 匿名访问控制 (R2-C2) | token 契约未定义 → PII 泄露红线 | 见 §7.1 完整契约：`access_token = secrets.token_urlsafe(32)`，DB 存 SHA-256 哈希；`task_id`/`storage_key` 用 UUID（不可猜测）；所有读路径经中间件统一校验，无/不匹配即 403；token 不进日志；结果 URL `/r/{task_id}?token=...` 或 header `X-Access-Token`。 |
| 单点 LLM 韧性 (R2-C3) | 降级/退避无参数 → 演示易崩 | 见 §7.8：不可用判定（连续 3 次 429/5xx/超时 → 降级 + 冷却探测）、指数退避+抖动、用户可见降级提示文案、预算护栏数字（§9）。 |
| ARQ 锁死 (R2-M1) | 仍留 BackgroundTasks 口子 | **删除所有 BackgroundTasks 措辞，M0 起仅用 ARQ**（Redis-only、async 原生、重启可持久、dev/prod 一致）。 |
| 成本护栏 (R2-M2) | 无数字=摆设 | 见 §9 具体数字：单份成本熔断 $0.01、并发信号量 6、日预算可配、嵌入预计算、调用批量合并。 |
| eval (R2-M3) | 谁标/何时跑/阈值虚 | M1 起 CI 跑 ≥20 份手工黄金集；指标 = 字段级 F1 + 匹配 Top-1/3 命中 + LLM-as-judge；设基线阈值，跌破告警。 |
| pgvector (R2-M4) | 易漏生产细节 | M0 migrations 写明 `CREATE EXTENSION`、HNSW 参数（`m`/`ef_construction`）、首次建索引锁表风险与低峰执行。 |
| 最小化上送 (R2-M5) | 字段边界未定 | §7.3 落地为**代码级白名单 payload schema**（送技能/经历摘要/教育/目标职能；剔除姓名/性别/年龄/身份证/电话/邮箱/住址）。 |

> 第一轮审查：`docs/ADVERSARIAL_REVIEW.md`；第二轮：`docs/ADVERSARIAL_REVIEW_2.md`（v0.3 已关闭其全部 blocker）。

---

## 1. 项目目标与范围

构建一个**生产级**简历优化平台，核心能力：

1. **多格式简历上传** —— MVP 支持 **PDF / Word(docx/doc) / 纯文本 & Markdown**；图片 OCR 为后续里程碑。
2. **简历解析与岗位匹配** —— 抽取结构化信息，结合岗位库（O*NET 源，可配置）给出匹配度、匹配点、能力缺口与推荐岗位。
3. **面试题生成 + 面试官场景模拟**（M2/M3）—— 按所选岗位生成针对性面试题 + 可对话"模拟面试官"。

MVP 边界：第一阶段先做 **上传 → 解析 → 岗位匹配**（不含 OCR、不含面试模块）。

---

## 2. 技术栈决策（已对齐 + v0.3 修订）

| 层 | 选型 | 理由 / 修订点 |
|---|---|---|
| 前端 | **Next.js (App Router) + TS + Tailwind** | 高端交互、SSR/流式友好 |
| 后端 | **Python FastAPI** | 异步高性能、AI/解析生态强 |
| AI | **DeepSeek** | chat=`deepseek-v4-flash`（配置化，关注 2026-07-24 弃用）；embedding=`deepseek-embedding`（1536 维，~$0.02/1M tokens，已核实可用，多语种） |
| 关系库 | **PostgreSQL + pgvector** | 主数据 + 向量检索；M0 即启用扩展与 HNSW |
| 异步/队列 | **ARQ（Redis-only，async 原生）** | **M0 起唯一选择**，禁用 BackgroundTasks |
| 对象存储 | **开发：本地磁盘；生产：MinIO/COS（docker profile）** | `StorageProvider` 抽象，MVP 不引 MinIO |
| OCR | 后续里程碑：云 OCR（腾讯云等）按需 | MVP 不含；保留 `OcrParser` 接口占位 |
| 解析 | PyMuPDF / pdfplumber（PDF）、python-docx（Word） | 版式 + 表格抽取 |
| 岗位源 | **`JobSourceProvider` 可配置（默认 `onet`）** | 见 §7.4：O*NET(CC BY 4.0) 为主，中文 overlay 可配，json/kaggle 为备选 |
| 容器化 | **Docker Compose（dev/prod profile）** | 环境一致 |
| 质量 | pytest / Playwright / Ruff / ESLint / Prettier | 代码门禁 |
| 可观测 | **M1 起：structlog + Sentry**；M4 补 OTel+Grafana | 早期可见性 |
| 包管理 | **pnpm workspaces**；TS 类型由 OpenAPI 生成 | 避免与 Pydantic 漂移 |

### 模型版本治理
- 所有模型 ID 集中在 `settings`（`LLM_CHAT_MODEL`、`LLM_EMBED_MODEL`），默认 `deepseek-v4-flash` / `deepseek-embedding`。
- 启动对 DeepSeek 做健康检查（探活 + 模型可用性）；封装 `LlmProvider` 抽象，便于换模型/厂商。
- 监控 DeepSeek 弃用公告（尤其 2026-07-24 的 chat/reasoner 弃用）。

---

## 3. 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                        用户浏览器                              │
│   Next.js (App Router) · Tailwind · Framer Motion             │
│   上传 / 解析进度(SSE/轮询) / 匹配结果 / (未来)面试模拟        │
└───────────────▲──────────────────────────┬───────────────────┘
                │ HTTPS (JSON / SSE)         │
┌───────────────┴──────────────────────────▼───────────────────┐
│              FastAPI 网关 (api)                                │
│  POST /upload(→task_id+access_token) /tasks/{id}(token校验)    │
│  /parse /structure /match · /health(含DeepSeek探活)            │
│  /jobs/seed(配置化摄入 O*NET) · (M2/M3)/interview             │
│      │                │                  │                   │
│  ┌───▼───┐      ┌─────▼─────┐      ┌─────▼─────┐              │
│  │ Redis │      │ PostgreSQL│      │ DeepSeek  │              │
│  │ARQ队列│      │ +pgvector  │      │  API       │              │
│  └───────┘      └───────────┘      └───────────┘              │
│  StorageProvider: 本地磁盘(dev) / MinIO·COS(prod profile)      │
└──────────────────────────────────────────────────────────────┘
        ARQ Worker 处理 解析 / 结构化 / 匹配 重任务
```

**任务状态机**：`uploaded → parsing → structured → matching → done | failed`，前端通过 `/tasks/{id}`（带 token）轮询或 SSE 订阅进度。

**关键设计原则**
- **同步轻、异步重**：上传即回 `task_id` + `access_token`，重活走 ARQ Worker。
- **解析与理解分离**：先确定性抽"原始文本"，再 LLM 做"结构化理解"（可重试）。
- **匹配双引擎**：pgvector 语义粗排 + LLM 理由精排。
- **岗位源可配置**：O*NET(CC BY 4.0) 为主，中文 overlay + json/kaggle 备选，§7.4。

---

## 4. 目录结构（Monorepo）

```
resume-optimization-platform/        # 品牌 ai-resume-coach
├── apps/web/                # Next.js 前端（含 OpenAPI 生成的 API client）
├── apps/api/                # FastAPI 后端（core/services/routers/workers/models/schemas/jobs/tests）
├── packages/shared/         # TS 类型（由 OpenAPI 生成，避免与 Pydantic 漂移）
├── infra/
│   ├── docker/              # Dockerfile(web/api/worker)
│   ├── compose.dev.yml      # 最小闭环：postgres+pgvector+redis+api+web+worker
│   └── compose.prod.yml     # + minio / ocr 服务（profile 启用）
├── data/jobs/               # 岗位源（标注来源/版权/署名）
│   ├── onet/                # O*NET 摄入脚本 + 快照（CC BY 4.0 署名）
│   ├── curated/             # 手写/覆盖层 JSON（自有 IP）
│   └── zh_overlay.json      # 中文标题/技能映射（可配置，自有 IP）
├── docs/                    # 规划 / 约束 / 审查
├── .github/workflows/       # CI（lint+test+类型）
├── scripts/                 # seed_jobs.py 等
└── README.md
```

---

## 5. 数据模型（PostgreSQL + pgvector）

```sql
-- 用户（MVP 匿名；M4 接入鉴权，user_id 可空）
users(id, email, password_hash, created_at)

-- 简历主记录（匿名所有权靠 access_token 哈希；软删 + 保留期）
resumes(
  id UUID PK, user_id NULLABLE, original_filename, storage_key UUID,
  storage_provider, file_type, file_size, status,
  access_token_hash TEXT,            -- SHA-256(access_token)，原值仅返回一次
  retention_until TIMESTAMPTZ, deleted_at TIMESTAMPTZ, created_at
)  -- status: uploaded|parsing|structured|matching|done|failed

-- 处理任务（进度/状态机，结果查看受 token 保护）
tasks(id UUID PK, resume_id FK, type, status, progress INT,
      error_text, created_at, updated_at)

-- 解析结果
resume_parses(
  id, resume_id, raw_text, structured_data JSONB,
  parser_used, ocr_used BOOL, confidence FLOAT, status, error_text, created_at
)

-- 岗位库（种子源可配置；来源与版权必标注）
jobs(
  id, source_code, source_license, title, title_zh NULLABLE,
  category, level, description, description_zh NULLABLE,
  required_skills TEXT[], required_skills_zh TEXT[] NULLABLE,
  soc_code NULLABLE, is_seed BOOL, embedding vector(1536), created_at
)

-- 匹配结果
job_matches(
  id, resume_id, job_id, score FLOAT,
  matched_skills TEXT[], missing_skills TEXT[], rationale TEXT, created_at
)

-- （M2/M3）interview_sessions / interview_questions
```

> 迁移中启用 `pgvector` 扩展（M0 写明：`CREATE EXTENSION IF NOT EXISTS vector;`）；`jobs.embedding` 建 HNSW 索引（数据量就绪后调参，`m=16, ef_construction=64`）。`resumes.access_token_hash` 用于匿名场景所有权与结果访问控制；`storage_key`/`id` 均 UUID 不可猜测。

---

## 6. 核心流程（MVP 链路）

1. **上传**：前端拖拽 → 后端校验（扩展名 + 魔数 + 硬大小上限 10MB）→ 落盘（本地磁盘/dev）→ 建 `resumes`(含 `access_token_hash`) 与 `tasks` → 签发 `access_token`(仅返回一次) + `task_id` → 投递 ARQ 任务。
2. **解析**（MVP：PDF/Word/文本）：Worker 按 `file_type` 走解析器 → `raw_text`（检测"文本层为空"且 OCR 启用时路由 OCR）。
3. **结构化**：混合抽取（正则/技能词典取高精字段 + DeepSeek 补其余）→ 固定 schema `Resume JSON`；schema 校验失败回退二次调用或人工复核队列。**上送 LLM 走代码级白名单 payload（§7.3）**。
4. **匹配**：结构化技能嵌入（deepseek-embedding,1536，多语种）→ pgvector Top-K 召回 → LLM 给分/匹配点/缺口；LLM 不可用时降级规则匹配（§7.8，用户可见提示）。
5. **呈现**：前端 SSE/轮询进度（带 `X-Access-Token`）→ 展示结构化预览 + 匹配岗位卡片（评分/缺口标签/明暗主题）。

**延迟预算（明确拆分）**
- 接口/页面响应 **<1.5s**（不含 AI 计算）。
- 简历处理为**异步任务**，接受 **10–60s**；前端进度条/SSE，可离开后凭 `task_id`+token 回来查看。

**最小化上送 LLM（合规 + 白名单）**：仅送 `{skills[], experiences[{company,title,duration,responsibility_summary}], education[], target_functions[]}`；**剔除**姓名/性别/年龄/身份证/电话/邮箱/住址/照片。

---

## 7. 模块设计与关键技术点

### 7.1 文件上传与匿名访问控制（契约，R2-C2）
- 前端 `react-dropzone` + 进度；后端校验扩展名 + **魔数** + 硬大小上限（10MB）。
- **Token 契约**：
  - 上传即签发 `access_token = secrets.token_urlsafe(32)`（~256bit），**原值仅返回一次**；DB 存 `SHA-256(access_token)`（`access_token_hash`）。
  - `task_id`、`storage_key` 均为 `uuid4()`（不可猜测、不暴露序号）。
  - 所有读路径（`GET /tasks/{id}`、`/resumes/{id}`、`/results/...`）经 **FastAPI 中间件/依赖**统一校验：请求须带 `X-Access-Token` header 或 `?token=`；服务端算 `SHA-256` 比对，不匹配即 `403`。
  - token **不进日志**（中间件脱敏）；结果 URL `/r/{task_id}?token=...` 可收藏但需用户主动保存。
  - 删除：`DELETE /resumes/{id}` 同样需 token；软删 + `retention_until` TTL 自动清理。
- 安全：Worker 内 **ClamAV**（或占位 + 拒绝加密/压缩炸弹）；解析超时熔断。
- 限流（M1 起）：per-IP 限流 + 全局 LLM 并发**信号量(6)**。

### 7.2 解析流水线（可插拔，MVP 不含 OCR）
- 工厂模式：`ParserFactory.get(file_type)` → `PdfParser / DocxParser / TextParser`（OCR 接口占位，后续接云 OCR）。
- PDF：PyMuPDF 提文本+坐标，pdfplumber 补表格；检测文本层为空 → （OCR 启用时）路由 OCR。
- Word：python-docx 读段落/表格/标题层级。
- 统一输出 `{text, blocks, warnings}`。

### 7.3 LLM 结构化（DeepSeek，混合抽取 + 上送白名单）
- **混合**：正则/技能词典取邮箱、电话、年限、学历、已知技能等高精字段；LLM 补其余语义字段。
- **上送 payload 白名单（代码级）**：仅 `skills / experiences(company,title,duration,responsibility_summary) / education / target_functions`；PII 字段在发送前被 `sanitize_for_llm()` 剥离并单测覆盖。
- 固定 JSON schema + few-shot + 严格 system prompt；失败重试 + Pydantic 校验；低置信度标记人工复核。
- 模型 ID 配置化。

### 7.4 岗位源 `JobSourceProvider`（可配置，R2-C1）
- **设计目标**：数据来源可插拔、版权可审计、中文可用。
- **默认 `onet`**：`OnetProvider` 从 O*NET 30.3（CC BY 4.0, 美国劳工部）摄入职业 → 映射为 `jobs` 行（`title/skills/tech/tasks` 组合为 `description`，`required_skills` 取 Skills+Technology Skills），应用 `zh_overlay.json`（可配置）补充中文标题/技能同义词；标注 `source_code='onet'`, `source_license='CC BY 4.0'`, `soc_code`。
- **备选 `json`**：`JsonProvider` 加载 `data/jobs/curated/*.json`（手写/覆盖层，自有 IP），用于补充或覆盖特定岗位。
- **未来 `kaggle_api`**：占位接口，可接 Kaggle "Job Market 2025"(Apache 2.0) 作真实描述补充。
- 配置：`JOB_SOURCE=onet|json`（默认 `onet`）、`JOB_SOURCE_ZH_OVERLAY=1`、`JOB_SOURCE_LIMIT`（MVP 先摄入 ~120 高频职业，控嵌入成本）、`JOB_SOURCE_CATEGORIES`（按职能筛选）。
- **署名与版权**：`data/jobs/README.md` + 应用"关于"页 + 数据文件头，均含 O*NET 署名："Includes information from the O*NET 30.3 Database by USDOL/ETA. Used under the CC BY 4.0 license. O*NET® is a trademark of USDOL/ETA."
- 摄入脚本 `scripts/seed_jobs.py`：读 provider → upsert `jobs` → 预计算 `deepseek-embedding` 嵌入。结构清晰，扩充=填表/换 provider，非重写。
- **数据规模里程碑**：v1= O*NET 摄入 ~120 高频职业（M1） → v2= 扩充至 300+（含更多职能与 zh 覆盖）→ 运营期持续。

### 7.5 面试模块（M2/M3，预留）
- 题目生成：`{resume_json, job}` → DeepSeek → 分维度（行为/技术/岗位/压力）。
- 模拟面试官：SSE 流式；**上下文管理**（滚动窗口/周期摘要）防超上下文；成本上限；M4 鉴权采用 BFF/same-site 解决跨域 cookie。

### 7.6 合规与隐私（PIPL，MVP 起有骨架）
- 静态加密（磁盘/MinIO SSE）+ DB 字段脱敏。
- 最小化上送 LLM（§6/§7.3 白名单）；明确数据留存与删除（用户主动删除 + `retention_until` TTL 自动清理，M1 即做删除按钮）。
- 访问控制：匿名场景靠 `access_token_hash` 门控（§7.1），禁止可猜测 ID 越权。
- 隐私/数据处理说明披露 AI 处理与第三方（DeepSeek）调用。

### 7.7 质量评估（eval，M1 起）
- M1 起在 CI 跑 **≥20 份**手工标注黄金集（简历 + 期望字段/岗位）。
- 指标：字段级 F1（邮箱/电话/技能召回）、匹配 Top-1/Top-3 命中率、LLM-as-judge 一致性；设基线阈值，跌破即告警。先小后大。

### 7.8 单点 LLM 韧性（R2-C3）
- **不可用判定**：连续 `LLM_FAILURE_THRESHOLD=3` 次 429/5xx/超时 → 进入降级，并启动冷却探测（每 30s 探一次，恢复则退出降级）。
- **退避**：指数退避 + 抖动（`base=0.5s, cap=30s`）；队列积压返回"处理中，预计 X 秒"而非失败。
- **用户可见降级**：标注"AI 理由暂不可用，已用规则匹配（技能交集）"，给出走通路径；不白屏。
- **预算护栏**：见 §9。

---

## 8. 里程碑与开发流程

### M0 — 最小可跑闭环（环境一致）【当前进行中】
- [ ] Monorepo（pnpm workspaces）：apps/web, apps/api, packages/shared, infra
- [ ] `compose.dev.yml`：Postgres+pgvector / Redis(ARQ) / api / web / worker
- [ ] FastAPI 骨架：配置（模型 ID 配置化、**成本护栏数字**、**JOB_SOURCE 配置**、**access_token 设置**）、DB、CORS、健康检查（含 DeepSeek 探活）、OpenAPI
- [ ] 安全模块：`security.py`（token 生成/哈希/校验）、匿名访问控制依赖/中间件
- [ ] `LlmProvider` 抽象 + DeepSeek 客户端 + 健康检查；ARQ 配置与 worker 跑通
- [ ] 数据模型 + M0 migrations（pgvector 扩展、HNSW 索引参数、锁表风险说明）
- [ ] Next.js 骨架：Tailwind、明/暗/跟随主题、基础布局、由 OpenAPI 生成 API client（携带 token）
- [ ] `JobSourceProvider` 接口 + `OnetProvider`/`JsonProvider` 骨架 + `data/jobs` 种子样本 + 署名
- [ ] CI(lint+test+类型)、README（含品牌/署名/quickstart）、env 模板
- [ ] 本地 git 初始化 + 首提交（不自动推远程）

### M1 — MVP：上传 + 解析 + 岗位匹配（强制三件套）
- [ ] 上传 API + 前端组件（PDF/Word/文本、进度、校验、access_token 返回与携带）
- [ ] 解析流水线（PDF/Word/文本）+ 单测
- [ ] 结构化抽取（混合 + schema + **上送白名单**校验）
- [ ] **岗位源摄入**：`scripts/seed_jobs.py` 跑 O*NET(~120) + 中文 overlay + 嵌入 + pgvector 匹配 + LLM 理由 + 降级模式
- [ ] **删除/留存（TTL）+ per-IP 限流 + 全局 LLM 信号量(6) + 成本熔断($0.01)**
- [ ] 结果页（SSE/轮询，token 校验）+ **eval 黄金集 + CI 基线**
- [ ] 端到端联调 + E2E 冒烟

### M2 — 面试题目生成 ｜ M3 — 面试官场景模拟 ｜ M4 — 生产加固
- M2：岗位选择 → 分维度题目生成 + 展示/导出
- M3：SSE 流式模拟面试官 + 上下文管理 + 评分建议
- M4：鉴权（BFF/same-site）、配额限流、可观测(OTel+Grafana)、安全审计、无障碍/60fps（M1 起前端基线）、部署文档/回滚/备份

---

## 9. 生产级考量（贯穿全程）

- **安全**：魔数+扩展名+大小校验、ClamAV、隔离存储、access_token 哈希门控、密钥 env/Secret、最小权限 DB、per-IP 限流、LLM 输出消毒。
- **成本护栏（数字，R2-M2）**：
  - 嵌入：简历 ~3k tokens × $0.02/1M ≈ **$0.00006/份**；岗位一次性摄入 ~120×1k ≈ 可忽略。
  - 结构化 chat：~5k tokens ≈ 依 DeepSeek 定价（假设 ~$0.0001–0.0003/份，构建时核对 https://platform.deepseek.com/api-docs/pricing）。
  - 匹配：Top-K 批量一次调用 ~数 k tokens ≈ ~$0.0002/份。
  - **护栏**：单份成本熔断 `LLM_COST_CAP_PER_RESUME=$0.01`；全局并发信号量 `LLM_CONCURRENCY=6`；日预算 `LLM_DAILY_BUDGET` 可配（dev 默认 $5）；岗位嵌入预计算、调用批量合并。无数字=摆设，已落地。
- **性能**：异步化、pgvector HNSW 索引、Redis 缓存岗位库、前端懒加载/流式；延迟预算拆分明确。
- **合规（PIPL）**：加密、最小化上送、留存删除、访问控制、第三方披露。
- **质量**：eval 黄金集 + CI 回归；解析确定性单测；LLM 调用 mock；上送白名单单测。
- **可观测**：structlog + Sentry(M1) → OTel+Grafana(M4)；任务队列监控。
- **可配置性**：岗位源、模型 ID、成本护栏、主题、存储 provider 全部 env 配置化，避免硬编码。

---

## 10. 风险与权衡

| 风险 | 缓解（v0.3） |
|---|---|
| 模型弃用（2026-07-24） | 模型 ID 配置化 + 健康检查 + provider 抽象 |
| 岗位数据版权/来源 | **O*NET(CC BY 4.0) 可配置源 + 署名 + zh overlay** |
| 跨语种嵌入（中文简历 vs 英文 O*NET） | zh overlay 提供中文技能名；嵌入用多语种 deepseek-embedding；M1 eval 验证命中率 |
| PDF 版式/脏简历 | 多解析器 + 混合抽取 + 低置信度复核 |
| OCR（后续） | 云 OCR 按需 + 结果可编辑 |
| LLM 不稳定/限流 | schema 校验+重试 + 规则降级(用户可见) + 退避 + 冷却探测 |
| 匿名越权访问 | access_token 哈希门控 + UUID 不可猜测 + 中间件统一校验 |
| 成本失控 | 单份熔断 $0.01 + 信号量 6 + 日预算 + 预计算 |
| pgvector 启用/索引 | M0 migrations 写明扩展/索引参数/锁表风险 |
| 评估缺失 | M1 起 ≥20 份黄金集 + CI 指标 + 基线阈值 |

---

## 11. 下一步

1. **M0 实施中**：最小可跑闭环，`docker compose -f compose.dev.yml up` 一键起前后端 + Worker + Postgres(pgvector) + Redis。
2. M0 完成后进入 M1：落地上传/解析/匹配 + O*NET 岗位摄入 + 合规/限流/成本护栏 + eval。
3. 任何大改将主动询问是否同步 `/docs`（见 `docs/项目约束.md`）。

> 本文档随审查持续迭代。具体模块实现细节（API 契约、提示词模板、组件 specs）在对应里程碑启动时补充到 `docs/`。
