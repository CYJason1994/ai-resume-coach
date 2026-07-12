"""ARQ 后台任务（M0 起唯一异步方案，禁用 BackgroundTasks）。

M0：process_resume_task 为占位状态机（running → done）。
M1：接入 解析 → 结构化 → 匹配（含 §7.8 降级与 §9 成本护栏）。
"""
from __future__ import annotations

import uuid

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.models.models import Resume, Task

settings = get_settings()
logger = get_logger("worker.tasks")

_redis = None


async def _redis_pool():
    global _redis
    if _redis is None:
        _redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    return _redis


async def enqueue_process_resume(resume_id: uuid.UUID) -> None:
    redis = await _redis_pool()
    await redis.enqueue_job("process_resume_task", str(resume_id))


async def enqueue_seed_jobs() -> None:
    redis = await _redis_pool()
    await redis.enqueue_job("seed_jobs_task")


async def process_resume_task(ctx: dict, resume_id: str) -> None:
    logger.info("process_start", resume_id=resume_id)
    async with SessionLocal() as session:
        task = await session.scalar(
            select(Task).where(
                Task.resume_id == uuid.UUID(resume_id), Task.type == "process_resume"
            )
        )
        if not task:
            logger.warning("process_no_task", resume_id=resume_id)
            return
        task.status = "running"
        task.progress = 10
        await session.commit()

        # TODO(M1): 解析 → 结构化(白名单上送) → 匹配(向量+LLM, 降级)
        resume = await session.get(Resume, uuid.UUID(resume_id))
        logger.info("process_placeholder", file_type=resume.file_type if resume else None)

        task.status = "done"
        task.progress = 100
        await session.commit()
    logger.info("process_done", resume_id=resume_id)


async def seed_jobs_task(ctx: dict) -> None:
    """摄入岗位源（O*NET / json，可配置）。失败不影响 API，仅记日志。"""
    from app.services.job_source import get_job_source

    logger.info("seed_start")
    try:
        result = await get_job_source().seed()
        logger.info("seed_done", **result)
    except Exception as e:  # noqa: BLE001
        logger.error("seed_failed", error=str(e))
