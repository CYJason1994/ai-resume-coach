"""pytest 配置：在 app 模块导入前设置仓库相关的绝对路径，保证测试可复现。

必须在任何 `from app... import` 之前设置，否则 get_settings() 的 lru_cache 会锁定默认相对路径。
"""
from __future__ import annotations

import os
import pathlib

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[3]  # apps/api/tests -> repo root

os.environ.setdefault("CURATED_JOBS_DIR", str(_REPO / "data" / "jobs" / "curated"))
os.environ.setdefault("ZH_OVERLAY_PATH", str(_REPO / "data" / "jobs" / "zh_overlay.json"))
os.environ.setdefault("ONET_SNAPSHOT_PATH", str(_REPO / "data" / "jobs" / "onet" / "snapshot.json"))


@pytest.fixture(autouse=True)
def _fast_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试默认不依赖外部 Redis：配额降级为 Noop（除非用例自行 monkeypatch 覆盖）。

    生产有 Redis 时 incr ~1ms；测试环境无 Redis 时异步连接会挂起（socket 超时
    对 redis.asyncio 连接不生效），故默认 Noop 提速且隔离。
    """

    class _NoopQuota:
        async def allow(self, subject: str) -> bool:
            return True

    monkeypatch.setattr("app.main.get_quota_enforcer", lambda: _NoopQuota())

