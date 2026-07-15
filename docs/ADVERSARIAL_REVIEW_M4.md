# M4 对抗性审查报告（生产加固）

> 审查方式：逐行读码（P3/P4/P5/P6 四个子 agent 产出 + 主 agent 合并补丁）+ 运行时复验
> （`pytest 73 passed/2 skipped`、`next build` 通过、共享文件合并后集成验证）。
> 范围：M4 剩余阶段 P3 可观测 / P4 安全审计 / P5 无障碍 / P6 部署 + 收尾合并。

## TL;DR

M4 生产加固 **P3–P6 全部落地并验证通过**。对抗审查发现 **0 个 P0、0 个 P1**，
**3 个 P2、4 个 P3**。其中 P2-1（worker 未接入可观测性——生产盲区）已在收尾阶段
修复闭环；P2-2/P2-3 与全部 P3 为部署机复核 / 范围取舍 / 后续增强项，非代码阻断。

核心加固点均经测试或结构验证：可观测 SSE 不破坏、审计脱敏绝不落 PII、安全头
Report-Only 不阻断、扫描 fail-closed、配额/审计/可观测与匿名流正交。

---

## P2（中优先级，建议处理）

### P2-1 worker 进程未接入可观测性【已修复】
- **现象**：`app/workers/worker.py` 的 arq worker 不跑 FastAPI `lifespan`，
  `main.py` 里的 `setup_observability(settings)` 从未在 worker 进程执行。
- **影响**：生产环境 worker 任务异常**不会上报 Sentry**、不产出 OTel span，
  形成可观测性盲区（API 层有、worker 层无）。
- **修复**：在 `on_startup` 调用 `setup_observability(settings)`，与 API 层一致。
  已落地并验证（pytest 73 passed 无回归）。

### P2-2 compose 文件未在部署机 `docker compose config` 复核
- **现象**：沙箱无 Docker，`compose.prod.yml` / `compose.observ.yml` 仅做了
  YAML 结构 + 健康检查语法校验，未跑 `docker compose -f <file> config`。
- **影响**：服务拓扑 / 依赖条件 / healthcheck 在生产机的真实解析未最终确认。
- **处置**：已在 `docs/DEPLOY.md` 与 compose 文件顶部标注「需在装有 Docker
  Compose v2.20+ 的机器执行 `docker compose config` 做最终确认」。非代码缺陷，
  属部署前必做步骤。

### P2-3 审计仅覆盖写路径，读路径未接入
- **现象**：`audit_event` 已接入 register / login / logout / upload / delete_resume
  五个写路径，但读路径（jobs 列表、result 查询等）未审计。
- **影响**：审计事件不完整（缺少读访问轨迹）。但读路径量大、PII 风险低于写/删。
- **处置**：范围取舍——高危写/删优先，读路径按需扩展。若合规要求全审计，
  后续在只读路由补 `audit_event`（helper 已就绪，零成本接入）。

---

## P3（低优先级，可选增强）

### P3-1 `observability.APP_VERSION` 硬编码
- `app/core/observability.py` 顶部的 `APP_VERSION = "0.3.0"` 与 `pyproject.toml`
  `version` 重复，存在漂移风险。非阻断；建议统一来源（如从 pyproject 读或常量集中）。

### P3-2 dev 环境 ConsoleSpanExporter 控制台噪音
- `ENVIRONMENT=development` 且无 OTLP endpoint 时，observability 用
  `ConsoleSpanExporter` 每个请求打印 span JSON 到 stdout（本地可见特性）。
  生产（`production` 且无 OTLP）走静默分支。可接受，标注供知悉。

### P3-3 ClamAV 真实分支为占位
- `app/core/scan.py` 的 `CLAMAV_ENABLED=True` 分支（`_scan_with_clamd`）为 TODO +
  安全默认拒绝，未引用 `clamd` 包。后续接真实 ClamAV 时需加依赖并实现。

### P3-4 安全 CSP 默认宽松 + Report-Only
- `security_headers_middleware` 默认 `script-src 'self'`、`style-src` 含
  `'unsafe-inline'`、且 CSP 走 **Report-Only**（只上报不阻断），是安全灰度设计。
  生产如需收紧，可改 nonce 方案并通过 `CSP_REPORT_ONLY=False` 切换。

---

## 做到位的地方（正面确认）

- **可观测性完全优雅**：Sentry DSN / OTLP endpoint 均空时静默、不抛、不联网；
  `observability_middleware` 只读写 headers、**绝不消费/缓冲 SSE body**（测试断言
  SSE 完整透传 + `X-Trace-ID`）。`setup_observability` 幂等（重复调用不相互污染，
  修复了测试 fixture 的全局 tracer 竞争）。
- **审计脱敏可靠**：`audit_event` 对 subject（token/user 仅哈希前缀）与 detail
  敏感 key（token/password/email/ip…）强制脱敏，测试断言日志文本不含原值/PII。
- **安全头不破坏前端**：默认 Report-Only + 宽松 CSP，HSTS 仅生产 HTTPS 域下发。
- **扫描 fail-closed**：压缩炸弹 / 高熵加密样本默认 `safe=False`，绝不放行。
- **正交性**：配额 / 审计 / 可观测与既有匿名 token 流、per-IP 限流、LLM 信号量(6)
  完全正交，零破坏性改动。
- **前端 a11y**：落实 WCAG 2.1 AA 多项（语义结构、label 关联、focus-visible、
  `role=alert`/`role=log`、键盘可达）+ `prefers-reduced-motion` 全局兜底；
  `next build` 通过。
- **部署就绪**：`compose.prod.yml` 含 migrate 串行（避免迁移竞态）、健康检查、
  `restart: unless-stopped`；`scripts/*.sh` 通过 `bash -n`；`docs/DEPLOY.md`
  runbook 完整（密钥/迁移/回滚/备份/监控/排障）。

---

## 优先级表

| 级别 | 项 | 状态 |
|---|---|---|
| P2-1 | worker 接入可观测性 | ✅ 已修复 |
| P2-2 | compose 部署机 `config` 复核 | ⏳ 部署前必做（已标注） |
| P2-3 | 读路径审计扩展 | ⏳ 按需（helper 就绪） |
| P3-1 | APP_VERSION 统一来源 | ⏳ 可选 |
| P3-2 | dev Console 噪音 | ⏳ 知悉（特性） |
| P3-3 | ClamAV 真实分支 | ⏳ 后续 |
| P3-4 | CSP 收紧（nonce） | ⏳ 可选 |

**结论**：M4 无 P0/P1 阻断项，生产加固可进入收尾提交。P2-1 已闭环；P2-2/P2-3
与 P3 为部署/后续事项，不阻塞本里程碑交付。
