"""P4 遗留1 收口：成本计数多副本化（Redis 权威账本 + 本地回退）。

验证：
- 无 REDIS_URL 时纯本地（单副本）预算判定仍正确；
- 配置了 Redis 客户端时，预算判定读 Redis 权威值（而非本地快照）；
- _track_cost 同步写入 Redis（日 + 单份）；
- Redis 读取故障 → 客户端熔断 _down → 回退本地（fail-open）。
"""
from __future__ import annotations

from datetime import date

import pytest

from app.core.llm import LlmProvider, _RedisCostClient


class _FakeRedis:
    """内存版 Redis，实现 incrbyfloat / expire / get（模拟 aioredis 异步接口）。"""

    def __init__(self) -> None:
        self.data: dict[str, float] = {}
        self.expire_calls = 0
        self.fail_get = False

    async def incrbyfloat(self, key: str, amount: float) -> float:
        self.data[key] = self.data.get(key, 0.0) + amount
        return self.data[key]

    async def expire(self, key: str, ttl: int) -> bool:
        self.expire_calls += 1
        return True

    async def get(self, key: str) -> "float | None":
        if self.fail_get:
            raise RuntimeError("redis down")
        return self.data.get(key)


def _provider_with(fake) -> LlmProvider:
    p = LlmProvider()
    p._redis_cost = _RedisCostClient(fake) if fake is not None else None
    return p


def _today_key() -> str:
    return f"cost:daily:{date.today().isoformat()}"


@pytest.mark.asyncio
async def test_local_only_when_no_redis():
    p = _provider_with(None)
    assert p._redis_cost is None
    assert await p._budget_ok() is True
    assert await p._resume_budget_ok("ck") is True


@pytest.mark.asyncio
async def test_redis_authoritative_overrides_local():
    fake = _FakeRedis()
    p = _provider_with(fake)
    # 本地 dict 为空，但 Redis 显示日预算已爆 → 应判不 ok（读 Redis 权威值）
    fake.data[_today_key()] = 999.0
    assert await p._budget_ok() is False
    # 单份超限
    fake.data["cost:resume:ck"] = 1.0
    assert await p._resume_budget_ok("ck") is False


@pytest.mark.asyncio
async def test_track_cost_writes_redis():
    fake = _FakeRedis()
    p = _provider_with(fake)
    await p._track_cost("chat", 1000, "ck")
    assert fake.data[_today_key()] == pytest.approx(0.00006)
    assert fake.data["cost:resume:ck"] == pytest.approx(0.00006)
    assert fake.expire_calls == 2


@pytest.mark.asyncio
async def test_redis_down_falls_back_local():
    fake = _FakeRedis()
    fake.fail_get = True
    p = _provider_with(fake)
    # get 抛错 → 客户端 _down，daily() 返回 None → 回退本地（空 → ok）
    assert await p._budget_ok() is True
    assert p._redis_cost._down is True
