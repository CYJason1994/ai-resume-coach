# M2 回归测试报告 (Regression Report)

- **日期**：2026-07-13
- **被测提交**：`affb2dc` — `fix(M2): 修复对抗性审查全部 P1/P2/P3 问题`（分支 `develop`，未推远程）
- **基线预期**：M2Fix 提交时声明 **31 passed / 2 skipped**
- **结论**：✅ 回归通过，M2Fix 未引入任何破坏，M2 功能完整

---

## 1. 环境

| 项 | 值 |
|---|---|
| Python | 3.13.14（托管运行时） |
| pytest | 9.1.1 |
| pytest-asyncio | 1.4.0（`asyncio_mode = auto`） |
| 依赖安装 | `pip install --target apps/api/.deps`（沙箱单命令内安装） |
| 运行目录 | `apps/api/`（**关键**：`test_eval` 用 cwd 相对路径 `data/eval/golden.json`，必须从 `apps/api/` 运行） |

---

## 2. 总结果

```
33 collected → 31 passed, 2 skipped, 0 failed, 0 error (5.05s)
```

与 M2Fix 提交时的基线 **完全一致**。

---

## 3. M2 功能覆盖（11 项全部通过）

| 测试 | 验证点 |
|---|---|
| `test_parse_llm_json_plain` | LLM 返回纯 JSON 解析 |
| `test_parse_llm_json_fenced` | 围栏代码块 ```` ```json ```` 容错 |
| `test_parse_llm_json_truncated` | 前后脏字符截断提取 |
| `test_parse_llm_json_invalid_raises` | 非法 JSON 显式抛错（不静默吞） |
| `test_build_messages_whitelist_excludes_pii` | **P2-A** 白名单上送：`work_history/projects` 等 PII 已被剔除 |
| `test_rule_questions_all_dimensions` | 规则兜底覆盖全部 4 维度 |
| `test_generate_questions_llm_path` | LLM 路径 + `task_id` 正确归属（**P1-A**） |
| `test_generate_questions_rule_fallback` | LLM 不可用 → `degraded=True` 规则兜底 |
| `test_generate_questions_bad_json_fallback` | **P3-A** 任何生成异常（含坏 JSON）→ 回退规则 |
| `test_get_interview_by_task_isolation` | **P1-A** 按 `task_id` 隔离，多岗位不串味 |

> M2 面试题目生成（4 维度：behavioral/technical/role/stress、PII 白名单、规则兜底、task_id 隔离、任意异常降级）全链路健康。

---

## 4. 非 M2 测试（回归守护，全过）

`test_app_imports`(M0.1) · `test_config` · `test_health` · `test_security` · `test_parser` · `test_extractor`(M1) · `test_matcher_rule`(M1 P1-3) · `test_eval`(golden) 全部通过 —— 确认 M2Fix 对 M0/M1 链路无回退。

---

## 5. Skip（非失败，环境性）

| 测试 | 跳过原因 |
|---|---|
| `test_seed_writes_rows` | 需可达 PostgreSQL（asyncpg 探测）；沙箱无 DB → 正确 skip |
| `test_pdf_missing_dep_or_works` | 沙箱已装 `pdfminer.six` → 走"已安装"分支并 skip 缺失依赖分支（环境性） |

---

## 6. 遗留事项 / 后续建议（非 M2 阻塞）

1. **`test_eval` 路径脆弱**：依赖未入库的 `apps/api/data/eval/golden.json`，且用 cwd 相对路径。仅当 pytest 在 `apps/api/` 下运行时通过。建议：
   - 将 `golden.json` 加入 git，使 CI 可复现；
   - 改用 `importlib.resources` 或复用 conftest 的 `_REPO` 仓库根解析，消除 cwd 依赖。
   - 与 M2 无关，不影响本次回归结论。
2. **分支状态**：`develop` 领先 `origin/develop` 1 个提交（`affb2dc`），按此前要求**未推送远程**。
