# M4 实施计划 — 生产加固（Production Hardening）【草案 · 待范围确认】

> 版本：v0.3 延续 ｜ 日期：2026-07-13 ｜ 角色：Senior Developer（高级开发工程师）
> 分支：`develop`（基于 M3 修复 `d45b81b`）
> 范围：**生产级加固** = 鉴权(BFF/same-site) + 配额限流 + 可观测(OTel+Grafana) + 安全审计 + 无障碍/60fps + 部署文档/回滚/备份
> 状态：**草案**。§10 列出 4 个待确认分叉，须经你确认后定稿再实施。

---

## 1. 目标 / 场景 / 成功标准（总览）

M4 把"能跑通的 MVP 链路 + 面试模块"变成**可上线、可运维、可审计**的服务。成功标准按工作流拆分（每条独立验收）：

| # | 工作流 | 成功标准（草案） |
|---|---|---|
| W1 | 鉴权(BFF/same-site) | 跨域 cookie 安全落地；匿名流与登录流并存或平滑迁移；无 XSS/CSRF/越权 |
| W2 | 配额限流 | per-token/per-user 配额生效；超限清晰拒绝；与现有 per-IP+信号量(6)不冲突 |
| W3 | 可观测(OTel+Grafana) | 请求链路 trace + 关键指标；错误可上报；compose profile 可起（沙箱不跑活实例） |
| W4 | 安全审计 | 访问/Token 使用留痕；输入消毒加固；安全响应头；ClamAV 占位清晰 |
| W5 | 无障碍/60fps | 关键页面 WCAG 2.1 AA；动画 60fps；Lighthouse a11y≥90 |
| W6 | 部署文档/回滚/备份 | compose.prod + 回滚脚本 + DB 备份脚本 + 部署 runbook |

---

## 2. 现状盘点（已核实，避免重复造轮子）

- **日志**：`core/logging.py` 已是结构化 JSON（request_id 传播、绝不记 token/PII/密钥），但是**自研 `StructuredLogger` 而非真正的 structlog 库**，且**无 Sentry / OTel / Grafana**。
- **限流**：`main.py` 仅 **per-IP 内存令牌桶**（上传限流，注释明确"生产改用 Redis"）；LLM 全局信号量(6) + 日预算护栏已在 `core/llm.py`。**无 per-user/per-token 配额**。
- **鉴权**：纯匿名 `access_token`（SHA-256 哈希门控），**无 users 表、无登录/JWT、无 BFF**。`models.py` 仅 `resumes.user_id` 可空占位，`users` 表类未定义。
- **安全**：读路径统一 token 中间件；PIPL 删除权（TTL + 级联擦除）已有骨架；**无审计日志、无 ClamAV、无安全响应头**。
- **前端**：已有明/暗/跟随主题；**无障碍与 60fps 未系统做过**。
- **部署**：`compose.dev.yml` 存在；**无 compose.prod、无回滚/备份脚本、无 runbook**。

---

## 3. 工作流设计（草案）

### W1 — 鉴权（BFF / same-site）【最大分叉，见 §10-Q1】
- 候选 A（完整账号体系）：`users` 表 + 注册/登录（密码哈希 argon2/bcrypt）+ 会话（httpOnly same-site cookie 或 JWT）+ BFF 反向代理解决跨域 cookie。
- 候选 B（轻量）：仅对现有匿名 token 做 same-site cookie 加固，不引入账号。
- 候选 C：本迭代不做，先聚焦其他加固。
- **推荐**：A（与 §5 数据模型 `users` 一致，且生产级平台需要账号）+ BFF 模式。但 A 是产品级决策，需你确认。

### W2 — 配额限流
- 在现有 per-IP + LLM 信号量(6) 之上，加 **per-token/per-user 配额**（按日/按小时窗口，Redis 计数，复用 `.deps` 已有 redis 客户端）。
- 超限返回 `429` + 清晰文案；配额数字配置化（`QUOTA_*` env）。
- 与上传限流、LLM 信号量**正交**，不改动既有逻辑。

