"""配额限流（M4 W2）：per-subject 固定窗口计数，Redis 支撑，失败优雅降级。

- subject：登录用户（会话）→ 匿名 access_token → 兜底客户端 IP；令牌一律 sha256
  后取前缀，绝不记录原始令牌/IP 明文到 Redis key 之外。
- 与既有 per-IP 上传限流、LLM 信号量(6) 正交，不改动其逻辑。
- Redis 不可达时降级放行（记录告警），避免限流组件本身造成可用性事故。
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Protocol

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger

settings = get_settings()
logger = get_logger("quota")


# ── P2-15：全局配额天花板（先于 per-subject 检查）──
# 攻击者即使持有多个令牌，也只能共享同一个全局窗口，无法无限放大。
# 默认 = QUOTA_LIMIT * 4；可用环境变量 QUOTA_GLOBAL_LIMIT 覆盖。
QUOTA_GLOBAL_LIMIT = int(os.environ.get("QUOTA_GLOBAL_LIMIT") or settings.QUOTA_LIMIT * 4)

# 仅当显式信任反向代理时才读取 X-Forwarded-For（避免客户端伪造 XFF 伪造成本极低的
# 独立 IP 桶绕过配额/审计）。默认不信任，直接使用 TCP 层 request.client.host。
# 部署在受信反代之后可设 REAL_IP_FROM_PROXY=1/true/yes 开启。
_TRUST_PROXY = os.environ.get("REAL_IP_FROM_PROXY", "").strip().lower() in {"1", "true", "yes"}


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


# ── 全局配额天花板：Redis 计数，失败优雅降级放行（同 per-subject 模式）──

class _AllowGlobal:
    """Redis 不可用时的降级：全局放行（fail-open）。"""

    async def allow_global(self) -> bool:
        return True


class RedisGlobalEnforcer:
    def __init__(self, client: aioredis.Redis, limit: int, window: int) -> None:
        self._client = client
        self._limit = limit
        self._window = window

    async def allow_global(self) -> bool:
        try:
            key = f"quota:global:{int(time.time()) // self._window}"
            n = await self._client.incr(key)
            if n == 1:
                await self._client.expire(key, self._window)
            return n <= self._limit
        except Exception as e:  # noqa: BLE001 — Redis 故障降级放行 + 熔断
            logger.warning(
                "quota_global_redis_error",
                error=str(e),
                msg="全局配额 Redis 不可用，熔断降级为放行",
            )
            _downgrade_global_to_allow()
            return True


_global_enforcer = None


def _downgrade_global_to_allow() -> None:
    """全局 Redis 首次失败后熔断为放行，避免反复打挂掉的 Redis 拖垮可用性。"""
    global _global_enforcer
    _global_enforcer = _AllowGlobal()


def get_global_enforcer():
    """单例：优先 Redis 全局计数；连接/库异常则 Noop 放行（fail-open）。"""
    global _global_enforcer
    if _global_enforcer is None:
        try:
            client = aioredis.Redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
            _global_enforcer = RedisGlobalEnforcer(
                client, QUOTA_GLOBAL_LIMIT, settings.QUOTA_WINDOW_SECONDS
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("quota_global_init_failed", error=str(e), msg="使用全局 Noop（不限制）")
            _global_enforcer = _AllowGlobal()
    return _global_enforcer


def client_host_from_request(request) -> str | None:
    """推导客户端真实 IP（安全假设见 _TRUST_PROXY）。

    默认不信任 X-Forwarded-For，直接使用 TCP 层 request.client.host，避免客户端伪造
    XFF 伪造成本极低的独立 IP 桶绕过 per-subject 配额。仅当显式设置 REAL_IP_FROM_PROXY
    时才采用 XFF 最左（首个）地址作为真实客户端 IP。
    """
    host = request.client.host if getattr(request, "client", None) else None
    if _TRUST_PROXY:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            host = xff.split(",")[0].strip()  # 取最左（原始客户端）地址
    return host


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
