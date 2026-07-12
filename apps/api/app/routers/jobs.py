"""岗位源摄入触发（R2-C1 可配置）。dev 端点，触发 JobSourceProvider 摄入。"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.schemas import SeedResponse
from app.services.job_source import get_job_source

settings = get_settings()
logger = get_logger("jobs")
router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/seed", response_model=SeedResponse)
async def seed_jobs(background: BackgroundTasks):
    """摄入岗位源（O*NET / json，可配置）。

    生产建议改为 ARQ 后台任务；M0 先同步+后台二选一，这里用后台任务避免阻塞。
    """
    provider = get_job_source()

    def _run():
        result = provider.seed()
        logger.info("jobs_seeded", **result)

    background.add_task(_run)
    return SeedResponse(
        provider=settings.JOB_SOURCE,
        ingested=0,
        skipped=0,
        errors=0,
        message="岗位摄入已在后台启动（查看日志 / 稍后查询）",
    )