### W3 — 可观测（OTel + Grafana + Sentry）【见 §10-Q2】
- 请求级 **OTel trace**（FastAPI middleware 注入 traceparent + span），关键指标（请求数/延迟/LLM 调用/降级次数/配额拒绝）走 OTel metrics。
- **Sentry**：错误上报（DSN 配置化，无 DSN 时静默）。
- **Grafana/collector**：提供 `compose.observ.yml`（OTel Collector + Tempo/Prometheus + Grafana）+ 示例 dashboard JSON；**沙箱内不跑活实例**，仅保证配置与代码可起。
- 复用 `core/logging.py` 的 request_id 与 OTel trace_id 对齐。

### W4 — 安全审计
- **审计日志**：读路径/Token 使用/删除操作写结构化审计事件（不记 PII/token 原值）。
- **输入消毒**：上传文件类型/魔数/大小校验已有；补 ClamAV 占位接口 + 拒绝加密/压缩炸弹（沙箱可用假阳性安全默认值）。
- **安全响应头**：CSP / HSTS / X-Content-Type-Options / Referrer-Policy（仅 HTTPS 域）。
- **LLM 输出消毒**：现有已做基本处理，补 XSS 转义断言单测。

### W5 — 无障碍 / 60fps
- 关键页面（上传 / 结果 / 面试）走 a11y 清单：语义标签、焦点管理、ARIA、对比度、键盘可达。
- 动画用 `transform/opacity` + `prefers-reduced-motion` 兜底，目标 60fps。
- Lighthouse a11y ≥ 90 作为验收（沙箱可跑 `next build` + 静态审计，活跑 Lighthouse 记录为手动项）。

### W6 — 部署文档 / 回滚 / 备份
- `compose.prod.yml`（postgres+pgvector / redis / api / web / worker，profile 启用 minio/ocr）。
- `scripts/backup_db.sh` + `scripts/restore_db.sh`（pg_dump + 加密归档）。
- `scripts/rollback.sh`（按 git tag / 镜像 digest 回滚）。
- `docs/DEPLOY.md` runbook（首次部署、密钥、迁移、回滚、备份恢复）。

---

## 4. 关键设计决策（待 §10 确认后定稿）

| 决策 | 候选 | 推荐 |
|---|---|---|
| D1 鉴权形态 | A 完整账号 / B 轻量 same-site / C 不做 | **A**（需确认） |
| D2 可观测栈 | A 代码+compose profile / B 仅 Sentry / C 延后 | **A**（沙箱不跑活实例） |
| D3 配额存储 | Redis 计数（复用现有） | Redis（确认 redis 可用） |
| D4 本轮范围 | 6 条全做 / 子集 | 见 §10-Q3 |

---

## 5. 影响范围 / 可能破坏

- **W1 改动最大**：新增 `users` 表 + migration + 认证依赖 + 前端登录态；可能影响现有匿名流（需兼容或迁移策略）。
- **W2/W3/W4** 多为**增量**（新 middleware、新配置、新表），不改动既有链路；但 W3 的 middleware 顺序、W4 的响应头需小心不破坏 SSE（`text/event-stream`）。
- **W5** 纯前端增量。
- **W6** 纯新增文件/文档。
- **生产 migration**：M0–M3 一直用 `Base.metadata.create_all`（dev）。M4 必须补**正式 Alembic migration**（含 `users` 表、审计表、配额表），这是生产上线的硬门槛。

---

## 6. 验证（Verification，草案）

- **单测**：配额拒绝、审计事件写入、消毒转义、Sentry 静默（无 DSN）、BFF cookie 标志。
- **全量回归**：M0–M3 全部测试（44 passed / 2 skipped 基线）**零回退**。
- **`next build`**：前端改动编译通过 + a11y 静态检查。
- **compose 校验**：`compose.prod.yml` / `compose.observ.yml` `config` 通过（不强制起活实例）。
- **手动**：活跑 Lighthouse / Grafana 记为文档项（沙箱限制）。

---

## 7. 下一步

