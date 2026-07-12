# M1 对抗性审查 — 修复记录

> 对应报告：`docs/ADVERSARIAL_REVIEW_M1.md`
> 修复分支：`develop`（本地提交，未推远程）
> 验证：`pytest tests/ -q` → **21 passed, 2 skipped**（Python 3.13.12）

## 已修复

### 🔴 P1-1｜删除真正擦除结构化数据（PIPL 删除权）+ 修正前端虚假承诺
- 新增 `app/services/compliance.py::purge_resume_personal_data`：硬删该简历的 `resume_parses` 与 `job_matches` 行。
- `routers/resumes.py::delete_resume`：删除时级联调用 `purge_resume_personal_data`，确保原始文本/结构化画像与匹配结果真正清除。
- `workers/tasks.py::cleanup_expired`：TTL 清理对过期简历同样 purge 其 parses/matches。
- 前端 `apps/web/app/result/[taskId]/page.tsx`：确认框文案由「文件与结构化数据将被彻底移除」（虚假）改为「文件与可访问的分析结果将被移除，服务器上的原始数据将按要求清除」，与实际行为一致。

### 🔴 P1-2｜`result_url` 路径修正
- `routers/upload.py`：`/r/{task_id}` → `/result/{task_id}`，与前端路由及前端实际跳转一致，消除死链。

### 🔴 P1-3｜无 API Key 时规则匹配真正可达
- `services/matcher.py`：`match_resume` 不再把整段匹配门禁在 `embed` 之后。
  - `embed` 失败（无 Key / 网络错误）→ 直接走新增的 `_rule_match_all` 纯规则匹配（对全部带技能要求的岗位按重叠度打分取 top-K）。
  - 向量粗排为空（岗位库无嵌入向量，如 seed 时未配 Key）→ 同样回退 `_rule_match_all`。
- 修复后 MVP 在无 DeepSeek Key 时仍能产出匹配，与 v0.3「整条链路不崩」承诺一致。

### 🟠 P2-1｜岗位种子幂等
- `services/job_source.py`：去重键由「仅 `soc_code`（curated 全为空 → 永不命中）」改为 `(source_code, title)`，重复 seed 不再插入重复岗位。

### 🟠 P2-2｜规则抽取子串假阳性
- `services/extractor.py`：技能匹配由 `s in lowered` 改为词边界正则 `(?<![a-z0-9]){skill}(?![a-z0-9])`，修复 `go` 命中 `google/good/goal` 等问题。

### 🟠 P2-4｜嵌入维度硬编码
- `core/config.py`：新增 `LLM_EMBED_DIM: int = 1536`。
- `models/models.py`：`Vector(1536)` → `Vector(settings.LLM_EMBED_DIM)`，维度随嵌入模型配置化（默认仍 1536，无破坏性变更）。

### 🟡 P3-1｜冗余 except 元组
- `services/matcher.py`：`except (LlmUnavailableError, Exception)` / `except (json.JSONDecodeError, Exception)` 简化为 `except Exception`，意图更清晰。

### 🟠 P2-5｜日预算文档/实现不符
- `core/llm.py`：docstring 与 `_daily_spend` 处更正为「M0/M1 进程内内存计数」，并标注 `TODO(M2/M4)` 迁 Redis（多副本共享预算）。由「虚假声称已改 Redis」改为「如实标注未做」。

## 未修复（留待 M2/M4，已在报告标注或需架构决策）
- **P2-3｜`access_token` 经 URL query 暴露**：需前端改 `sessionStorage`/postMessage 传递或令牌一次性失效，属架构改动，单独排期。

## 新增测试（回归保护）
- `test_matcher_rule.py::test_rule_match_all_ranks_and_limits`：规则匹配排序/top-K 正确。
- `test_matcher_rule.py::test_match_resume_falls_back_to_rule_when_embed_fails`：无 Key（embed 抛错）时 `match_resume` 必须产出匹配（P1-3 回归）。
- `test_extractor.py::test_rule_based_no_substring_false_positive`：`go` 不被 `google` 子串误命中（P2-2 回归）。
