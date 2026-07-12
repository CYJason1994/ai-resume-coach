# ai-resume-coach

> 生产级简历优化平台：多格式简历上传 → 解析与结构化 → 岗位匹配（O*NET 源，可配置）→ 面试题生成与面试官场景模拟（M2/M3）。

品牌 / 仓库：`ai-resume-coach`（GitHub: CYJason1994/ai-resume-coach）
规划文档：`docs/PROJECT_PLAN.md`（v0.3，已关闭全部对抗性审查 blocker）
项目约束：`docs/项目约束.md`（AI 工程协作增强规则，约束所有协作）

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | Next.js (App Router) + TS + Tailwind |
| 后端 | Python FastAPI |
| AI | DeepSeek（`deepseek-v4-flash` / `deepseek-embedding`，模型 ID 配置化） |
| 数据库 | PostgreSQL + pgvector |
| 队列 | ARQ（Redis-only，async 原生） |
| 岗位源 | `JobSourceProvider` 可配置（默认 `onet` → O*NET 30.3, CC BY 4.0） |

## 快速开始（M0 最小可跑闭环）

```bash
# 1. 准备环境变量
cp .env.example .env   # 填入 DEEPSEEK_API_KEY 等

# 2. 一键起前后端 + Worker + Postgres(pgvector) + Redis
docker compose -f docker-compose.dev.yml up --build

# 3. 摄入岗位种子（O*NET 可配置源，首次需网络拉取快照或用手写 json）
docker compose -f docker-compose.dev.yml exec api python scripts/seed_jobs.py

# 4. 访问
#    前端  http://localhost:3000
#    后端  http://localhost:3001  (OpenAPI: /docs)
#    探活  http://localhost:3001/health
```

本地开发（无 Docker）：

```bash
# 后端
cd apps/api && python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" && uvicorn app.main:app --reload --port 3001

# 前端
cd apps/web && pnpm install && pnpm dev
```

## 数据来源与版权（务必阅读）

岗位数据默认来自 **O*NET 30.3 Database**（美国劳工部，CC BY 4.0）。
署名要求见 `data/jobs/README.md`。中文标题/技能映射见 `data/jobs/zh_overlay.json`（自有 IP）。
任何外部数据集仅在明确可商用/CC 协议下引入，并在 `jobs.source_*` 字段标注出处与许可。

## 里程碑

- **M0**（进行中）：最小可跑闭环。
- **M1**：上传 + 解析 + 岗位匹配（含 O*NET 摄入、合规/限流/成本护栏、eval）。
- **M2/M3/M4**：面试题生成 / 面试官模拟 / 生产加固。

详见 `docs/PROJECT_PLAN.md`。
