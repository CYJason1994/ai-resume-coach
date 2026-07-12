"""岗位源摄入触发（R2-C1 可配置）。dev 端点，通过 ARQ 后台任务摄入。

v0.3 决策「ARQ 锁死」：禁用 BackgroundTasks，岗位摄入改为入队 ARQ 任务，
避免阻塞请求且不丢失任务（worker 重启可恢复）。亦可直接跑 scripts/seed_jobs.py 同步摄入。
"""
from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.schemas import SeedResponse
from app.workers.tasks import enqueue_seed_jobs

settings = get_settings()
logger = get_logger("jobs")
router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/seed", response_model=SeedResponse)
async def seed_jobs():
    """摄入岗位源（O*NET / json，可配置），经 ARQ 后台执行。"""
    await enqueue_seed_jobs()
    return SeedResponse(
        provider=settings.JOB_SOURCE,
        ingested=0,
        skipped=0,
        errors=0,
        message="岗位摄入任务已入队（ARQ 后台执行，查看 worker 日志 / 稍后查询）",
    )
