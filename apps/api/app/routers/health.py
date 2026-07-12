"""健康检查：/health（存活） + /ready（就绪，含 DB/Redis/LLM 探活）。"""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import engine
from app.core.llm import get_llm
from app.core.logging import get_logger

logger = get_logger("health")
router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    return {"status": "ok", "service": "api"}


@router.get("/ready")
async def ready():
    checks: dict[str, str] = {}

    # DB
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["database"] = f"fail:{e}"

    # Redis
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(get_settings().REDIS_URL)
        await r.ping()
        checks["redis"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["redis"] = f"fail:{e}"

    # LLM 探活（不阻塞就绪判定，失败仅记录）
    try:
        ok = await get_llm().health_check()
        checks["llm"] = "ok" if ok else "degraded"
    except Exception as e:  # noqa: BLE001
        checks["llm"] = f"fail:{e}"

    ok = all(v == "ok" for v in checks.values() if v != "degraded") and "database" in checks and checks["database"] == "ok"
    status = 200 if (checks.get("database") == "ok") else 503
    return {"status": "ok" if status == 200 else "degraded", "checks": checks}
