# M1 实现说明（上传 → 解析 → 岗位匹配 核心链路）

> 状态：✅ 已落地并通过单元/集成测试（18 passed, 2 skipped）。提交于 `develop` 分支。
> 对应 `PROJECT_PLAN.md` v0.3 的 M1 里程碑。

## 新增/改动文件

### 后端（apps/api）
| 文件 | 作用 |
|------|------|
| `app/services/parser.py` | **解析层（M1-1）**：可插拔工厂。TextParser / PdfParser(pdfminer.six) / DocxParser(python-docx)；legacy .doc 与 OCR 显式 NotImplementedError（MVP 范围外，明确报错而非静默失败）。仅产出原始文本，确定性、可单测。 |
| `app/services/extractor.py` | **结构化抽取（M1-2）**：仅上送白名单字段 `raw_text`（PIPL）；调 DeepSeek chat + JSON mode → `ResumeStructured`；pydantic 校验；失败回退规则提取（技能词典 + 经验年限正则）。 |
| `app/services/matcher.py` | **岗位匹配（M1-3）**：简历嵌入 → pgvector 余弦检索 top-10 → LLM 精排/理由；LLM 不可用回退规则匹配（命中技能占比）。写入 `JobMatch`。 |
| `app/workers/tasks.py` | **worker 串联（M1-4）**：解析 → 结构化 → 匹配，各步失败隔离；新增 `cleanup_expired`（TTL 清理）。 |
| `app/routers/result.py` | **结果查询（M1-5）**：`GET /api/tasks/{id}/result`，携带 token 校验，返回结构化 + 匹配岗位。 |
| `app/routers/resumes.py` | **合规删除（M1-5 §7.6）**：`DELETE /api/resumes/{id}`，软删 + 删文件 + 失效令牌。 |
| `app/main.py` | 挂载 result/resumes 路由；**上传限流（M1-6）** 基于客户端 IP 的内存令牌桶（生产改 Redis）。 |
| `app/workers/worker.py` | 注册 `cleanup_expired` 为每日 04:00 cron（arq 0.28 `cron`）。 |
| `app/core/llm.py` | 修复：无 `DEEPSEEK_API_KEY` 时不含 `Authorization` header，避免 httpx `InvalidHeader`。 |
| `app/schemas/schemas.py` | 新增 `ResumeStructured` / `MatchItem` / `ResumeResultResponse`。 |
| `pyproject.toml` | 新增 `pdfminer.six` / `python-docx` 依赖。 |
| `data/eval/golden.json` | eval 黄金集（4 份样例，质量基线）。 |
| `tests/test_{parser,extractor,matcher_rule,eval}.py` | 单测 + 规则兜底 + 黄金集基线。 |

### 前端（apps/web）
| 文件 | 作用 |
|------|------|
| `lib/api.ts` | 扩展 `getResult` / `deleteResume` 及结果类型。 |
| `app/result/[taskId]/page.tsx` | **结果页（M1-7）**：轮询 → 结构化画像 + 匹配岗位卡片（分数/已具备/建议补充）+ 删除按钮。 |
| `components/UploadDropzone.tsx` | 处理完成后跳转结果页（携带 token）。 |

## 核心链路（worker `process_resume_task`）
```
上传 → 落盘 → 建 Resume/Task → 投 ARQ
  └─ worker:
       1) 加载文件 (storage)
       2) 解析 (parser) → raw_text → 存 ResumeParse
       3) 结构化 (extractor, 白名单上送 LLM, 失败回退规则) → 存 structured_data
       4) 匹配 (matcher: 嵌入 + 向量检索 + LLM/规则精排) → 存 JobMatch
       5) 更新 Task 进度/状态
```

## 降级与鲁棒性（无 DeepSeek KEY / 限流 / 服务不可用）
- 解析失败 → Task `failed`（不拖累其他分支）。
- LLM 不可用 → 结构化回退规则；匹配回退规则 / 空结果。**整条链路不崩，Task 仍 `done`**。
- 降级状态机 + 日预算护栏见 `app/core/llm.py`（v0.3 决策）。

## 如何运行
```bash
# 1) 启动基础设施（Postgres+pgvector / Redis / api / worker / web）
docker compose -f docker-compose.dev.yml up --build

# 2) 摄入岗位（O*NET 需先生成 snapshot，否则回退 curated 手写样本）
curl -X POST "http://localhost:3001/api/jobs/seed"        # 或前端"岗位摄入"按钮
# O*NET 快照生成：python data/jobs/onet/ingest.py（需下载 O*NET 30.3 官方 SQL）

# 3) 上传简历（前端 / 或 curl）
curl -F "file=@resume.pdf" http://localhost:3001/api/upload
# 返回 task_id + access_token + result_url；前端自动跳转结果页轮询

# 4) 查看结果
GET /api/tasks/{task_id}/result?token={access_token}
```

## 本地测试（无需 DB / Redis / KEY）
```bash
cd apps/api
PYTHONPATH=. python -m pytest tests/ -q
# 18 passed, 2 skipped（seed / 向量匹配为需 DB 的集成测试）
```

## 已知限制（M2/M3 之前）
- OCR（图片简历）与 legacy .doc 未实现（v0.3 决策，MVP 范围外）。
- 上传限流为内存实现，多副本部署需改 Redis。
- 岗位数据默认 `curated` 手写样本（12 条）；O*NET 全量需下载官方 SQL 生成 snapshot。
- 单份简历成本护栏为估算（单份不会超 $0.01 熔断），硬阻断意义有限，已由日预算覆盖。