1. **§10 四个分叉须由你确认**（尤其 W1 鉴权形态、W3 可观测栈、本轮范围）。
2. 确认后定稿本计划 → 按工作流分批实施（每完成一条即单测 + 局部回归 + 提交 develop，不推远程）。
3. 交付后由你做「M4 对抗性审查 → 修复」闭环。

---

## 8. 分阶段编排（已锁定范围）

确认范围：W1 完整账号 + W2 配额 + W3 可观测 + W4 安全审计 + W5 无障碍/60fps + W6 部署/回滚/备份 + **Alembic 迁移**。按依赖排序：

| 阶段 | 内容 | 交付 | 验证 |
|---|---|---|---|
| **P0 迁移地基** | 引入 Alembic；初始 migration = 当前全部表（手写，不依赖活 DB）；env.py 接 `DATABASE_URL` + metadata | Alembic 配置 + 初始迁移 | `alembic upgrade head --sql` 离线产出合法 SQL |
| **P1 W1 账号核心** | `users` 表 + migration；`auth` router（register/login/logout/me）；argon2 密码哈希；httpOnly SameSite 会话 cookie（itsdangerous 签名）；BFF/CORS 说明 | 可注册登录、cookie 会话 | 单测：注册/登录/错误密码/me/登出；`next build` |
| **P2 W2 配额限流** | per-token/per-user 配额中间件（Redis 计数）；超限 429；`QUOTA_*` 配置化 | 配额生效 | 单测：超额拒绝、窗口重置 |
| **P3 W3 可观测** | FastAPI OTel middleware（trace/metrics）；Sentry 错误上报（DSN 配置化）；`compose.observ.yml` + 示例 dashboard | 埋点 + compose profile | 单测：Sentry 无 DSN 静默；`docker compose -f compose.observ.yml config` 通过 |
| **P4 W4 安全审计** | 审计日志（读路径/删除/Token 使用，不记 PII/token 原值）；输入消毒加固 + XSS 转义断言；安全响应头 middleware；ClamAV 占位接口（安全默认拒绝加密包） | 审计 + 头 + 消毒 | 单测：审计事件写入、响应头、转义 |
| **P5 W5 无障碍/60fps** | 关键页面 a11y 清单（语义/焦点/ARIA/对比度/键盘）；动画 `transform/opacity` + `prefers-reduced-motion` | 前端达标 | `next build` + 静态 a11y 检查；Lighthouse 记为手动 |
| **P6 W6 部署/回滚/备份** | `compose.prod.yml`；`scripts/backup_db.sh`/`restore_db.sh`/`rollback.sh`；`docs/DEPLOY.md` runbook | 文档 + 脚本 | `compose.prod.yml config` 通过；脚本语法检查 |

每阶段完成即单测 + 局部回归 + 提交 `develop`（**不推远程**），全里程碑结束做「M4 对抗性审查 → 修复」闭环。

---

## 9. W1 共存设计（非破坏性，已定）

现有 M0–M3 的**匿名 token 流（resumes / interviews / interview_sessions）保持不动**——零登录摩擦。账号层为**可选叠加**：

- 新增账号相关端点（`/api/auth/*`、`/api/account/*`）走 cookie 会话认证；
- 既有匿名端点**不强制登录**（无 breaking change）；
- `resumes.user_id` 已可空，后续可选择性把匿名简历关联到登录用户（W1 不做关联，仅建账号能力）；
- 生产推荐 BFF 反向代理使前端与 API 同站，cookie `SameSite=Lax` 即可；dev 跨域用 `SameSite=None; Secure` + CORS `allow_credentials`，文档说明。

---

## 10. 已确认决策（2026-07-13）

- **Q1（W1）**：完整用户账号体系（users 表 + 注册/登录 + 会话 cookie/BFF）。
- **Q2（W3）**：代码埋点 + Sentry + `compose.observ.yml`（沙箱不跑活实例）。
- **Q3（范围）**：W2 + W4 + W5 + W6 全做。
- **Q4（迁移）**：引入 Alembic（生产硬门槛）。
