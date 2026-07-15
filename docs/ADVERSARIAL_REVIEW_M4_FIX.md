# M4 对抗性审查 — 修复记录

> 对应 `ADVERSARIAL_REVIEW_M4.md`。审查发现 0 P0 / 0 P1，3 P2 / 4 P3。
> 本报告记录修复项、验证方式与未处理项（部署/后续）。

## 修复项映射

| 发现 | 级别 | 修复 | 文件 |
|---|---|---|---|
| worker 未接入可观测性 | P2-1 | `on_startup` 调 `setup_observability(settings)` | `app/workers/worker.py` |
| OTel 全局 tracer 测试竞争 | 测试脚手架 | `setup_observability` 加 `force` 参数 + 幂等（已初始化则跳过），测试 fixture 用 `force=True` 装 in-memory exporter | `app/core/observability.py`、`tests/test_observability.py` |

### P2-1 详情
arq worker 不跑 FastAPI lifespan，原 `main.py` 的 `setup_observability` 不在 worker
生效 → 生产 worker 异常不上报 Sentry / 不产 span。修复后在 `on_startup`（worker 启动钩子）
初始化，与 API 层一致。`setup_observability` 无配置时静默，不影响 worker 启动。

### 测试脚手架详情
多个测试调用 `setup_observability`，每次重写模块级全局 `tracer`，导致 session fixture
安装的 in-memory exporter 被后续调用覆盖（单独跑一个测试通过、整跑失败）。
修复：`setup_observability(force=False)` 幂等（tracer 已非 None 则跳过），测试 fixture
用 `force=True` 强制安装 in-memory。顺带让真实运行更健壮（热重载/重复调用不相互污染）。

## 验证方式

| 项 | 命令 / 动作 | 结果 |
|---|---|---|
| 后端全量 | `pytest tests/ -q` | **73 passed / 2 skipped**（无回归） |
| 前端构建 | `next build`（apps/web） | 通过（5 路由，类型/Lint 零错误） |
| 可观测 SSE | `test_observability` 断言 SSE 透传 + X-Trace-ID + span 属性 | 8 passed |
| 安全审计 | `test_security` 断言响应头 / 扫描 / 消毒 / 审计脱敏 | 13 passed |
| 部署脚本 | `bash -n scripts/*.sh` | 全部 OK |
| 审计接入 | register/login/logout/upload/delete 路由调 `audit_event`（subject 脱敏） | 集成验证 + 全量测试通过 |

## 未处理项（非阻断，已标注）

| 发现 | 处置 |
|---|---|
| P2-2 compose 部署机 `config` 复核 | 已在 `docs/DEPLOY.md` + compose 文件顶部标注，部署前必做 |
| P2-3 读路径审计扩展 | helper 就绪，按需补 `audit_event` 到只读路由 |
| P3-1 `APP_VERSION` 硬编码 | 可选统一来源 |
| P3-2 dev Console 噪音 | 特性（本地可见），生产静默 |
| P3-3 ClamAV 真实分支 | 后续接 `clamd` 时加依赖 + 实现 |
| P3-4 CSP 收紧（nonce） | 可选，`CSP_REPORT_ONLY=False` 切换 |

## 结论

M4 无 P0/P1 阻断项，全部 P3–P6 实现 + 收尾合并 + 对抗审查已闭环。P2-1 已修复，
其余为部署/后续事项，不阻塞里程碑交付。三提交均落本地 `develop`（未推远程）。
