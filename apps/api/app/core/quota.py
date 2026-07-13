"""配额限流（M4 W2）：per-subject 固定窗口计数，Redis 支撑，失败优雅降级。

- subject：登录用户（会话）→ 匿名 access_token → 兜底客户端 IP；令牌一律 sha256
  后取前缀，绝不记录原始令牌/IP 明文到 Redis key 之外。
- 与既有 per-IP 上传限流、LLM 信号量(6) 正交，不改动其逻辑。
- Redis 不可达时降级放行（记录告警），避免限流组件本身造成可用性事故。
"""
from __future__ import annotations

import hashlib
import time
from typing import Protocol

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger

settings = get_settings()
logger = get_logger("quota")


class QuotaEnforcer(Protocol):
    async def allow(self, subject: str) -> bool:
        """返回 True 表示放行，False 表示超额拒绝。"""
        ...


class RedisQuotaEnforcer:
    def __init__(self, client: aioredis.Redis, limit: int, window: int) -> None:
        self._client = client
        self._limit = limit
        self._window = window

    async def allow(self, subject: str) -> bool:
        try:
            key = f"quota:{subject}:{int(time.time()) // self._window}"
            n = await self._client.incr(key)
            if n == 1:
                await self._client.expire(key, self._window)
            return n <= self._limit
        except Exception as e:  # noqa: BLE001 — Redis 故障降级放行 + 熔断
            logger.warning("quota_redis_error", error=str(e), msg="配额 Redis 不可用，熔断降级为 Noop")
            _downgrade_to_noop()
            return True


class NoopQuotaEnforcer:
    async def allow(self, subject: str) -> bool:
        return True


_enforcer: QuotaEnforcer | None = None


def _downgrade_to_noop() -> None:
    """Redis 首次失败后熔断为 Noop，避免反复打挂掉的 Redis 拖垮可用性。"""
    global _enforcer
    _enforcer = NoopQuotaEnforcer()


def get_quota_enforcer() -> QuotaEnforcer:
    """单例：优先 Redis，连接/库异常则 Noop 降级；运行时 Redis 故障会熔断为 Noop。"""
    global _enforcer
    if _enforcer is None:
        try:
            client = aioredis.Redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
            _enforcer = RedisQuotaEnforcer(client, settings.QUOTA_LIMIT, settings.QUOTA_WINDOW_SECONDS)
        except Exception as e:  # noqa: BLE001
            logger.warning("quota_init_failed", error=str(e), msg="使用 Noop 配额（不限制）")
            _enforcer = NoopQuotaEnforcer()
    return _enforcer


def quota_subject(token: str | None, client_host: str | None) -> str:
    """从请求推导配额主体（令牌/用户优先，兜底 IP）。"""
    if token:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
        return f"tok:{digest}"
    return f"ip:{client_host or 'unknown'}"
