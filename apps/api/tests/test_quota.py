"""配额限流中间件测试（M4 W2）。

用内存假 QuotaEnforcer 替换 app.main.get_quota_enforcer，验证：
- 窗口内未超额：非 429；
- 超过 QUOTA_LIMIT：返回 429 + QUOTA_EXCEEDED。
Redis 不可用降级放行由 core/quota 单元覆盖（此处只验证中间件接线与拒绝语义）。
"""
from __future__ import annotations

from starlette.testclient import TestClient

from app.main import app


class _FakeQuota:
    def __init__(self, limit: int):
        self.limit = limit
        self.n = 0

    async def allow(self, subject: str) -> bool:
        # 全局计数（忽略 subject 漂移），只验证窗口内/超窗口的拒绝语义
        self.n += 1
        return self.n <= self.limit


def test_quota_allows_within_limit(monkeypatch):
    fake = _FakeQuota(3)
    monkeypatch.setattr("app.main.get_quota_enforcer", lambda: fake)
    client = TestClient(app)
    for _ in range(3):
        r = client.get("/api/jobs")
        assert r.status_code != 429


def test_quota_rejects_after_limit(monkeypatch):
    fake = _FakeQuota(3)
    monkeypatch.setattr("app.main.get_quota_enforcer", lambda: fake)
    client = TestClient(app)
    for _ in range(3):
        client.get("/api/jobs")
    r = client.get("/api/jobs")
    assert r.status_code == 429, r.text
    assert r.json()["title"] == "QUOTA_EXCEEDED"
